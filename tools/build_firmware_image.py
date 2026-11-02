#!/usr/bin/env python3
"""Build the firmware and store an image in dist/firmware/ for stackchan-flash.

Run inside the ESP-IDF environment: `make firmware-image PURPOSE="..."`.
"""

import argparse
import hashlib
import runpy
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
BOARD = "elena-stackchan"
VARIANT = "elena-stackchan"


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


def store_image(build: Path, sdkconfig: Path, lock: Path, destination: Path, manifest: dict) -> None:
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
    shutil.copy2(lock, partial / "dependencies.lock")
    hashes["dependencies.lock"] = sha256(lock)
    content = {**manifest, "name": destination.name, "flash_files": flash_files, "sha256": hashes}
    (partial / "manifest.json").write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    partial.rename(destination)


def flash_app(build: Path) -> Path:
    """The application named by flasher_args.json."""
    args = json.loads((build / "flasher_args.json").read_text(encoding="utf-8"))
    return build / args["flash_files"]["0x20000"]


def check_language(app: Path, language: str) -> None:
    """A stale generated header once shipped Chinese strings in an es-ES image."""
    locale = FIRMWARE / "main" / "assets" / "locales" / language / "language.json"
    expected = json.loads(locale.read_text(encoding="utf-8"))["strings"]["LISTENING"]
    if expected.encode() not in app.read_bytes():
        raise SystemExit(f"The built app does not contain the {language} strings; rebuild it.")


def git(*args: str, cwd: Path = FIRMWARE) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def run(*command: str) -> None:
    subprocess.run(command, cwd=FIRMWARE, check=True)


def source_fingerprint(directory: Path) -> str:
    """Identify the delivered working tree, including uncommitted new sources."""
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=directory, check=True, capture_output=True)
    digest = hashlib.sha256()
    for name in sorted(set(result.stdout.split(b"\0")) - {b""}):
        path = directory / os.fsdecode(name)
        digest.update(name + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"missing")
    return digest.hexdigest()


def resolve_selection(board: str, variant: str) -> str:
    """Validate against the same board registry that the canonical builder uses."""
    if not re.fullmatch(r"[a-z0-9./-]+", board) or ".." in board.split("/"):
        raise ValueError("Invalid board directory")
    builder = runpy.run_path(str(FIRMWARE / "scripts" / "build.py"))
    variants = builder["_collect_variants"](idf_version=(6, 1, 0))
    if not any(v["board"] == board and v["name"] == variant for v in variants):
        raise ValueError(f"Unknown board/variant: {board} / {variant}")
    config = json.loads((FIRMWARE / "main" / "boards" / board / "config.json").read_text())
    preset = next(b for b in builder["_get_builds_for_idf"](config, (6, 1, 0)) if b["name"] == variant)
    return builder["_resolve_board_config"](board, config["target"], preset.get("sdkconfig_append", []), variant_name=variant)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", default="", help="Short purpose; defaults to the firmware branch")
    parser.add_argument("--language", default="es-ES", help="Firmware language locale")
    parser.add_argument("--xiaozhi-cloud", action="store_true", help="Keep the default Xiaozhi cloud server")
    parser.add_argument("--board", default=BOARD, help="Board directory")
    parser.add_argument("--variant", default=VARIANT, help="Board variant name")
    args = parser.parse_args()
    try:
        board_symbol = resolve_selection(args.board, args.variant)
    except ValueError as error:
        parser.error(str(error))

    if "IDF_PATH" not in os.environ:
        raise SystemExit("ESP-IDF is not active. Use `make firmware-image`.")
    url = None if args.xiaozhi_cloud else read_ota_url(Path(os.environ.get("FIRMWARE_ENV_FILE", ENV_FILE)))

    branch = git("branch", "--show-current")
    purpose = args.purpose or branch or "build"
    dirty = bool(git("status", "--porcelain"))
    commit = git("rev-parse", "--short=7", "HEAD")

    source_sha256 = source_fingerprint(FIRMWARE)
    run(sys.executable, "scripts/build.py", args.board, "--name", args.variant, "--language", args.language)
    sdkconfig = FIRMWARE / "sdkconfig"
    if f"{board_symbol}=y" not in sdkconfig.read_text(encoding="utf-8").splitlines():
        raise SystemExit("The configured board does not match the requested image.")
    if url:
        set_ota_url(sdkconfig, url)
        run("idf.py", "build")
    build = FIRMWARE / "build"
    if url and url.encode() not in (build / "xiaozhi.bin").read_bytes():
        raise SystemExit("The built app does not contain OTA_URL.")
    check_language(flash_app(build), args.language)
    match = re.search(r'^CONFIG_OTA_URL="(.*)"$', sdkconfig.read_text(encoding="utf-8"), re.MULTILINE)

    if source_fingerprint(FIRMWARE) != source_sha256:
        raise SystemExit("Firmware sources changed during the build; rebuild before packaging.")
    created = datetime.now(timezone.utc).astimezone()
    destination = DIST / image_name(created, purpose, commit, dirty)
    store_image(build, sdkconfig, FIRMWARE / "dependencies.lock", destination, {
        "purpose": purpose,
        "created_at": created.isoformat(timespec="seconds"),
        "board": args.board,
        "variant": args.variant,
        "idf_version": os.environ.get("ESP_IDF_VERSION", ""),
        "firmware_commit": git("rev-parse", "HEAD"),
        "firmware_dirty": dirty,
        "firmware_source_sha256": source_sha256,
        "repository_commit": git("rev-parse", "HEAD", cwd=ROOT),
        "language": args.language,
        "ota_url": match.group(1) if match else "",
    })
    print(f"Image: {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
