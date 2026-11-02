# Tested versions

Tested on 2026-11-01 under WSL2 with Ubuntu 26.04. Nothing below has been run on the robot.

| Component | Version |
|---|---|
| ESP-IDF | v6.1, `xtensa-esp-elf` esp-15.2.0_20251204, Python 3.12 |
| Xiaozhi firmware | `4632dc5`, board `m5stack/core-s3`, variant `m5stack-core-s3` |
| `xiaozhi-esp32-server` | `4745186` plus `requirements-dev.txt`, Python 3.10 |
| Hermes | Not integrated yet |

## Results

| Check | Result |
|---|---|
| `make test` | All suites pass; the server reports 45 passed and 2 skipped |
| `make firmware` | Builds |
| GitHub Actions on `main` | `tools`, `server`, and `firmware` jobs pass |
| `make server-run` with `config/server.local.yaml.template` | Starts and initializes LLM, VAD, and STT providers |

## Not tested

- A conversation through the local LLM, STT, and TTS services. These services were not running during the startup test.
- The TTS provider, which the server did not report initializing at startup.
- The firmware on the robot.
