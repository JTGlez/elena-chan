# Tested versions

Tested on 2026-11-02 under WSL2 with Ubuntu 26.04.

| Component | Version |
|---|---|
| ESP-IDF | v6.1 (`fff9895`), `xtensa-esp-elf` esp-15.2.0_20251204, Python 3.12 |
| Xiaozhi firmware | `4632dc5` plus our branch `feature/elena-pc-recording` at `743f631` and `dependencies.lock`, board and variant `elena-stackchan` |
| `xiaozhi-esp32-server` | `4745186` plus `requirements-dev.txt` and `requirements.lock`, Python 3.10 |
| Hermes | Not integrated yet |

The firmware environment for host tests and tools is `.venv-firmware`: Python 3.12 and `tools/requirements-firmware.lock`.

## Results

| Check | Result |
|---|---|
| `make test` | All suites pass; the server reports 48 passed and 2 skipped |
| `make firmware` | Builds |
| GitHub Actions on `main` | `tools`, `server`, and `firmware` jobs pass |
| `make server-run` with `config/server.local.yaml.template` | Starts and initializes LLM, VAD, and STT providers |

## Built and host-tested, not run on the robot

Branch `feature/elena-avatar-stage` of the firmware at `7ab1f70`, with the avatar stage and the boot screen. `make test`, `make check-locks` and `make firmware BOARD=m5stack/core-s3 BOARD_VARIANT=m5stack-core-s3` pass, and images build for `AVATAR=elena` and `AVATAR=emoji`. `ProbeServer` and the changes in `main/application.cc` have no host test. The checks on the robot are in Task 6 of `docs/superpowers/plans/2026-11-02-avatar-stage.md`.

## On the robot

Board `elena-stackchan` with `SCREEN_STREAM=1`, from the sources of `743f631` before they were committed, except for example addresses in a host test — `dist/firmware/20261102-134814-pc-recording-lease-fix-fe366d4-dirty`. Measured in `docs/screen-stream.md`. The same sources built with `AVATAR=emoji` have not run on the robot.

Board `elena-stackchan` at `fe366d4` — `dist/firmware/20261102-041708-elena-stronger-reds-fe366d4`, written through the `stackchan-flash` skill with its pre-write snapshot kept — using `server/` with MiniMax for STT, LLM, and TTS: it booted, connected, and recognized Spanish speech, and the user judged the avatar on the screen. Details and what was not checked are in [avatar-validation.md](avatar-validation.md).

Earlier, image `base-xiaozhi-cloud` at `4632dc5`, in Spanish, using the Xiaozhi cloud server, not `server/`.

- Boots, connects to the cloud, and holds a spoken conversation, as observed by the user.
- NVS and phy were unchanged after the write.
- Not checked: camera and touch. This board has no head servo code.

## Not tested

- A conversation through the local LLM, STT, and TTS services. These services were not running during the startup test.
- The TTS provider, which the server did not report initializing at startup.
