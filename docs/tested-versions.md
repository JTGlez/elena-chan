# Tested versions

Tested on 2026-11-02 under WSL2 with Ubuntu 26.04.

| Component | Version |
|---|---|
| ESP-IDF | v6.1 (`fff9895`), `xtensa-esp-elf` esp-15.2.0_20251204, Python 3.12 |
| Xiaozhi firmware | `4632dc5` plus our branch `feature/elena-avatar` at `fe366d4` and `dependencies.lock`, board and variant `elena-stackchan` |
| `xiaozhi-esp32-server` | `4745186` plus `requirements-dev.txt` and `requirements.lock`, Python 3.10 |
| Hermes | Not integrated yet |

## Results

| Check | Result |
|---|---|
| `make test` | All suites pass; the server reports 48 passed and 2 skipped |
| `make firmware` | Builds |
| GitHub Actions on `main` | `tools`, `server`, and `firmware` jobs pass |
| `make server-run` with `config/server.local.yaml.template` | Starts and initializes LLM, VAD, and STT providers |

## On the robot

Board `elena-stackchan`, using `server/` with MiniMax for STT, LLM, and TTS: boots, holds a spoken conversation, and shows the avatar. The robot runs the build at `4e577d6`; the image at `fe366d4`, `dist/firmware/20261102-041708-elena-stronger-reds-fe366d4`, is built and verified but not written yet. Details and what was not checked are in [avatar-validation.md](avatar-validation.md).

Earlier, image `base-xiaozhi-cloud` at `4632dc5`, in Spanish, using the Xiaozhi cloud server, not `server/`.

- Boots, connects to the cloud, and holds a spoken conversation, as observed by the user.
- NVS and phy were unchanged after the write.
- Not checked: camera and touch. This board has no head servo code.

## Not tested

- A conversation through the local LLM, STT, and TTS services. These services were not running during the startup test.
- The TTS provider, which the server did not report initializing at startup.
