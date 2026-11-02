---
name: stackchan-build
description: Build the project's firmware into a dated image in dist/firmware/, ready for the stackchan-flash skill. Use when the task involves producing, naming, or finding a firmware image for the robot. This skill never writes to the robot.
---

# StackChan build

Run from the repository root:

```bash
make firmware-image PURPOSE="keep open conversation"
```

To use the Xiaozhi cloud server instead of a local one, add `XIAOZHI_CLOUD=1`. Only do this when the user asks, because voice then leaves this machine. The firmware language defaults to `es-ES`; change it with `LANGUAGE=en-US`.

Write `PURPOSE` as two to five words describing what this build is for. Without it, the firmware branch name is used.

The image is stored in `dist/firmware/<YYYYMMDD-HHMMSS>-<purpose>-<commit>/`, and the command prints its path. A `-dirty` suffix means the firmware had uncommitted changes, so the commit alone cannot rebuild it.

## Requirements

- `make setup` has run.
- Unless `XIAOZHI_CLOUD=1` is set, `config/firmware.local.env` exists, created from `config/firmware.local.env.example`. The build stops without it, so an image cannot silently point to the Xiaozhi cloud.
- The address in that file is reachable from the robot. Ask the user if it is missing. Never copy it to a tracked file.

## What an image contains

- The files named by `flasher_args.json`, plus `sdkconfig`.
- `manifest.json`: purpose, date, board, commits, ESP-IDF version, server URL, and hashes.

`merged-binary.bin` is excluded on purpose, because writing it erases NVS.

## Next step

Load an image only through the `stackchan-flash` skill and follow its confirmation workflow:

```bash
python3 .claude/skills/stackchan-flash/scripts/stackchan_flash.py flash --build dist/firmware/<image>
```

Without `--execute`, this is a dry run. A successful build does not validate hardware, so report what was tested on the robot and what was not.
