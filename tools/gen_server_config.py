#!/usr/bin/env python3
"""Generate the server's gitignored config from a template and local secrets."""

import argparse
import os
import secrets
import sys
import time
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from build_firmware_image import ENV_FILE as FIRMWARE_ENV_FILE
from build_firmware_image import read_ota_url

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "server" / "main" / "xiaozhi-server" / "data" / ".config.yaml"
WEBSOCKET_PORT = 8000
HTTP_PORT = 8003


class Profile(NamedTuple):
    template: Path
    env_file: Path
    key_name: str


PROFILES = {
    "local": Profile(
        ROOT / "config" / "server.local.yaml.template",
        ROOT / ".localworker" / "qwen-flash-worker" / ".env",
        "BASALT_API_KEY",
    ),
    "minimax": Profile(
        ROOT / "config" / "server.minimax.yaml.template",
        ROOT / "config" / "minimax.env",
        "MINIMAX_API_KEY",
    ),
}


def read_env_value(env_file: Path, name: str) -> str:
    """Return a variable's value from a .env file, or an empty string."""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key == name:
            return value.strip().strip("\"'")
    return ""


def timezone_offset_hours() -> str:
    """Return this machine's UTC offset in hours, as the server's OTA reply expects."""
    return f"{time.localtime().tm_gmtoff / 3600:g}"


def server_host(firmware_env: Path) -> str:
    """Return the address the robot uses, so the firmware and the server agree on it."""
    host = urlsplit(read_ota_url(firmware_env)).hostname
    if not host:
        raise SystemExit(f"OTA_URL in {firmware_env} has no host.")
    return f"[{host}]" if ":" in host else host


def generate(
    template: Path,
    env_file: Path,
    output: Path,
    force: bool = False,
    key_name: str = "BASALT_API_KEY",
    firmware_env: Optional[Path] = None,
) -> None:
    if output.exists() and not force:
        raise SystemExit(f"{output} already exists. Use FORCE=1 to replace it.")
    if not env_file.exists():
        raise SystemExit(f"{env_file} not found.")
    key = read_env_value(env_file, key_name)
    if not key:
        raise SystemExit(f"{key_name} is not set in {env_file}.")

    content = template.read_text(encoding="utf-8").replace(f"@{key_name}@", key)
    if "@WEBSOCKET_URL@" in content or "@VISION_URL@" in content:
        host = server_host(firmware_env or FIRMWARE_ENV_FILE)
        content = content.replace(
            "@WEBSOCKET_URL@", f"ws://{host}:{WEBSOCKET_PORT}/xiaozhi/v1/"
        ).replace("@VISION_URL@", f"http://{host}:{HTTP_PORT}/mcp/vision/explain")
    content = content.replace("@TIMEZONE_OFFSET@", timezone_offset_hours())
    # A fixed secret keeps the robot's token valid when the server restarts.
    content = content.replace("@AUTH_KEY@", secrets.token_hex(32))

    output.parent.mkdir(parents=True, exist_ok=True)
    # The file holds a key, so create it readable only by its owner.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(content)
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="local")
    parser.add_argument("--force", action="store_true", help="replace an existing config")
    args = parser.parse_args()
    profile = PROFILES[args.profile]
    generate(profile.template, profile.env_file, OUTPUT, args.force, profile.key_name)
    print(f"Wrote {OUTPUT} using the {args.profile} profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
