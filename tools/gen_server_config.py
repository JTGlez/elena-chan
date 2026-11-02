#!/usr/bin/env python3
"""Generate the server's gitignored config from the template and local secrets."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "config" / "server.local.yaml.template"
OUTPUT = ROOT / "server" / "main" / "xiaozhi-server" / "data" / ".config.yaml"
ENV_FILE = ROOT / ".localworker" / "qwen-flash-worker" / ".env"
KEY_NAME = "BASALT_API_KEY"
PLACEHOLDER = "@BASALT_API_KEY@"


def read_env_value(env_file: Path, name: str) -> str:
    """Return a variable's value from a .env file, or an empty string."""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key == name:
            return value.strip().strip("\"'")
    return ""


def generate(template: Path, env_file: Path, output: Path, force: bool = False) -> None:
    if output.exists() and not force:
        raise SystemExit(f"{output} already exists. Use FORCE=1 to replace it.")
    if not env_file.exists():
        raise SystemExit(f"{env_file} not found.")
    key = read_env_value(env_file, KEY_NAME)
    if not key:
        raise SystemExit(f"{KEY_NAME} is not set in {env_file}.")

    content = template.read_text(encoding="utf-8").replace(PLACEHOLDER, key)
    output.parent.mkdir(parents=True, exist_ok=True)
    # The file holds a key, so create it readable only by its owner.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(content)
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace an existing config")
    args = parser.parse_args()
    generate(TEMPLATE, ENV_FILE, OUTPUT, args.force)
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
