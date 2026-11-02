#!/usr/bin/env bash
# Report missing system requirements. Installs nothing, because apt needs sudo.
set -u

missing=0

for tool in git make uv ffmpeg; do
    if ! command -v "$tool" >/dev/null; then
        echo "missing command: $tool"
        missing=1
    fi
done

for library in libopus.so.0 libusb-1.0.so.0; do
    if ! ldconfig -p | grep -q "$library"; then
        echo "missing library: $library"
        missing=1
    fi
done

if [ "$missing" -ne 0 ]; then
    echo "On Ubuntu: sudo apt install make ffmpeg libopus0 libusb-1.0-0"
    echo "Install uv from https://docs.astral.sh/uv/"
    exit 1
fi

echo "System requirements found."
