#!/usr/bin/env python3
"""Flash, roll back, back up and inspect a StackChan (ESP32-S3, 16 MiB flash).

Anything that writes to flash is a dry run unless --execute is given.
Run with --help, or read ../SKILL.md for the reasoning behind each rule.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ESPTOOL_VERSION = "5.3.1"  # Version used by every recorded backup and restore.
FLASH_SIZE = 0x1000000
CHUNK = 0x100000
SECTOR = 0x1000
STATE_OFFSET, STATE_SIZE = 0x8000, 0x8000  # Partition table, NVS, otadata, phy.
READ_TIMEOUT = 180
WRITE_TIMEOUT = 600

BACKUPS = Path(__file__).resolve().parents[1] / "backups"  # Fixed location, gitignored.
BASELINE_FILE = BACKUPS / "baseline.json"
IMAGE_NAME = "firmware.bin"
MANIFEST_NAME = "manifest.json"

SUBTYPE_OTA_DATA, SUBTYPE_PHY, SUBTYPE_NVS = 0x00, 0x01, 0x02
TYPE_APP, TYPE_DATA = 0x00, 0x01
SUBTYPE_OTA_0 = 0x10


class Refused(Exception):
    """A safety check failed, so nothing was written."""


# ---------------------------------------------------------------- pure parsing


def parse_partitions(blob: bytes) -> list[dict]:
    """Parse an ESP-IDF partition table: 32-byte entries, magic bytes AA 50."""
    parts = []
    for pos in range(0, len(blob) - 31, 32):
        entry = blob[pos : pos + 32]
        if entry[:2] != b"\xaa\x50":
            break
        kind, subtype, offset, size = struct.unpack("<BBII", entry[2:12])
        label = entry[12:28].split(b"\0")[0].decode("ascii", "replace")
        parts.append({"label": label, "type": kind, "subtype": subtype, "offset": offset, "size": size})
    return parts


def find_part(parts: list[dict], kind: int, subtype: int) -> dict | None:
    return next((p for p in parts if p["type"] == kind and p["subtype"] == subtype), None)


def protected_ranges(parts: list[dict]) -> list[tuple[str, int, int]]:
    """Regions holding Wi-Fi, servo calibration and RF data for this unit."""
    found = []
    for name, subtype in (("nvs", SUBTYPE_NVS), ("phy_init", SUBTYPE_PHY)):
        part = find_part(parts, TYPE_DATA, subtype)
        if part:
            found.append((name, part["offset"], part["offset"] + part["size"]))
    return found


def active_ota_slot(otadata: bytes, slot_count: int) -> int:
    """Return the OTA slot the bootloader picks. Blank otadata means slot 0."""
    best = None
    for pos in (0, 0x1000):
        entry = otadata[pos : pos + 32]
        if len(entry) < 32:
            continue
        seq, state, crc = struct.unpack("<I20xII", entry)
        if seq == 0xFFFFFFFF or state in (3, 4):  # Blank, invalid or aborted.
            continue
        if zlib.crc32(entry[:4], 0xFFFFFFFF) != crc:
            continue
        best = seq if best is None else max(best, seq)
    return 0 if best is None or slot_count == 0 else (best - 1) % slot_count


def app_description(blob: bytes) -> dict | None:
    """Read esp_app_desc_t, which sits 0x20 bytes into an app image."""
    if len(blob) < 0x20 + 112 or blob[0] != 0xE9:
        return None
    desc = blob[0x20:]
    if struct.unpack("<I", desc[:4])[0] != 0xABCD5432:
        return None
    text = lambda a, b: desc[a:b].split(b"\0")[0].decode("ascii", "replace")
    return {"version": text(16, 48), "project": text(48, 80), "built": f"{text(96, 112)} {text(80, 96)}"}


def sector_span(offset: int, size: int) -> tuple[int, int]:
    return offset // SECTOR * SECTOR, (offset + size + SECTOR - 1) // SECTOR * SECTOR


def check_regions(
    regions: list[tuple[int, Path]],
    live: list[dict],
    new_table: list[dict] | None,
    active_slot: int | None,
    writes_otadata: bool,
    allow_nvs: bool = False,
    allow_layout: bool = False,
    allow_inactive: bool = False,
) -> None:
    """Refuse any write plan that could lose this unit's data or fail to boot."""
    table = new_table or live
    if new_table is not None:
        before, after = protected_ranges(live), protected_ranges(new_table)
        if live and before != after and not allow_layout:
            raise Refused(f"The new table moves NVS/phy ({before} -> {after}). Wi-Fi and calibration would be lost.")
    occupied: list[tuple[int, int]] = []
    for offset, path in sorted(regions):
        size = path.stat().st_size
        if size == 0:
            raise Refused(f"{path} is empty")
        low, high = sector_span(offset, size)
        if offset % SECTOR:
            raise Refused(f"Offset {offset:#x} is not aligned to a 4 KiB sector")
        if high > FLASH_SIZE:
            raise Refused(f"{path} does not fit in 16 MiB at {offset:#x}")
        if any(low < end and high > start for start, end in occupied):
            raise Refused(f"{path} overlaps another image")
        occupied.append((low, high))
        for name, start, end in protected_ranges(table):
            if low < end and high > start and not allow_nvs:
                raise Refused(
                    f"{path.name} at {offset:#x} would erase {name} ({start:#x}-{end:#x}): Wi-Fi, servo "
                    "calibration and RF data. A merged image does this even where it holds no data."
                )
        for part in table:
            if part["type"] == TYPE_APP and part["offset"] == offset and size > part["size"]:
                raise Refused(f"{path.name} ({size} B) exceeds partition {part['label']} ({part['size']} B)")
    if active_slot is None or writes_otadata:
        return
    slots = sorted(
        (p for p in table if p["type"] == TYPE_APP and p["subtype"] >= SUBTYPE_OTA_0), key=lambda p: p["subtype"]
    )
    for offset, path in regions:
        for index, part in enumerate(slots):
            if part["offset"] == offset and index != active_slot and not allow_inactive:
                raise Refused(
                    f"{path.name} goes to {part['label']} but the bootloader selects slot {active_slot}, so the "
                    "new app would never run. Include otadata or target the active slot."
                )


def regions_from_build(build: Path) -> tuple[list[tuple[int, Path]], dict, Path | None]:
    """Read the flash plan that ESP-IDF generated, keeping files inside the build."""
    args = json.loads((build / "flasher_args.json").read_text())
    root = build.resolve()
    regions = []
    table = None
    for offset, name in args["flash_files"].items():
        path = (build / name).resolve()
        if not path.is_relative_to(root):
            raise Refused(f"{name} is outside the build directory")
        regions.append((int(offset, 16), path))
        if int(offset, 16) == STATE_OFFSET:
            table = path
    return regions, args.get("flash_settings", {}), table


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- device access


def esptool(port: str, *args: str, before="default-reset", after="no-reset", stub=True, baud=None, timeout=READ_TIMEOUT):
    command = ["uvx", "--from", f"esptool=={ESPTOOL_VERSION}", "esptool", "--chip", "esp32s3", "--port", port]
    command += ["--before", before, "--after", after]
    if baud:
        command += ["--baud", str(baud)]
    if not stub:
        command.append("--no-stub")
    command += list(args)
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise Refused(f"esptool timed out after {timeout}s: {' '.join(args[:2])}") from error
    if done.returncode:
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
        raise Refused(f"esptool failed: {' '.join(args[:2])}\n{tail}")
    return done.stdout


def check_port(port: str) -> None:
    if not os.path.exists(port):
        raise Refused(
            f"{port} does not exist. In an admin PowerShell run: usbipd list, then usbipd attach --wsl --busid "
            "<BUSID of 303a:1001>. If it is still missing, hold RST about 3 s until the green LED lights, release, retry."
        )
    if shutil.which("fuser"):
        holder = subprocess.run(["fuser", port], capture_output=True, text=True)
        if holder.stdout.strip():
            raise Refused(f"{port} is in use by PID {holder.stdout.strip()}. Close the monitor or other flasher.")


def read_region(port: str, offset: int, size: int, **options) -> bytes:
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "region.bin"
        esptool(port, "read-flash", hex(offset), hex(size), str(target), **options)
        return target.read_bytes()


def identify(port: str) -> dict:
    """Connect, leaving the chip in the bootloader for the next step."""
    output = esptool(port, "flash-id")
    mac = re.search(r"MAC:\s*([0-9a-fA-F:]{17})", output)
    size = re.search(r"Detected flash size:\s*(\S+)", output)
    security = esptool(port, "get-security-info", before="no-reset")
    locked = "Disabled" not in re.search(r"Flash Encryption:\s*(\S+)", security).group(0) or (
        "Disabled" not in re.search(r"Secure Boot:\s*(\S+)", security).group(0)
    )
    return {"mac": mac.group(1).lower() if mac else None, "flash": size.group(1) if size else None, "locked": locked}


def read_state(port: str) -> dict:
    blob = read_region(port, STATE_OFFSET, STATE_SIZE, before="no-reset")
    parts = parse_partitions(blob[:0xC00])
    otadata = find_part(parts, TYPE_DATA, SUBTYPE_OTA_DATA)
    slots = [p for p in parts if p["type"] == TYPE_APP and p["subtype"] >= SUBTYPE_OTA_0]
    slots.sort(key=lambda p: p["subtype"])
    active = None
    if otadata and slots:
        start = otadata["offset"] - STATE_OFFSET
        active = active_ota_slot(blob[start : start + otadata["size"]], len(slots))
    return {"blob": blob, "parts": parts, "slots": slots, "active": active}


def preflight(port: str) -> tuple[dict, dict]:
    check_port(port)
    device = identify(port)
    if device["flash"] != "16MB":
        raise Refused(f"Expected 16 MB of flash, found {device['flash']}")
    if device["locked"]:
        raise Refused("Secure boot or flash encryption is on. Raw images cannot be restored safely.")
    return device, read_state(port)


def save_snapshot(blob: bytes) -> Path:
    """Save table, NVS and otadata before writing. Holds secrets, so owner-only."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = BACKUPS / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    target = folder / f"state-{stamp}.bin"
    target.write_bytes(blob)
    os.chmod(target, 0o600)
    return target


def verify_chunks(port: str, image: Path) -> list[int]:
    """Compare a full image to flash per MiB. Returns offsets that differ."""
    data = image.read_bytes()
    different = []
    with tempfile.TemporaryDirectory() as scratch:
        for index in range(len(data) // CHUNK):
            offset = index * CHUNK
            piece = Path(scratch) / f"{index:02d}.bin"
            piece.write_bytes(data[offset : offset + CHUNK])
            before = "default-reset" if index == 0 else "no-reset"
            try:
                esptool(port, "verify-flash", hex(offset), str(piece), before=before)
                print(f"  {offset:#010x} matches")
            except Refused:
                print(f"  {offset:#010x} DIFFERS")
                different.append(offset)
    return different


def release(port: str, reset: bool) -> None:
    """Leave the bootloader. A manual reset is the recorded reliable path."""
    if reset:
        esptool(port, "flash-id", before="no-reset", after="hard-reset")
        print("Hard reset sent. If the screen stays frozen after 10 s, press the physical reset once.")
    else:
        print("The robot is still in the bootloader. Press its physical reset to boot.")


def baseline_name() -> str:
    if not BASELINE_FILE.exists():
        raise Refused(f"{BASELINE_FILE} is missing. See 'Baseline' in SKILL.md.")
    return json.loads(BASELINE_FILE.read_text())["backup"]


def load_backup(name: str) -> dict:
    """Load a backup by folder name from the fixed backups location."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
        raise Refused(f"Invalid backup name: {name}")
    folder = BACKUPS / name
    if not (folder / MANIFEST_NAME).is_file():
        raise Refused(f"No backup named {name} in {BACKUPS}")
    manifest = json.loads((folder / MANIFEST_NAME).read_text())
    manifest["name"] = name
    manifest["image_path"] = folder / IMAGE_NAME
    return manifest


def active_app(port: str, state: dict) -> dict | None:
    if not state["slots"] or state["active"] is None:
        return None
    slot = state["slots"][state["active"]]
    return app_description(read_region(port, slot["offset"], 0x100, before="no-reset"))


# ---------------------------------------------------------------- commands


def cmd_status(args) -> int:
    check_port(args.port)
    device = identify(args.port)
    state = read_state(args.port)
    print(f"MAC: {device['mac']}  flash: {device['flash']}  secure boot/encryption: {'ON' if device['locked'] else 'off'}")
    for part in state["parts"]:
        print(f"  {part['label']:<10} {part['offset']:#010x} {part['size']:#010x}")
    if state["slots"] and state["active"] is not None:
        label = state["slots"][state["active"]]["label"]
        print(f"Active app: {label} -> {active_app(args.port, state) or 'no valid image'}")
    if BASELINE_FILE.exists():
        print(f"Baseline: {baseline_name()}")
    esptool(args.port, "flash-id", before="no-reset", after="hard-reset")
    return 0


def restore_backup(args, name: str) -> int:
    """Load a full backup. Rollback is this same operation on the baseline."""
    backup = load_backup(name)
    image = backup["image_path"]
    if not image.is_file() or image.stat().st_size != FLASH_SIZE:
        raise Refused(f"{image} is not a full 16 MiB image")
    digest = sha256(image)
    if digest != backup["sha256"]:
        raise Refused(f"SHA-256 mismatch for {image}\n  expected {backup['sha256']}\n  actual   {digest}")
    device, state = preflight(args.port)
    if device["mac"] != backup["mac"]:
        raise Refused(f"This backup belongs to {backup['mac']}, but the device is {device['mac']}")
    print(f"Device {device['mac']}. Load backup {name} ({digest[:12]}), firmware: {backup.get('firmware')}.")
    print("This replaces the whole flash, including the current firmware and NVS.")
    if not args.execute:
        esptool(args.port, "flash-id", before="no-reset", after="hard-reset")
        print("Dry run: nothing was written. Repeat with --execute after confirmation.")
        return 0
    print(f"Pre-write state saved to {save_snapshot(state['blob'])}")
    esptool(args.port, "write-flash", "0x0", str(image), timeout=WRITE_TIMEOUT)
    print("Written. Verifying 16 chunks...")
    different = verify_chunks(args.port, image)
    if different:
        release(args.port, False)
        raise Refused(f"{len(different)} chunks differ after writing. Do not boot; load the backup again.")
    print("Verified: 16/16 chunks match.")
    release(args.port, args.reset)
    return 0


def cmd_flash(args) -> int:
    if args.backup:
        return restore_backup(args, args.backup)
    settings: dict = {}
    table_path = None
    if args.build:
        regions, settings, table_path = regions_from_build(Path(args.build))
    else:
        if args.offset is None:
            raise Refused("--image requires --offset")
        regions = [(int(args.offset, 16), Path(args.image).resolve())]
    for _, path in regions:
        if not path.is_file():
            raise Refused(f"Missing image: {path}")
    device, state = preflight(args.port)
    new_table = parse_partitions(table_path.read_bytes()) if table_path else None
    writes_otadata = any(
        o == p["offset"] for o, _ in regions for p in (new_table or state["parts"]) if p["subtype"] == SUBTYPE_OTA_DATA and p["type"] == TYPE_DATA
    )
    check_regions(
        regions, state["parts"], new_table, state["active"], writes_otadata,
        args.allow_nvs_overwrite, args.allow_layout_change, args.allow_inactive_slot,
    )
    print(f"Device {device['mac']}. Write plan:")
    for offset, path in sorted(regions):
        print(f"  {offset:#010x}  {path.stat().st_size:>9} B  {sha256(path)[:12]}  {path}")
    if not args.execute:
        esptool(args.port, "flash-id", before="no-reset", after="hard-reset")
        print("Dry run: nothing was written. Repeat with --execute after confirmation.")
        return 0
    print(f"Pre-write state saved to {save_snapshot(state['blob'])}")
    command = ["write-flash"]
    for option, key in (("--flash-mode", "flash_mode"), ("--flash-freq", "flash_freq"), ("--flash-size", "flash_size")):
        if key in settings:
            command += [option, settings[key]]
    for offset, path in sorted(regions):
        command += [hex(offset), str(path)]
    esptool(args.port, *command, baud=460800, timeout=WRITE_TIMEOUT)
    print("Written and verified by esptool's hash check.")
    table = new_table or state["parts"]
    after = read_region(args.port, STATE_OFFSET, STATE_SIZE, before="no-reset")
    for name, start, end in protected_ranges(table):
        same = after[start - STATE_OFFSET : end - STATE_OFFSET] == state["blob"][start - STATE_OFFSET : end - STATE_OFFSET]
        print(f"{name}: {'unchanged' if same else 'CHANGED'}")
        if not same and not args.allow_nvs_overwrite:
            release(args.port, False)
            raise Refused(f"{name} changed without authorization. Inspect the pre-write snapshot.")
    release(args.port, args.reset)
    return 0


def cmd_rollback(args) -> int:
    return restore_backup(args, baseline_name())


def cmd_verify(args) -> int:
    image = Path(args.image).resolve() if args.image else load_backup(args.backup or baseline_name())["image_path"]
    check_port(args.port)
    different = verify_chunks(args.port, image)
    esptool(args.port, "flash-id", before="no-reset", after="hard-reset")
    print(f"{16 - len(different)}/16 chunks match {image}")
    return 1 if different else 0


def cmd_backup(args) -> int:
    check_port(args.port)
    device = identify(args.port)
    state = read_state(args.port)
    app = active_app(args.port, state)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version = (app or {}).get("version", "unknown")
    name = f"{args.name or re.sub(r'[^A-Za-z0-9_.-]', '_', (app or {}).get('project', 'firmware') + '-' + version)}-{stamp}"
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
        raise Refused(f"Invalid backup name: {name}")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    partial = BACKUPS / f".{name}.partial"
    partial.mkdir(mode=0o700)
    image = partial / IMAGE_NAME
    with image.open("wb") as output:
        for index in range(FLASH_SIZE // CHUNK):
            offset = index * CHUNK
            piece = partial / f"chunk-{index:02d}.bin"
            # A full read stops near 1.38 MiB. The recorded working pattern is 1 MiB
            # per run at 115200, with the stub for the first chunk and ROM after it.
            # No reset between chunks: running firmware could change NVS mid-dump.
            esptool(args.port, "read-flash", hex(offset), hex(CHUNK), str(piece),
                    stub=index == 0, baud=115200)
            data = piece.read_bytes()
            if len(data) != CHUNK:
                raise Refused(f"Chunk {index} is {len(data)} B, expected {CHUNK}")
            output.write(data)
            piece.unlink()
            print(f"  read {index + 1}/16", flush=True)
    os.chmod(image, 0o600)
    print("Checking the dump against the flash...", flush=True)
    different = verify_chunks(args.port, image)
    if different:
        raise Refused(f"The dump differs from flash in {len(different)} chunks. Kept for inspection: {partial}")
    manifest = {"sha256": sha256(image), "mac": device["mac"], "firmware": app, "taken": stamp}
    (partial / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(partial / MANIFEST_NAME, 0o600)
    partial.rename(BACKUPS / name)
    esptool(args.port, "flash-id", before="no-reset", after="hard-reset")
    print(f"Backup {name}: 16/16 chunks verified, sha256 {manifest['sha256']}")
    return 0


def cmd_list(args) -> int:
    pinned = baseline_name() if BASELINE_FILE.exists() else None
    for folder in sorted(BACKUPS.glob("*")) if BACKUPS.exists() else []:
        if (folder / MANIFEST_NAME).is_file():
            info = load_backup(folder.name)
            mark = "  [baseline]" if folder.name == pinned else ""
            print(f"{folder.name}  {info['sha256'][:12]}  {info.get('firmware')}{mark}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=os.environ.get("STACKCHAN_PORT", "/dev/ttyACM0"))
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="Show identity, partitions and the active app").set_defaults(run=cmd_status)

    flash = commands.add_parser("flash", help="Load a firmware. NVS and phy are protected, except with --backup")
    source = flash.add_mutually_exclusive_group(required=True)
    source.add_argument("--build", help="ESP-IDF build directory containing flasher_args.json")
    source.add_argument("--image", help="A single binary; requires --offset")
    source.add_argument("--backup", help="A backup name from 'list'. Replaces the whole flash")
    flash.add_argument("--offset", help="Hex offset for --image, for example 0x20000")
    flash.add_argument("--allow-nvs-overwrite", action="store_true", help="Accept losing Wi-Fi and calibration")
    flash.add_argument("--allow-layout-change", action="store_true", help="Accept a table that moves NVS or phy")
    flash.add_argument("--allow-inactive-slot", action="store_true", help="Write an app to the slot that will not boot")
    flash.add_argument("--execute", action="store_true", help="Write. Without this, only print the plan")
    flash.add_argument("--reset", action="store_true", help="Reset through USB instead of asking for the button")
    flash.set_defaults(run=cmd_flash)

    rollback = commands.add_parser("rollback", help="Load the pinned baseline (same as flash --backup)")
    rollback.add_argument("--execute", action="store_true", help="Write. Without this, only check")
    rollback.add_argument("--reset", action="store_true", help="Reset through USB instead of asking for the button")
    rollback.set_defaults(run=cmd_rollback)

    backup = commands.add_parser("backup", help="Dump the whole flash into the backups folder")
    backup.add_argument("--name", help="Name prefix. Defaults to the running firmware and version")
    backup.set_defaults(run=cmd_backup)

    commands.add_parser("list", help="List backups").set_defaults(run=cmd_list)

    verify = commands.add_parser("verify", help="Compare flash with a backup, read-only")
    target = verify.add_mutually_exclusive_group()
    target.add_argument("--backup", help="Backup name. Defaults to the baseline")
    target.add_argument("--image", help="Any full image file")
    verify.set_defaults(run=cmd_verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.run(args)
    except Refused as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
