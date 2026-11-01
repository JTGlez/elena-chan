"""Hardware-free tests. Run: python3 -m unittest discover -s .claude/skills/stackchan-flash/scripts"""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import stackchan_flash as sf


def entry(label, kind, subtype, offset, size):
    return b"\xaa\x50" + struct.pack("<BBII", kind, subtype, offset, size) + label.encode().ljust(16, b"\0") + bytes(4)


TABLE = b"".join([
    entry("nvs", 1, 2, 0x9000, 0x4000),
    entry("otadata", 1, 0, 0xD000, 0x2000),
    entry("phy_init", 1, 1, 0xF000, 0x1000),
    entry("ota_0", 0, 0x10, 0x20000, 0x4F0000),
    entry("ota_1", 0, 0x11, 0x510000, 0x4F0000),
])
LIVE = sf.parse_partitions(TABLE)


def ota_entry(seq, state=2):
    head = struct.pack("<I", seq)
    return head + bytes(20) + struct.pack("<II", state, zlib.crc32(head, 0xFFFFFFFF))


class Parsing(unittest.TestCase):
    def test_partitions(self):
        self.assertEqual([p["label"] for p in LIVE], ["nvs", "otadata", "phy_init", "ota_0", "ota_1"])
        self.assertEqual(sf.protected_ranges(LIVE), [("nvs", 0x9000, 0xD000), ("phy_init", 0xF000, 0x10000)])

    def test_active_slot(self):
        blank = b"\xff" * 0x2000
        self.assertEqual(sf.active_ota_slot(blank, 2), 0)
        self.assertEqual(sf.active_ota_slot(ota_entry(1).ljust(0x2000, b"\xff"), 2), 0)
        self.assertEqual(sf.active_ota_slot(ota_entry(2).ljust(0x2000, b"\xff"), 2), 1)
        self.assertEqual(sf.active_ota_slot(ota_entry(1).ljust(0x1000, b"\xff") + ota_entry(4), 2), 1)

    def test_active_slot_ignores_bad_entries(self):
        invalid = ota_entry(1).ljust(0x1000, b"\xff") + ota_entry(2, state=3)
        self.assertEqual(sf.active_ota_slot(invalid, 2), 0)
        corrupt = bytearray(ota_entry(2))
        corrupt[28] ^= 0xFF
        self.assertEqual(sf.active_ota_slot(bytes(corrupt).ljust(0x2000, b"\xff"), 2), 0)

    def test_app_description(self):
        desc = struct.pack("<I", 0xABCD5432) + bytes(12) + b"1.4.4".ljust(32, b"\0") + b"stack-chan".ljust(32, b"\0")
        desc += b"18:05:42".ljust(16, b"\0") + b"Jul 10 2026".ljust(16, b"\0")
        info = sf.app_description(b"\xe9" + bytes(0x1f) + desc)
        self.assertEqual(info["version"], "1.4.4")
        self.assertIsNone(sf.app_description(bytes(0x100)))


class Safety(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)

    def image(self, size, name="a.bin"):
        path = Path(self.scratch.name) / name
        path.write_bytes(b"\xe9" * size)
        return path

    def test_app_in_active_slot_passes(self):
        sf.check_regions([(0x20000, self.image(0x1000))], LIVE, None, 0, False)

    def test_merged_image_over_nvs_is_refused(self):
        regions = [(0x0, self.image(0x400000))]
        with self.assertRaisesRegex(sf.Refused, "nvs"):
            sf.check_regions(regions, LIVE, None, 0, False)
        sf.check_regions(regions, LIVE, None, 0, False, allow_nvs=True)

    def test_sector_rounding_touches_phy(self):
        with self.assertRaisesRegex(sf.Refused, "phy_init"):
            sf.check_regions([(0xE000, self.image(0x1001))], LIVE, None, 0, True)

    def test_inactive_slot_without_otadata_is_refused(self):
        regions = [(0x510000, self.image(0x1000))]
        with self.assertRaisesRegex(sf.Refused, "slot 0"):
            sf.check_regions(regions, LIVE, None, 0, False)
        sf.check_regions(regions, LIVE, None, 0, True)

    def test_oversized_app_is_refused(self):
        with self.assertRaisesRegex(sf.Refused, "exceeds"):
            sf.check_regions([(0x20000, self.image(0x4F0001))], LIVE, None, 0, True)

    def test_unaligned_and_overlap_are_refused(self):
        with self.assertRaisesRegex(sf.Refused, "aligned"):
            sf.check_regions([(0x20001, self.image(16))], LIVE, None, 0, True)
        pair = [(0x20000, self.image(0x2000)), (0x21000, self.image(0x1000, "b.bin"))]
        with self.assertRaisesRegex(sf.Refused, "overlaps"):
            sf.check_regions(pair, LIVE, None, 0, True)

    def test_table_that_moves_nvs_is_refused(self):
        moved = sf.parse_partitions(entry("nvs", 1, 2, 0x11000, 0x4000) + TABLE[32:])
        regions = [(0x20000, self.image(0x1000))]
        with self.assertRaisesRegex(sf.Refused, "moves"):
            sf.check_regions(regions, LIVE, moved, 0, True)
        sf.check_regions(regions, LIVE, moved, 0, True, allow_layout=True)

    def test_build_paths_cannot_leave_build_directory(self):
        build = Path(self.scratch.name)
        (build / "flasher_args.json").write_text(json.dumps({"flash_files": {"0x20000": "../outside.bin"}}))
        with self.assertRaisesRegex(sf.Refused, "outside"):
            sf.regions_from_build(build)


class Backups(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        patcher = mock.patch.object(sf, "BACKUPS", Path(scratch.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_by_name(self):
        folder = sf.BACKUPS / "stock"
        folder.mkdir()
        (folder / sf.MANIFEST_NAME).write_text(json.dumps({"sha256": "x", "mac": "y"}))
        self.assertEqual(sf.load_backup("stock")["image_path"], folder / sf.IMAGE_NAME)

    def test_names_cannot_leave_backups_folder(self):
        for name in ("../outside", ".hidden.partial", "a/b"):
            with self.assertRaisesRegex(sf.Refused, "Invalid"):
                sf.load_backup(name)

    def test_unknown_backup_is_refused(self):
        with self.assertRaisesRegex(sf.Refused, "No backup"):
            sf.load_backup("missing")


if __name__ == "__main__":
    unittest.main()
