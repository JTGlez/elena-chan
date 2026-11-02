#!/usr/bin/env bash
# Install ESP-IDF v6.1 beside any other ESP-IDF version. Safe to run again.
set -euo pipefail

IDF_VERSION=v6.1
IDF_COMMIT=fff9895c82d744c7237be8847347bdd1b07c6643
IDF_DIR="${ESP_IDF_DIR:-$HOME/esp/esp-idf-v6.1}"
SHIM_DIR="$HOME/esp/bin"

uv python install --quiet 3.12
PYTHON_312="$(uv python find 3.12)"
mkdir -p "$SHIM_DIR"
ln -sf "$PYTHON_312" "$SHIM_DIR/python3"
ln -sf "$PYTHON_312" "$SHIM_DIR/python"

if [ ! -d "$IDF_DIR/.git" ]; then
    git clone -q -b "$IDF_VERSION" --depth 1 --recursive --shallow-submodules \
        https://github.com/espressif/esp-idf.git "$IDF_DIR"
fi

actual="$(git -C "$IDF_DIR" rev-parse HEAD)"
if [ "$actual" != "$IDF_COMMIT" ]; then
    echo "error: $IDF_DIR is at $actual, expected $IDF_VERSION ($IDF_COMMIT)" >&2
    exit 1
fi

PATH="$SHIM_DIR:$PATH" "$IDF_DIR/install.sh" esp32s3
