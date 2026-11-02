import json
import os
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import screen_viewer as sv  # noqa: E402

CORE_HEADER = (
    Path(__file__).resolve().parents[2]
    / "firmware/main/boards/elena-stackchan/screen_stream_core.h"
)

try:
    from PIL import Image
except ImportError:
    Image = None


def chunk(session, hash_, area, codec, total, offset, payload):
    # The same layout the firmware's host tests pin byte by byte.
    header = b"ES\x10\x01" + struct.pack("<II4HBxII", session, hash_, *area, codec, total, offset)
    return header + payload


def map_packet(session, sequence, time_ms, entries):
    body = struct.pack("<IIH", sequence, time_ms, len(entries))
    for area, hash_ in entries:
        body += struct.pack("<4HI", *area, hash_)
    return b"ES\x11\x01" + struct.pack("<I", session) + body


def stats_packet(session, values):
    return (
        b"ES\x12\x01"
        + struct.pack("<IH", session, len(values))
        + struct.pack(f"<{len(values)}I", *values)
    )


def stats_values(**overrides):
    values = dict.fromkeys(sv.STAT_NAMES, 0)
    values.update(overrides)
    return [values[name] for name in sv.STAT_NAMES]


class PacketTests(unittest.TestCase):
    def test_hello_matches_firmware_format(self):
        packet = sv.hello_packet(9, sv.MODE_FRAMES, 40, 2000)
        self.assertEqual(packet, bytes(b"ES\x01\x01\x09\0\0\0\x01\x28\xd0\x07"))

    def test_ack_matches_firmware_format(self):
        packet = sv.ack_packet(9, [1, 2])
        self.assertEqual(packet, bytes(b"ES\x02\x01\x09\0\0\0\x02\0\x01\0\0\0\x02\0\0\0"))

    def test_parses_chunk_bytes_pinned_by_firmware_test(self):
        header = bytes.fromhex(
            "4553" "10" "01" "04030201" "ddccbbaa" "0100" "0200" "0300" "0400" "01" "00"
            "00100000" "20000000"
        )
        parsed = sv.parse_packet(header + b"xy")
        self.assertEqual(parsed.kind, "chunk")
        self.assertEqual(parsed.session, 0x01020304)
        self.assertEqual(parsed.hash, 0xAABBCCDD)
        self.assertEqual(parsed.area, (1, 2, 3, 4))
        self.assertEqual(parsed.codec, sv.CODEC_JPEG)
        self.assertEqual(parsed.total, 0x1000)
        self.assertEqual(parsed.offset, 0x20)
        self.assertEqual(parsed.payload, b"xy")

    def test_parses_map_bytes_pinned_by_firmware_test(self):
        data = bytes.fromhex(
            "4553" "11" "01" "05000000" "06000000" "07000000" "0100"
            "0000" "0000" "4001" "1400" "44332211"
        )
        parsed = sv.parse_packet(data)
        self.assertEqual(parsed.kind, "map")
        self.assertEqual(parsed.sequence, 6)
        self.assertEqual(parsed.time_ms, 7)
        self.assertEqual(parsed.entries, [((0, 0, 320, 20), 0x11223344)])

    def test_rejects_malformed_packets(self):
        self.assertIsNone(sv.parse_packet(b""))
        self.assertIsNone(sv.parse_packet(b"XX\x10\x01aaaaaaaa"))
        self.assertIsNone(sv.parse_packet(b"ES\x10\x01short"))
        self.assertIsNone(sv.parse_packet(b"ES\x11\x01" + struct.pack("<IIIH", 1, 1, 1, 5)))
        self.assertIsNone(sv.parse_packet(b"ES\x99\x01aaaaaaaa"))

    def test_stat_names_match_firmware_order(self):
        header = CORE_HEADER.read_text(encoding="utf-8")
        body = header[header.index("enum Stat"):]
        body = body[: body.index("};")]
        names = re.findall(r"kStat(\w+),", body)
        self.assertNotIn("Count", sv.STAT_NAMES)
        firmware = [re.sub(r"(?<!^)([A-Z])", r"_\1", n).lower() for n in names if n != "Count"]
        self.assertEqual(list(sv.STAT_NAMES), firmware)


class StreamStateTests(unittest.TestCase):
    AREA = (0, 0, 320, 20)

    def setUp(self):
        self.state = sv.StreamState(session=9)

    def feed(self, data, now=0.0):
        return self.state.handle(data, now)

    def test_acks_when_all_chunks_arrive_in_any_order(self):
        self.assertEqual(self.feed(chunk(9, 5, self.AREA, 1, 4, 2, b"cd")), [])
        self.assertEqual(self.feed(chunk(9, 5, self.AREA, 1, 4, 0, b"ab")), [5])
        self.assertEqual(self.state.strips[5].data, b"abcd")
        self.assertEqual(self.state.strips[5].codec, sv.CODEC_JPEG)

    def test_acks_a_strip_it_already_has_because_the_ack_was_lost(self):
        self.feed(chunk(9, 5, self.AREA, 1, 2, 0, b"ab"))
        self.assertEqual(self.feed(chunk(9, 5, self.AREA, 1, 2, 0, b"ab")), [5])

    def test_ignores_other_sessions(self):
        self.assertEqual(self.feed(chunk(8, 5, self.AREA, 1, 2, 0, b"ab")), [])
        self.assertNotIn(5, self.state.strips)
        self.feed(map_packet(8, 1, 1, [(self.AREA, 5)]))
        self.assertIsNone(self.state.current_map)

    def test_ignores_chunks_outside_the_declared_size(self):
        self.assertEqual(self.feed(chunk(9, 5, self.AREA, 1, 2, 1, b"ab")), [])
        self.assertNotIn(5, self.state.strips)

    def test_keeps_only_the_newest_map(self):
        self.feed(map_packet(9, 2, 20, [(self.AREA, 5)]))
        self.feed(map_packet(9, 1, 10, [(self.AREA, 6)]))
        self.assertEqual(self.state.current_map.sequence, 2)

    def test_accepts_a_lower_sequence_when_the_robot_restarted_counting(self):
        self.feed(map_packet(9, 100, 1000, [(self.AREA, 5)]))
        self.feed(map_packet(9, 1, 2000, [(self.AREA, 6)]))
        self.assertEqual(self.state.current_map.sequence, 1)
        self.assertEqual(self.state.maps_received, 2)

    def test_resync_after_a_strip_stays_missing(self):
        self.feed(map_packet(9, 1, 10, [(self.AREA, 5)]), now=100.0)
        self.assertFalse(self.state.needs_resync(102.9))
        self.assertTrue(self.state.needs_resync(103.0))

    def test_no_resync_while_chunks_are_still_arriving(self):
        self.feed(map_packet(9, 1, 10, [(self.AREA, 5)]), now=100.0)
        self.feed(chunk(9, 6, self.AREA, 1, 4, 0, b"ab"), now=102.5)
        self.assertFalse(self.state.needs_resync(105.0))
        self.assertTrue(self.state.needs_resync(105.5))

    def test_no_resync_once_the_strip_arrives(self):
        self.feed(map_packet(9, 1, 10, [(self.AREA, 5)]), now=100.0)
        self.feed(chunk(9, 5, self.AREA, 1, 2, 0, b"ab"), now=101.0)
        self.assertFalse(self.state.needs_resync(200.0))

    def test_no_resync_when_the_missing_strip_left_the_screen(self):
        self.feed(map_packet(9, 1, 10, [(self.AREA, 5)]), now=100.0)
        self.feed(map_packet(9, 2, 20, [(self.AREA, 6)]), now=102.0)
        self.assertFalse(self.state.needs_resync(103.5))
        self.assertTrue(self.state.needs_resync(105.0))

    def test_keeps_strips_across_a_new_session(self):
        self.feed(chunk(9, 5, self.AREA, 1, 2, 0, b"ab"))
        self.state.start_session(10)
        self.assertEqual(self.state.session, 10)
        self.assertIn(5, self.state.strips)
        self.assertIsNone(self.state.current_map)

    def test_stores_stats_by_name(self):
        self.feed(stats_packet(9, stats_values(udp_bytes=1234)))
        self.assertEqual(self.state.stats["udp_bytes"], 1234)


class StripCacheTests(unittest.TestCase):
    def test_a_saved_strip_loads_again_with_its_area_and_codec(self):
        with tempfile.TemporaryDirectory() as tmp:
            strip = sv.Strip((1, 2, 3, 4), sv.CODEC_JPEG, b"jpeg")
            sv.StripCache(Path(tmp)).save(5, strip)
            self.assertEqual(sv.StripCache(Path(tmp)).load(), {5: strip})

    def test_load_ignores_files_it_did_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("x")
            (Path(tmp) / "zz-1-2-3-4.jpg").write_text("x")
            self.assertEqual(sv.StripCache(Path(tmp)).load(), {})

    def test_load_returns_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = sv.StripCache(Path(tmp))
            cache.save(2, sv.Strip((0, 0, 1, 1), sv.CODEC_RAW565, b"ab"))
            cache.save(1, sv.Strip((0, 0, 1, 1), sv.CODEC_RAW565, b"cd"))
            os.utime(Path(tmp) / "00000002-0-0-1-1.rgb565", (10, 10))
            os.utime(Path(tmp) / "00000001-0-0-1-1.rgb565", (20, 20))
            self.assertEqual(list(cache.load()), [2, 1])


class SeedTests(unittest.TestCase):
    def test_splits_hashes_into_packets_the_robot_can_read(self):
        packets = sv.seed_packets(9, list(range(1, 251)))
        self.assertEqual(len(packets), 3)
        for packet in packets:
            self.assertLessEqual(len(packet), 512)
        self.assertEqual(packets[0], sv.ack_packet(9, list(range(1, 101))))

    def test_keeps_the_newest_hashes_within_what_the_robot_remembers(self):
        hashes = list(range(1, sv.MAX_SEEDED_STRIPS + 51))
        packets = sv.seed_packets(9, hashes)
        sent = []
        for packet in packets:
            count = struct.unpack_from("<H", packet, 8)[0]
            sent += struct.unpack_from(f"<{count}I", packet, 10)
        self.assertEqual(len(sent), sv.MAX_SEEDED_STRIPS)
        self.assertEqual(sent[-1], hashes[-1])
        self.assertEqual(sent[0], 51)

    def test_no_hashes_need_no_packets(self):
        self.assertEqual(sv.seed_packets(9, []), [])


class RecordingCachedStripsTests(unittest.TestCase):
    def test_a_recording_includes_strips_that_came_from_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            viewer = sv.Viewer.__new__(sv.Viewer)
            viewer.changed = sv.threading.Condition(sv.threading.Lock())
            viewer.event_counter = 0
            viewer.state = sv.StreamState(session=9)
            viewer.state.strips[5] = sv.Strip((0, 0, 1, 1), sv.CODEC_RAW565, b"\x00\xf8")
            viewer.recorder = sv.Recorder(Path(tmp) / "session", {})
            packet = sv.parse_packet(map_packet(9, 1, 10, [((0, 0, 1, 1), 5)]))
            with viewer.changed:
                viewer.on_map(packet, 1.0)
            viewer.recorder.close()
            self.assertTrue((Path(tmp) / "session/strips/00000005.rgb565").exists())


class HttpTests(unittest.TestCase):
    def test_a_browser_closing_the_page_is_not_an_error(self):
        class ClosedPipe:
            def write(self, _):
                raise BrokenPipeError

        handler_class = sv.make_handler(None)
        handler = handler_class.__new__(handler_class)
        handler.wfile = ClosedPipe()
        handler.send_response = handler.send_header = lambda *_: None
        handler.end_headers = lambda: None
        handler.respond(404, "text/plain", b"Not yet")


class ArgumentTests(unittest.TestCase):
    def test_refuses_raw_whole_frames(self):
        with self.assertRaises(SystemExit):
            sv.main(["view", "--robot", "127.0.0.1", "--mode", "frames", "--quality", "0"])


class ViewerReportTests(unittest.TestCase):
    def report_for(self, rows):
        viewer = sv.Viewer.__new__(sv.Viewer)
        viewer.lock = sv.threading.Lock()
        viewer.state = sv.StreamState(session=9)
        viewer.stats_rows = rows
        viewer.recorder = None
        return viewer.report()

    def test_says_the_robot_did_not_answer_without_stats(self):
        with self.assertLogs("screen_viewer", level="ERROR") as logs:
            self.assertEqual(self.report_for([]), 1)
        self.assertIn("did not answer", logs.output[0])

    def test_says_the_session_was_too_short_with_one_stats_packet(self):
        row = {"t": 1.0, **dict(zip(sv.STAT_NAMES, stats_values()))}
        with self.assertLogs("screen_viewer", level="ERROR") as logs:
            self.assertEqual(self.report_for([row]), 1)
        self.assertIn("only one stats packet", logs.output[0])


class ReportTests(unittest.TestCase):
    def test_summarizes_a_session(self):
        rows = [
            {"t": 0.0, **dict(zip(sv.STAT_NAMES, stats_values(
                frames_seen=0, hook_us_total=0, udp_bytes=0, mic_frames_dropped=3,
                internal_min_free=90000, internal_free=95000)))},
            {"t": 10.0, **dict(zip(sv.STAT_NAMES, stats_values(
                audio_decoded=50, audio_played=40, audio_dropped=7,
                frames_seen=80, hook_us_total=80000, hook_us_max=2000, udp_bytes=100000,
                encode_count=10, encode_us_total=30000, encode_us_max=5000,
                mic_frames_dropped=5, internal_min_free=80000, internal_free=85000,
                stack_high_water=1000)))},
        ]
        report = sv.summarize(rows, maps_received=40)
        self.assertEqual(report["seconds"], 10.0)
        self.assertEqual(report["frames_per_second"], 8.0)
        self.assertEqual(report["hook_us_per_frame"], 1000.0)
        self.assertEqual(report["hook_us_max"], 2000)
        self.assertEqual(report["kbit_per_second"], 80.0)
        self.assertEqual(report["encode_us_average"], 3000.0)
        self.assertEqual(report["mic_frames_dropped"], 2)
        self.assertEqual(report["internal_min_free"], 80000)
        self.assertEqual(report["maps_per_second"], 4.0)
        self.assertEqual(report["audio_decoded"], 50)
        self.assertEqual(report["audio_played"], 40)
        self.assertEqual(report["audio_dropped"], 7)

    def test_counts_across_a_robot_counter_reset(self):
        def row(t, frames):
            return {"t": t, **dict(zip(sv.STAT_NAMES, stats_values(frames_seen=frames)))}
        report = sv.summarize([row(0.0, 0), row(5.0, 50), row(6.0, 2), row(10.0, 42)], 0)
        self.assertEqual(report["frames_per_second"], 9.2)  # 50 + 2 + 40 frames in 10 s

    def test_empty_session_has_no_report(self):
        self.assertIsNone(sv.summarize([], maps_received=0))


class RecorderTests(unittest.TestCase):
    AREA = (0, 0, 2, 1)

    def test_writes_strips_timeline_and_stats_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "session"
            recorder = sv.Recorder(directory, {"mode": "strips"})
            strip = sv.Strip(self.AREA, sv.CODEC_RAW565, b"\x00\xf8\xe0\x07")
            recorder.strip(5, strip)
            recorder.strip(5, strip)
            parsed = sv.parse_packet(map_packet(9, 1, 10, [(self.AREA, 5)]))
            recorder.frame(parsed, 1.5)
            recorder.stats({"udp_bytes": 1}, 2.0)
            recorder.close()

            self.assertEqual((directory / "strips" / "00000005.rgb565").read_bytes(),
                             b"\x00\xf8\xe0\x07")
            self.assertEqual(json.loads((directory / "meta.json").read_text())["mode"], "strips")
            timeline = [json.loads(line) for line in
                        (directory / "timeline.jsonl").read_text().splitlines()]
            self.assertEqual(timeline, [{
                "t": 1.5, "sequence": 1, "robot_ms": 10,
                "entries": [[0, 0, 2, 1, "00000005"]],
            }])
            stats = [json.loads(line) for line in
                     (directory / "stats.jsonl").read_text().splitlines()]
            self.assertEqual(stats, [{"t": 2.0, "udp_bytes": 1}])


@unittest.skipIf(Image is None, "Pillow is optional")
class ComposeTests(unittest.TestCase):
    def test_raw_pixels_are_little_endian_rgb565(self):
        strip = sv.Strip((0, 0, 3, 1), sv.CODEC_RAW565, struct.pack("<3H", 0xF800, 0x07E0, 0x001F))
        image = sv.decode_strip(strip)
        self.assertEqual(image.tobytes(), bytes((255, 0, 0, 0, 255, 0, 0, 0, 255)))

    def test_composes_in_painting_order(self):
        strips = {
            1: sv.Strip((0, 0, 2, 1), sv.CODEC_RAW565, struct.pack("<2H", 0xF800, 0xF800)),
            2: sv.Strip((1, 0, 1, 1), sv.CODEC_RAW565, struct.pack("<H", 0x001F)),
        }
        canvas = Image.new("RGB", (2, 1))
        sv.paint(canvas, [((0, 0, 2, 1), 1), ((1, 0, 1, 1), 2), ((0, 0, 1, 1), 99)], strips, {})
        self.assertEqual(canvas.tobytes(), bytes((255, 0, 0, 0, 0, 255)))

    def test_exports_png_frames_from_a_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "session"
            recorder = sv.Recorder(directory, {"mode": "strips"})
            recorder.strip(5, sv.Strip((0, 0, 320, 240), sv.CODEC_RAW565, b"\x00\xf8" * 76800))
            parsed = sv.parse_packet(map_packet(9, 1, 10, [((0, 0, 320, 240), 5)]))
            recorder.frame(parsed, 1.5)
            parsed = sv.parse_packet(map_packet(9, 2, 110, [((0, 0, 320, 240), 5)]))
            recorder.frame(parsed, 1.6)
            recorder.close()

            frames = sv.export_frames(directory)

            self.assertEqual(len(frames), 2)
            with Image.open(frames[0][0]) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
            self.assertAlmostEqual(frames[0][1], 0.1)


if __name__ == "__main__":
    unittest.main()
