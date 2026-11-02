#!/usr/bin/env python3
"""Build the firmware and store an image in dist/firmware/ for stackchan-flash.

Run inside the ESP-IDF environment: `make firmware-image PURPOSE="..."`.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
DIST = ROOT / "dist" / "firmware"
ENV_FILE = ROOT / "config" / "firmware.local.env"
BOARD = "m5stack/core-s3"
VARIANT = "m5stack-core-s3"


def slugify(text: str) -> str:
    """Return a short lowercase identifier for a build's purpose."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40].strip("-") or "build"


def image_name(created: datetime, purpose: str, commit: str, dirty: bool) -> str:
    stamp = created.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(purpose)}-{commit}{'-dirty' if dirty else ''}"


def read_ota_url(env_file: Path) -> str:
    if not env_file.exists():
        raise SystemExit(f"{env_file} not found. Copy it from {env_file.name}.example.")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key == "OTA_URL":
            url = value.strip().strip("\"'")
            if re.fullmatch(r"https?://[^\s\"]+", url) and "YOUR_SERVER_ADDRESS" not in url:
                return url
            raise SystemExit(f"OTA_URL in {env_file} is not a usable URL.")
    raise SystemExit(f"OTA_URL is not set in {env_file}.")


def set_ota_url(sdkconfig: Path, url: str) -> None:
    text, count = re.subn(
        r'^CONFIG_OTA_URL=".*"$',
        lambda _: f'CONFIG_OTA_URL="{url}"',
        sdkconfig.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if count != 1:
        raise SystemExit(f"Expected one CONFIG_OTA_URL in {sdkconfig}, found {count}.")
    sdkconfig.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def store_image(build: Path, sdkconfig: Path, destination: Path, manifest: dict) -> None:
    """Copy only what flasher_args.json names, so merged images stay out."""
    flash_files = json.loads((build / "flasher_args.json").read_text(encoding="utf-8"))["flash_files"]
    partial = destination.with_name(destination.name + ".partial")
    partial.mkdir(parents=True)
    hashes = {}
    for relative in flash_files.values():
        target = partial / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / relative, target)
        hashes[relative] = sha256(target)
    shutil.copy2(build / "flasher_args.json", partial / "flasher_args.json")
    shutil.copy2(sdkconfig, partial / "sdkconfig")
    content = {**manifest, "name": destination.name, "flash_files": flash_files, "sha256": hashes}
    (partial / "manifest.json").write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    partial.rename(destination)


def git(*args: str, cwd: Path = FIRMWARE) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def run(*command: str) -> None:
    subprocess.run(command, cwd=FIRMWARE, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", default="", help="Short purpose; defaults to the firmware branch")
    args = parser.parse_args()

    if "IDF_PATH" not in os.environ:
        raise SystemExit("ESP-IDF is not active. Use `make firmware-image`.")
    url = read_ota_url(Path(os.environ.get("FIRMWARE_ENV_FILE", ENV_FILE)))

    branch = git("branch", "--show-current")
    purpose = args.purpose or branch or "build"
    dirty = bool(git("status", "--porcelain"))
    commit = git("rev-parse", "--short=7", "HEAD")

    run(sys.executable, "scripts/build.py", BOARD, "--name", VARIANT)
    sdkconfig = FIRMWARE / "sdkconfig"
    set_ota_url(sdkconfig, url)
    run("idf.py", "build")

    build = FIRMWARE / "build"
    if url.encode() not in (build / "xiaozhi.bin").read_bytes():
        raise SystemExit("The built app does not contain OTA_URL.")

    created = datetime.now(timezone.utc).astimezone()
    destination = DIST / image_name(created, purpose, commit, dirty)
    store_image(build, sdkconfig, destination, {
        "purpose": purpose,
        "created_at": created.isoformat(timespec="seconds"),
        "board": BOARD,
        "variant": VARIANT,
        "idf_version": os.environ.get("ESP_IDF_VERSION", ""),
        "firmware_commit": git("rev-parse", "HEAD"),
        "firmware_dirty": dirty,
        "repository_commit": git("rev-parse", "HEAD", cwd=ROOT),
        "ota_url": url,
    })
    print(f"Image: {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
