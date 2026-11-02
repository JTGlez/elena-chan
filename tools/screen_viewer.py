#!/usr/bin/env python3
"""Watch and record Elena's screen on this PC; see docs/screen-stream.md.

Needs an image built with SCREEN_STREAM=1. This script
uses only the standard library. Pillow is optional: without it, /latest.png and
`export` are unavailable, and everything else works.
"""

import argparse
import json
import logging
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

try:
    from PIL import Image
except ImportError:
    Image = None

LOG = logging.getLogger("screen_viewer")

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = ROOT / "dist" / "screen-recordings"
STRIP_CACHE = ROOT / "dist" / "screen-cache"

PORT = 12801
MODE_STRIPS = 0
MODE_FRAMES = 1
CODEC_RAW565 = 0
CODEC_JPEG = 1
WIDTH = 320
HEIGHT = 240
HELLO_PERIOD_S = 1.0
RESYNC_AFTER_S = 3.0
MAX_STRIP_BYTES = WIDTH * HEIGHT * 2
SEED_BATCH = 100  # The robot reads control packets of up to 512 bytes
MAX_SEEDED_STRIPS = 700  # Under the 768 it remembers before forgetting them all
SEED_PAUSE_S = 0.02

MAGIC = b"ES"
VERSION = 1
TYPE_HELLO = 1
TYPE_ACK = 2
TYPE_CHUNK = 0x10
TYPE_MAP = 0x11
TYPE_STATS = 0x12
CHUNK_FORMAT = "<I4HBxII"
CHUNK_SIZE = struct.calcsize(CHUNK_FORMAT)

# Same order as enum Stat in the firmware's screen_stream_core.h.
STAT_NAMES = (
    "frames_seen",
    "strips_seen",
    "strips_new",
    "strips_dropped",
    "hook_us_total",
    "hook_us_max",
    "encode_count",
    "encode_us_total",
    "encode_us_max",
    "encoded_bytes",
    "udp_bytes",
    "udp_datagrams",
    "send_errors",
    "paused_low_memory",
    "internal_free",
    "internal_min_free",
    "internal_largest",
    "psram_free",
    "stack_high_water",
    "mic_frames_dropped",
    "audio_decoded",
    "audio_played",
    "audio_dropped",
)


def header(packet_type, session):
    return MAGIC + bytes((packet_type, VERSION)) + struct.pack("<I", session)


def hello_packet(session, mode, quality, max_kbps):
    return header(TYPE_HELLO, session) + struct.pack("<BBH", mode, quality, max_kbps)


def ack_packet(session, hashes):
    return (
        header(TYPE_ACK, session)
        + struct.pack("<H", len(hashes))
        + struct.pack(f"<{len(hashes)}I", *hashes)
    )


def parse_packet(data):
    """Return a packet from the robot, or None if it is not a valid one."""
    if len(data) < 8 or data[:2] != MAGIC or data[3] != VERSION:
        return None
    session = struct.unpack_from("<I", data, 4)[0]
    body = data[8:]
    try:
        if data[2] == TYPE_CHUNK:
            hash_, x, y, w, h, codec, total, offset = struct.unpack_from(CHUNK_FORMAT, body)
            return SimpleNamespace(
                kind="chunk", session=session, hash=hash_, area=(x, y, w, h), codec=codec,
                total=total, offset=offset, payload=body[CHUNK_SIZE:],
            )
        if data[2] == TYPE_MAP:
            sequence, time_ms, count = struct.unpack_from("<IIH", body)
            entries = []
            for index in range(count):
                x, y, w, h, hash_ = struct.unpack_from("<4HI", body, 10 + index * 12)
                entries.append(((x, y, w, h), hash_))
            return SimpleNamespace(
                kind="map", session=session, sequence=sequence, time_ms=time_ms, entries=entries,
            )
        if data[2] == TYPE_STATS:
            count = struct.unpack_from("<H", body)[0]
            values = struct.unpack_from(f"<{count}I", body, 2)
            return SimpleNamespace(
                kind="stats", session=session, values=dict(zip(STAT_NAMES, values)),
            )
    except struct.error:
        return None
    return None


@dataclass
class Strip:
    area: tuple
    codec: int
    data: bytes


class StreamState:
    """What is known about the screen. Not thread safe: callers hold a lock."""

    def __init__(self, session):
        self.session = session
        self.strips = {}
        self.partial = {}
        self.current_map = None
        self.stats = {}
        self.missing_since = {}
        self.last_chunk = None
        self.maps_received = 0
        self.on_strip = None
        self.on_map = None
        self.on_stats = None

    def start_session(self, session):
        """Begin again with the robot. Strips stay valid: they are named by content."""
        self.session = session
        self.partial.clear()
        self.current_map = None
        self.missing_since.clear()
        self.last_chunk = None

    def handle(self, data, now):
        """Take one datagram; return the hashes to confirm to the robot."""
        packet = parse_packet(data)
        if packet is None or packet.session != self.session:
            return []
        if packet.kind == "chunk":
            self.last_chunk = now
            return self._handle_chunk(packet)
        if packet.kind == "map":
            self._handle_map(packet, now)
        elif packet.kind == "stats":
            self.stats = packet.values
            if self.on_stats:
                self.on_stats(packet.values, now)
        return []

    def _handle_chunk(self, chunk):
        if chunk.hash in self.strips:
            return [chunk.hash]  # The robot is sending it again, so our confirmation was lost.
        end = chunk.offset + len(chunk.payload)
        if chunk.total > MAX_STRIP_BYTES or end > chunk.total or not chunk.payload:
            return []
        partial = self.partial.get(chunk.hash)
        if partial is None or partial.total != chunk.total or partial.codec != chunk.codec:
            partial = SimpleNamespace(
                total=chunk.total, codec=chunk.codec, area=chunk.area,
                data=bytearray(chunk.total), ranges={},
            )
            self.partial[chunk.hash] = partial
        partial.data[chunk.offset:end] = chunk.payload
        partial.ranges[chunk.offset] = len(chunk.payload)
        if sum(partial.ranges.values()) < partial.total:
            return []
        del self.partial[chunk.hash]
        strip = Strip(partial.area, partial.codec, bytes(partial.data))
        self.strips[chunk.hash] = strip
        self.missing_since.pop(chunk.hash, None)
        if self.on_strip:
            self.on_strip(chunk.hash, strip)
        return [chunk.hash]

    def _handle_map(self, packet, now):
        # The robot's clock decides what is newest: its sequence restarts with the stream.
        if self.current_map is not None and packet.time_ms < self.current_map.time_ms:
            return
        is_new = self.current_map is None or packet.sequence != self.current_map.sequence
        self.current_map = packet
        wanted = {hash_ for _, hash_ in packet.entries if hash_ not in self.strips}
        self.missing_since = {h: self.missing_since.get(h, now) for h in wanted}
        if is_new:
            self.maps_received += 1
            if self.on_map:
                self.on_map(packet, now)

    def needs_resync(self, now):
        """True when a strip on screen is missing and the robot has stopped sending.

        The stream runs at the lowest priority, so while chunks keep arriving a missing
        strip is only slow. Silence means the robot evicted it and will not send it again.
        """
        if self.last_chunk is not None and now - self.last_chunk < RESYNC_AFTER_S:
            return False
        return any(now - since >= RESYNC_AFTER_S for since in self.missing_since.values())


class StripCache:
    """Strips kept between sessions. They are named by content, so they stay valid.

    Compressing new strips on the robot is what cost it voice packets (see
    docs/screen-stream.md), so whatever this PC already has is not asked for again.
    """

    SUFFIXES = {CODEC_JPEG: "jpg", CODEC_RAW565: "rgb565"}

    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, hash_, strip):
        self.directory.mkdir(parents=True, exist_ok=True)
        x, y, w, h = strip.area
        name = f"{hash_:08x}-{x}-{y}-{w}-{h}.{self.SUFFIXES[strip.codec]}"
        (self.directory / name).write_bytes(strip.data)

    def load(self):
        """Return the cached strips, oldest first."""
        codecs = {suffix: codec for codec, suffix in self.SUFFIXES.items()}
        if not self.directory.is_dir():
            return {}
        strips = {}
        paths = sorted(self.directory.iterdir(), key=lambda path: path.stat().st_mtime)
        for path in paths:
            parts = path.stem.split("-")
            codec = codecs.get(path.suffix[1:])
            if codec is None or len(parts) != 5:
                continue
            try:
                hash_, *area = int(parts[0], 16), *map(int, parts[1:])
            except ValueError:
                continue
            strips[hash_] = Strip(tuple(area), codec, path.read_bytes())
        return strips


def seed_packets(session, hashes):
    """Tell the robot which strips this PC already has, newest first to survive the cap."""
    kept = list(hashes)[-MAX_SEEDED_STRIPS:]
    return [ack_packet(session, kept[i:i + SEED_BATCH]) for i in range(0, len(kept), SEED_BATCH)]


class Recorder:
    """Stores a session so it can be replayed or exported later."""

    def __init__(self, directory, meta):
        self.directory = Path(directory)
        (self.directory / "strips").mkdir(parents=True, exist_ok=True)
        (self.directory / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.timeline = (self.directory / "timeline.jsonl").open("w", encoding="utf-8")
        self.stats_file = (self.directory / "stats.jsonl").open("w", encoding="utf-8")

    def strip(self, hash_, strip):
        suffix = "jpg" if strip.codec == CODEC_JPEG else "rgb565"
        path = self.directory / "strips" / f"{hash_:08x}.{suffix}"
        if not path.exists():
            path.write_bytes(strip.data)

    def frame(self, packet, now):
        entries = [[*area, f"{hash_:08x}"] for area, hash_ in packet.entries]
        row = {"t": now, "sequence": packet.sequence, "robot_ms": packet.time_ms,
               "entries": entries}
        self.timeline.write(json.dumps(row) + "\n")
        self.timeline.flush()

    def stats(self, values, now):
        self.stats_file.write(json.dumps({"t": now, **values}) + "\n")
        self.stats_file.flush()

    def close(self):
        self.timeline.close()
        self.stats_file.close()


def summarize(rows, maps_received):
    """Turn a session's stats rows into the figures used to compare modes."""
    if not rows:
        return None
    first, last = rows[0], rows[-1]
    seconds = last["t"] - first["t"]
    if seconds <= 0:
        return None

    def delta(name):
        # A counter that went down means the robot restarted the stream and counted from 0.
        pairs = zip(rows, rows[1:])
        return sum(b[name] - a[name] if b[name] >= a[name] else b[name] for a, b in pairs)

    frames = delta("frames_seen")
    encodes = delta("encode_count")
    return {
        "seconds": round(seconds, 1),
        "frames_per_second": round(frames / seconds, 2),
        "maps_per_second": round(maps_received / seconds, 2),
        "hook_us_per_frame": round(delta("hook_us_total") / frames, 1) if frames else 0,
        "hook_us_max": max(row["hook_us_max"] for row in rows),
        "encode_count": encodes,
        "encode_us_average": round(delta("encode_us_total") / encodes, 1) if encodes else 0,
        "encode_us_max": max(row["encode_us_max"] for row in rows),
        "kbit_per_second": round(delta("udp_bytes") * 8 / 1000 / seconds, 1),
        "strips_new": delta("strips_new"),
        "strips_dropped": delta("strips_dropped"),
        "send_errors": delta("send_errors"),
        "paused_low_memory": delta("paused_low_memory"),
        "mic_frames_dropped": delta("mic_frames_dropped"),
        "audio_decoded": delta("audio_decoded"),
        "audio_played": delta("audio_played"),
        "audio_dropped": delta("audio_dropped"),
        "internal_min_free": min(row["internal_min_free"] for row in rows),
        "internal_free_last": last["internal_free"],
        "internal_largest_min": min(row["internal_largest"] for row in rows),
        "psram_free_last": last["psram_free"],
        "stack_high_water": min(row["stack_high_water"] for row in rows),
    }


def decode_strip(strip):
    """Return a strip as a Pillow image."""
    x, y, w, h = strip.area
    if strip.codec == CODEC_JPEG:
        with Image.open(BytesIO(strip.data)) as image:
            return image.convert("RGB")
    pixels = bytearray(w * h * 3)
    values = struct.unpack(f"<{w * h}H", strip.data)
    for index, value in enumerate(values):
        red = (value >> 11) & 0x1F
        green = (value >> 5) & 0x3F
        blue = value & 0x1F
        pixels[index * 3] = (red << 3) | (red >> 2)
        pixels[index * 3 + 1] = (green << 2) | (green >> 4)
        pixels[index * 3 + 2] = (blue << 3) | (blue >> 2)
    return Image.frombytes("RGB", (w, h), bytes(pixels))


def paint(canvas, entries, strips, cache):
    """Paint a screen map; a strip that has not arrived leaves what was there."""
    for area, hash_ in entries:
        if hash_ not in strips:
            continue
        if hash_ not in cache:
            cache[hash_] = decode_strip(strips[hash_])
        canvas.paste(cache[hash_], (area[0], area[1]))


def load_recorded_strip(strips_dir, name, area):
    jpeg = strips_dir / f"{name}.jpg"
    if jpeg.exists():
        return Strip(tuple(area), CODEC_JPEG, jpeg.read_bytes())
    raw = strips_dir / f"{name}.rgb565"
    if raw.exists():
        return Strip(tuple(area), CODEC_RAW565, raw.read_bytes())
    return None


def export_frames(directory):
    """Write each recorded frame as a PNG; return (path, seconds on screen) pairs."""
    if Image is None:
        raise SystemExit("Exporting needs Pillow.")
    directory = Path(directory)
    frames_dir = directory / "frames"
    frames_dir.mkdir(exist_ok=True)
    with (directory / "timeline.jsonl").open(encoding="utf-8") as timeline:
        rows = [json.loads(line) for line in timeline]

    strips, cache = {}, {}
    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    frames = []
    for index, row in enumerate(rows):
        entries = []
        for x, y, w, h, name in row["entries"]:
            hash_ = int(name, 16)
            if hash_ not in strips:
                strip = load_recorded_strip(directory / "strips", name, (x, y, w, h))
                if strip is None:
                    continue
                strips[hash_] = strip
            entries.append(((x, y, w, h), hash_))
        paint(canvas, entries, strips, cache)
        path = frames_dir / f"{index:06d}.png"
        canvas.save(path, compress_level=1)
        following = rows[index + 1]["t"] if index + 1 < len(rows) else row["t"] + 0.1
        frames.append((path, following - row["t"]))
    return frames


def export_video(directory, output):
    """Export a recording to a video with its real timing, using ffmpeg."""
    frames = export_frames(directory)
    if not frames:
        raise SystemExit("The recording has no frames.")
    if shutil.which("ffmpeg") is None:
        raise SystemExit(f"PNG frames are in {Path(directory) / 'frames'}. Video needs ffmpeg.")
    concat = Path(directory) / "frames" / "frames.ffconcat"
    lines = ["ffconcat version 1.0"]
    for path, seconds in frames:
        lines += [f"file '{path.name}'", f"duration {seconds:.3f}"]
    lines.append(f"file '{frames[-1][0].name}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat), "-vf", "fps=30,scale=640:480:flags=neighbor",
         "-pix_fmt", "yuv420p", str(output)],
        check=True,
    )


PAGE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Pantalla de Elena</title>
<style>
body{background:#1b1b1f;color:#e6e6e6;font:14px system-ui;display:flex;gap:24px;padding:24px}
canvas{width:640px;height:480px;image-rendering:pixelated;background:#000}
table{border-collapse:collapse}td{padding:2px 12px 2px 0}td:last-child{text-align:right}
#age{margin-top:8px}.stale{color:#ff8a80}
</style></head><body>
<div><canvas id="screen" width="320" height="240"></canvas><div id="age"></div></div>
<table id="stats"></table>
<script>
const context = document.getElementById("screen").getContext("2d");
const bitmaps = new Map();
const loading = new Set();
let current = [];
let lastFrame = 0;

async function load(entry) {
  const [x, y, w, h, hash] = entry;
  if (bitmaps.has(hash) || loading.has(hash)) return;
  loading.add(hash);
  try {
    const response = await fetch("/strip/" + hash);
    if (!response.ok) return;
    const data = await response.arrayBuffer();
    if (response.headers.get("X-Codec") === "jpeg") {
      bitmaps.set(hash, await createImageBitmap(new Blob([data], {type: "image/jpeg"})));
    } else {
      const pixels = new Uint16Array(data);
      const rgba = new Uint8ClampedArray(pixels.length * 4);
      pixels.forEach((value, i) => {
        rgba[i * 4] = ((value >> 11) & 31) * 255 / 31;
        rgba[i * 4 + 1] = ((value >> 5) & 63) * 255 / 63;
        rgba[i * 4 + 2] = (value & 31) * 255 / 31;
        rgba[i * 4 + 3] = 255;
      });
      bitmaps.set(hash, await createImageBitmap(new ImageData(rgba, w, h)));
    }
    draw();
  } finally {
    loading.delete(hash);
  }
}

function draw() {
  for (const entry of current) {
    const bitmap = bitmaps.get(entry[4]);
    if (bitmap) context.drawImage(bitmap, entry[0], entry[1]);
    else load(entry);
  }
}

const events = new EventSource("/events");
events.addEventListener("map", event => {
  current = JSON.parse(event.data).entries;
  lastFrame = performance.now();
  draw();
});
events.addEventListener("stats", event => {
  const rows = Object.entries(JSON.parse(event.data));
  document.getElementById("stats").innerHTML =
    rows.map(([name, value]) => `<tr><td>${name}</td><td>${value}</td></tr>`).join("");
});
setInterval(() => {
  const seconds = (performance.now() - lastFrame) / 1000;
  const label = document.getElementById("age");
  label.textContent = lastFrame ? `Último cambio hace ${seconds.toFixed(1)} s` : "Esperando al robot";
}, 250);
</script></body></html>
"""


class Viewer:
    """Holds the stream open with the robot and serves it to a browser."""

    def __init__(self, args):
        self.args = args
        self.robot = args.robot
        self.mode = MODE_FRAMES if args.mode == "frames" else MODE_STRIPS
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.state = StreamState(random.getrandbits(32) or 1)
        self.state.on_strip = self.on_strip
        self.state.on_map = self.on_map
        self.state.on_stats = self.on_stats
        self.cache = None if args.no_cache else StripCache(args.cache)
        self.seeded_session = None
        if self.cache:
            self.state.strips.update(self.cache.load())
        self.stats_rows = []
        self.event_counter = 0
        self.strip_images = {}
        self.canvas = Image.new("RGB", (WIDTH, HEIGHT)) if Image else None
        self.recorder = None
        if args.record is not None:
            directory = args.record or RECORDINGS / datetime.now().strftime("%Y%m%d-%H%M%S")
            self.recorder = Recorder(directory, {
                "mode": args.mode, "quality": args.quality, "kbps": args.kbps,
                "started": datetime.now().isoformat(timespec="seconds"),
            })

    def on_strip(self, hash_, strip):
        if self.cache:
            self.cache.save(hash_, strip)
        if self.recorder:
            self.recorder.strip(hash_, strip)

    def on_map(self, packet, now):
        if self.recorder:
            # Strips from the cache never arrive as chunks, so store them as they appear.
            for _, hash_ in packet.entries:
                if hash_ in self.state.strips:
                    self.recorder.strip(hash_, self.state.strips[hash_])
            self.recorder.frame(packet, now)
        self.event_counter += 1
        self.changed.notify_all()

    def on_stats(self, values, now):
        self.stats_rows.append({"t": now, **values})
        if self.recorder:
            self.recorder.stats(values, now)
        self.event_counter += 1
        self.changed.notify_all()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        address = (self.robot, PORT)
        started = time.monotonic()
        last_hello = 0.0
        print(f"Connected to the robot. Open http://127.0.0.1:{self.args.http_port}/", flush=True)
        while self.args.duration is None or time.monotonic() - started < self.args.duration:
            now = time.monotonic()
            if now - last_hello >= HELLO_PERIOD_S:
                last_hello = now
                with self.lock:
                    if self.state.needs_resync(now):
                        self.state.start_session(random.getrandbits(32) or 1)
                    session = self.state.session
                sock.sendto(hello_packet(session, self.mode, self.args.quality, self.args.kbps),
                            address)
                if session != self.seeded_session and self.mode == MODE_STRIPS:
                    self.seeded_session = session
                    with self.lock:
                        known = list(self.state.strips)
                    for packet in seed_packets(session, known):
                        sock.sendto(packet, address)
                        time.sleep(SEED_PAUSE_S)
            try:
                data, source = sock.recvfrom(2048)
            except TimeoutError:
                continue
            if source[0] != self.robot:
                continue
            with self.lock:
                acks = self.state.handle(data, time.monotonic())
                session = self.state.session
            if acks:
                sock.sendto(ack_packet(session, acks), address)
        sock.close()

    def report(self):
        with self.lock:
            report = summarize(self.stats_rows, self.state.maps_received)
            strips = len(self.state.strips)
        if self.recorder:
            self.recorder.close()
        if report is None:
            if self.stats_rows:
                LOG.error("The robot sent only one stats packet, too few for a report. "
                          "The session was too short or the robot too busy.")
            else:
                LOG.error("The robot did not answer. Check its address and that its image "
                          "was built with SCREEN_STREAM=1.")
            return 1
        report["strips_on_pc"] = strips
        text = json.dumps(report, indent=2)
        print(text)
        if self.recorder:
            (self.recorder.directory / "report.json").write_text(text + "\n", encoding="utf-8")
            print(f"Recording: {self.recorder.directory}")
        return 0


def make_handler(viewer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, content_type, body, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self.respond(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            elif self.path == "/events":
                self.send_events()
            elif self.path.startswith("/strip/"):
                self.send_strip(self.path[len("/strip/"):])
            elif self.path == "/latest.png":
                self.send_latest()
            else:
                self.respond(404, "text/plain", b"Not found")

        def send_strip(self, name):
            try:
                hash_ = int(name, 16)
            except ValueError:
                self.respond(404, "text/plain", b"Not found")
                return
            with viewer.lock:
                strip = viewer.state.strips.get(hash_)
            if strip is None:
                self.respond(404, "text/plain", b"Not yet")
                return
            codec = "jpeg" if strip.codec == CODEC_JPEG else "raw565"
            self.respond(200, "application/octet-stream", strip.data, {
                "X-Codec": codec, "Cache-Control": "max-age=86400",
            })

        def send_latest(self):
            if viewer.canvas is None:
                self.respond(503, "text/plain", b"This needs Pillow")
                return
            with viewer.lock:
                entries = viewer.state.current_map.entries if viewer.state.current_map else []
                paint(viewer.canvas, entries, viewer.state.strips, viewer.strip_images)
                output = BytesIO()
                viewer.canvas.save(output, "PNG", compress_level=1)
            self.respond(200, "image/png", output.getvalue(), {"Cache-Control": "no-store"})

        def send_events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with viewer.changed:
                        viewer.changed.wait_for(lambda: viewer.event_counter != seen, timeout=15)
                        seen = viewer.event_counter
                        current = viewer.state.current_map
                        entries = [[*area, f"{hash_:08x}"] for area, hash_ in current.entries] if current else []
                        stats = viewer.state.stats
                    body = f"event: map\ndata: {json.dumps({'entries': entries})}\n\n"
                    body += f"event: stats\ndata: {json.dumps(stats)}\n\n"
                    self.wfile.write(body.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    view = commands.add_parser("view", help="Watch the screen, and record it if asked")
    view.add_argument("--robot", default=os.environ.get("ELENA_ROBOT_IP"),
                      help="The robot's IPv4 address, or set ELENA_ROBOT_IP")
    view.add_argument("--mode", choices=("strips", "frames"), default="strips",
                      help="strips sends each distinct strip once; frames sends every frame")
    view.add_argument("--quality", type=int, default=50, help="JPEG quality; 0 sends raw RGB565")
    view.add_argument("--kbps", type=int, default=1000, help="The most bandwidth to use")
    view.add_argument("--http-port", type=int, default=8765)
    view.add_argument("--cache", type=Path, default=STRIP_CACHE,
                      help="Where to keep strips between sessions")
    view.add_argument("--no-cache", action="store_true",
                      help="Ask the robot for every strip again, to measure that cost")
    view.add_argument("--duration", type=float, help="Stop after this many seconds")
    view.add_argument("--record", nargs="?", const="", default=None, metavar="DIR",
                      help=f"Record the session, by default under {RECORDINGS}")

    export = commands.add_parser("export", help="Turn a recording into PNG frames and a video")
    export.add_argument("directory", type=Path)
    export.add_argument("--out", type=Path, help="Video path, by default inside the recording")

    args = parser.parse_args(argv)

    if args.command == "export":
        output = args.out or args.directory / "screen.mp4"
        export_video(args.directory, output)
        print(f"Video: {output}")
        return 0

    if not args.robot:
        parser.error("Set the robot's address with --robot or ELENA_ROBOT_IP.")
    if not 0 <= args.quality <= 100:
        parser.error("--quality must be between 0 and 100.")
    if args.mode == "frames" and args.quality == 0:
        parser.error("Raw whole frames are too heavy for the robot, so they are not allowed.")

    viewer = Viewer(args)
    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(viewer))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        viewer.run()
    except KeyboardInterrupt:
        pass
    server.shutdown()
    return viewer.report()


if __name__ == "__main__":
    sys.exit(main())
