# AGENTS.md

Custom StackChan robot (M5Stack CoreS3, ESP32-S3) with a local STT → LLM → TTS pipeline.

## Contents

1. [Repository layout](#repository-layout)
2. [Rules for all work](#rules-for-all-work)
3. [Development environment](#development-environment)
4. [Submodules and upstream changes](#submodules-and-upstream-changes)
5. [Knowledge graphs](#knowledge-graphs)
6. [Local voice lab](#local-voice-lab)

## Repository layout

```text
elena-chan/
├── firmware/         # Submodule: JTGlez/xiaozhi-esp32, branch elena
├── server/           # Submodule: JTGlez/xiaozhi-esp32-server, branch elena
├── brain/            # Hermes profile, SOUL.md, stackchan_mcp.py; no secrets
├── docs/             # Decisions and tested version combinations
├── tools/            # Setup scripts and helpers; `make help` lists entry points
├── config/           # Templates without secrets
├── dist/firmware/    # Firmware images ready to flash (gitignored)
├── .github/          # CI workflows
├── .claude/skills/   # Firmware build and flash, local models, delegation to Hermes
├── vendor/           # Read-only reference code and graphs (gitignored)
├── .localworker/     # Local model workers (gitignored)
└── .localaudio/      # Local voice lab (gitignored)
```

Create `brain/` and `docs/` when they have content.

| Component | Location | Responsibility |
|---|---|---|
| Firmware | `firmware/` | Audio, display, servos, and the robot's MCP tools |
| Server | `server/` | Robot connection, VAD, STT, TTS, and the HTTP bridge to the robot |
| Brain | `brain/` | Hermes personality, memory, and tools |

Hermes has no fork. Install it separately and record the tested version in `docs/`.

**Pinned bases:** Xiaozhi `4632dc5`, `xiaozhi-esp32-server` `4745186`, ESP-IDF v6.1. See [Update dependencies](#update-dependencies).

## Rules for all work

### Secrets and personal data

- Never commit tokens, `.env` files, `data/.config.yaml`, or robot backups. Backups contain Wi-Fi passwords and tokens.
- Never put MAC addresses, IP addresses, or SSIDs in tracked files.
- These rules apply to this repository and to both forks, which are public.

### Flashing the robot

- Use only the `stackchan-flash` skill. Do not use `idf.py flash` or esptool directly.
- Every write to the robot needs explicit user confirmation.
- Build images with the `stackchan-build` skill. They live in `dist/firmware/<date>-<time>-<purpose>-<commit>/`.

### Validation

- A successful build does not validate hardware. Report what was tested on the robot and what was not.

## Development environment

```sh
make check-system   # Reports missing apt packages; installs nothing
make setup          # Submodules, server environment, ESP-IDF v6.1
make test           # All test suites
make check-locks    # Fails if pinned versions drifted; run after a firmware build
make firmware       # Builds for CoreS3; never writes to the robot
make firmware-image PURPOSE="..."  # Stores an image in dist/firmware/
make server-config  # Generates data/.config.yaml for local services; PROFILE=minimax uses MiniMax
make server-run     # Starts the server
```

Each Python environment stays separate:

| Environment | Location | Python | Used for |
|---|---|---|---|
| Server | `server/main/xiaozhi-server/.venv` | 3.10 | Server code and tests, with CPU torch |
| ESP-IDF | Managed by ESP-IDF under `~/.espressif` | 3.12 | Firmware builds and script tests |
| System | `python3` | Any current version | `tools/` and skills, standard library only |

Activate ESP-IDF in a shell with `source tools/idf-env.sh`. Other ESP-IDF versions on the machine are unaffected.

`PROFILE=minimax` reads `MINIMAX_API_KEY` from `config/minimax.env` and the server address from `OTA_URL` in `config/firmware.local.env`. Both files are gitignored; copy them from their `.example` files. Replacing an existing config needs `FORCE=1`.

The server's two skipped tests are skipped upstream through a fixed `pytest.skip`. Leave them as they are.

### Skills that need local setup

Details of a machine stay out of tracked files: each skill reads them from an untracked config created from a tracked example.

| Skill | Needs | Set up |
|---|---|---|
| `stackchan-build` | `make setup`, a server address | `config/firmware.local.env`, from its `.example` |
| `stackchan-flash` | The robot attached | Its "First use with a robot" section |
| `local-model` | An NVIDIA GPU, Docker, model workers | Its "Set up" section; `doctor` reports what is missing |
| `hermes-delegate` | Hermes, a route to a model | `.hermes-delegate.local.json`, from `.hermes-delegate.local.example.json`; then `install-guard` |

`.hermes-delegate.local.json` is the only place that joins `local-model` and `hermes-delegate`. To delegate to a remote model, set its provider and model there and remove `ready_command`.

## Submodules and upstream changes

### Fork branches

Firmware and server code lives only in the forks. This repository records which commit of each fork it uses.

- `main` mirrors upstream and never carries our commits.
- `elena` starts at the pinned base and holds all our finished changes.
- `feature/<name>` holds larger work in progress and merges into `elena` when ready.

Each `elena-chan` commit records an exact combination of firmware and server.

### Clone

```sh
git clone --recursive git@github.com:JTGlez/elena-chan.git
cd elena-chan
git config push.recurseSubmodules on-demand
git -C firmware remote add upstream https://github.com/78/xiaozhi-esp32.git
git -C server remote add upstream https://github.com/xinnan-tech/xiaozhi-esp32-server.git
```

To check out only the Python server and the repository's root files:

```sh
git -C server sparse-checkout set main/xiaozhi-server
```

### Make a change

1. Work inside the submodule on `elena` or a `feature/<name>` branch. `git submodule update` leaves a detached HEAD, so switch to the branch before committing.
2. Commit and push to the fork.
3. In the root, commit the updated pointer together with anything that depends on it.

Never publish a pointer to a commit that is missing from its fork. `push.recurseSubmodules on-demand` prevents this, but it can also publish the submodule's whole branch of the same name, and both forks are public. Follow the order above so a root push publishes nothing extra.

### Keep changes easy to merge

- **Add before editing.** Prefer new files and a few registration lines in upstream files.
- **Firmware:** put our hardware in its own `main/boards/` folder and do not alter existing boards. Keep `main/application.cc` changes to minimal hooks.
- **Server:** select STT, LLM, and TTS through configuration when possible. Build an image from the fork; do not copy files into a running container.
- **Commits:** keep them small and explain why. After `git fetch upstream`, `git diff upstream/main...elena` must read as the full list of our changes. Use three dots.

### Update dependencies

Exact versions are recorded here. A build must never change them silently.

| Dependency | Pinned in | Update with |
|---|---|---|
| ESP components | `firmware/dependencies.lock`, tracked with `git add -f` because upstream ignores it | Delete or edit the lock, then build |
| Server Python packages | `server/main/xiaozhi-server/requirements.lock` | Edit `requirements*.txt`, then `make update-server-lock`; for one package, `make update-server-lock UPGRADE=<name>` |
| ESP-IDF | `IDF_COMMIT` in `tools/setup-idf.sh` and the image digest in `.github/workflows/ci.yml` | Change both together |
| `uv` in CI | `.github/workflows/ci.yml` | Change the version |
| Xiaozhi and server | Submodule pointers | See [Pull upstream changes](#pull-upstream-changes) |

1. Update only for a concrete reason, and one dependency group at a time.
2. Run `make check-locks`, `make test`, and `make firmware`.
3. If firmware behavior can change, test on the robot before merging.
4. Commit the lock with the change, including the submodule pointer, and update `docs/tested-versions.md`.

`make check-locks` fails when a lock no longer matches its inputs. CI also fails if a firmware build changes `dependencies.lock`.

### Pull upstream changes

Update only for a concrete reason, such as a needed fix or feature. Do not track upstream continuously.

1. In the submodule, run `git fetch upstream --tags`.
2. Create a test branch from `elena` and `git merge` a specific tag or commit. Do not rebase, because older pointers must stay valid.
3. Build and test on the robot.
4. Resolve `dependencies.lock` and `requirements.lock` for the merged manifests, then follow [Update dependencies](#update-dependencies).
5. Merge into `elena`, push, then update the pointer and the tested combination in `docs/`.

Update firmware and server separately when possible, so a failure has one likely cause.

## Knowledge graphs

Graphs of reference code live under `vendor/`. It is gitignored, so `rg` and `git ls-files` skip it unless you use `rg --no-ignore`. There is no `graphify-out/` at the repository root.

### Isolated graphs

Each graph covers one codebase.

| Graph | `graph.json` path | Contents |
|---|---|---|
| Official firmware | `vendor/firmwares/m5stack-StackChan/graphify-out/graph.json` | M5Stack `firmware/`, without Xiaozhi |
| Custom firmware | `vendor/firmwares/7H3-CH053N-StackChan-CustomFW/graphify-out/graph.json` | Its patch, scripts, and README |
| Xiaozhi `4632dc5` | `vendor/firmwares/xiaozhi-esp32/graphify-out/graph.json` | Custom firmware's base, without its patch |
| Xiaozhi v2.2.4 | `vendor/firmwares/xiaozhi-esp32-v2.2.4/graphify-out/graph.json` | Official firmware's base, with its patch applied |
| Nous-Girl | `vendor/firmwares/nous-girl-stackchan/graphify-out/graph.json` | Server, Hermes add-ons, docs, and wake words; its firmware is unpublished |
| `xiaozhi-esp32-server` `4745186` | `vendor/servers/xiaozhi-esp32-server/graphify-out/graph.json` | Unmodified `main/xiaozhi-server` code, without its docs |
| ESP-IDF v6.1 | `vendor/sdk/esp-idf-v6.1/graphify-out/graph.json` | Code of the components in the `elena-stackchan` build, ESP32-S3 only, without submodules, tests, docs, examples, or register structs; scope in its `.graphifyignore` |

### Connected graphs

Each graph joins two isolated graphs and adds bridge edges.

| Graph | `graph.json` path |
|---|---|
| Official + Xiaozhi v2.2.4 | `vendor/firmwares/_connected/official--xiaozhi-v2.2.4/graphify-out/graph.json` |
| Custom + Xiaozhi `4632dc5` | `vendor/firmwares/_connected/custom--xiaozhi-4632dc5/graphify-out/graph.json` |

Every `graph.json` has a `GRAPH_REPORT.md` and a `graph.html` beside it. Connected graphs also have `BRIDGES.json`, which lists each bridge and its justification. Bridge edges carry `bridge: true`.

### Query

Run from the repository root:

```sh
graphify query "<question>" --graph <path>/graph.json
graphify path "<A>" "<B>" --graph <path>/graph.json
graphify explain "<node>" --graph <path>/graph.json
```

If a name is ambiguous, the command exits with code 3 and lists the candidates.

### Interpret results

- Graphs do not replace code. Verify in the source before stating something as fact.
- `INFERRED` and `AMBIGUOUS` edges are unconfirmed.
- In connected graphs, shared dependencies such as `mutex` or `string` are nodes without a `repo`. A path through them is not a real connection between the two codebases.
- Keep the two Xiaozhi versions separate: the official firmware uses v2.2.4 and the custom firmware uses `4632dc5`.

## Local voice lab

`.localaudio/` compares STT and TTS candidates independently before they join an STT → LLM → TTS pipeline. Like `.localworker/`, it is excluded through `.gitignore`. Its rules live here, in the root `AGENTS.md`.

### Requirements

- **Target hardware:** NVIDIA RTX 5090 Blackwell, 32 GB. Verify that dependencies and kernels support its `sm_120` architecture. Support for another Blackwell GPU is not enough.
- **VRAM budget:** a full voice candidate may use **up to 16 GB**, counting STT and TTS resident at once, their caches, and inference peaks. Leave room for the LLM and the rest of the pipeline.
- **Going over budget:** ask the user before running anything above 16 GB. Only the user can raise the budget, and never above 31 GB. Free memory is not authorization.
- **Languages:** each candidate must understand and produce speech in **Spanish, English, and Japanese**. Verify STT and TTS separately. Multilingual text support does not prove multilingual speech support.
- **Latency:** prioritize low latency and speed for interactive conversation, with streaming when the model and engine allow it.
- **Model choice:** look for unified STT/TTS models first. Separate model pairs are also acceptable.
- **Terminology:** distinguish a unified model from a shared engine that loads different models, and from a speech-to-speech model without independent transcription and synthesis. Document what each proposal allows.
- **Engines:** research vLLM/vLLM-Omni, Basalt, audio.cpp, and other options through current primary sources. Confirm support for the checkpoint and required operations before choosing one.

### Candidate layout

```text
.localaudio/
├── README.md
├── .workspace/                # Shared research and candidate comparison
└── <candidate>/               # Model(s) + engine + evaluated configuration
    ├── README.md              # Short load and run instructions
    ├── docker-compose.yml     # Runs from locally built images
    ├── <image>.tar.zst        # One or more compressed exported Docker images
    ├── SHA256SUMS             # Integrity of delivered artifacts
    ├── models/                # Weights, when delivered outside the images
    └── .workspace/            # Sources, Dockerfiles, scripts, builds, tests, logs
```

- Give each candidate an isolated folder and Compose project, with names and ports that avoid collisions.
- Evaluate candidates one at a time by default. Do not assume they all fit on the GPU together.
- For a unified model, use one voice service when its engine allows it. For separate models, use independent `stt` and `tts` services in the same Compose project that can also start separately.
- Do all experimentation, source downloads, compilation, conversion, and image builds in the candidate's `.workspace/`. Keep the instructions and versions needed to reproduce the image there. Shared research may live in `.localaudio/.workspace/`.
- The candidate root holds the final delivery: README, Compose file, compressed images, and required weights and configuration. Startup must not depend on paths inside `.workspace/`.
- The LLM comes later. STT and TTS must be testable without starting one.

### Delivery

- A proposal is not ready to run until its image is built, its services are tested on the 5090, and its compressed images are exported next to the Compose file. Clearly mark candidates that are only researched or still awaiting validation.
- The delivered Compose file references specific local tags and does not build at startup.
- Export with `docker save` and compression. Document decompressing and running `docker load` before `docker compose up`, because Compose cannot consume a compressed image file directly.
- Include weights in the image or as local artifacts that Compose mounts, and document their location and preparation. Do not present a delivery without its weights as complete. Keep applicable licenses when distributing them.
- Pin model revisions, sources, base images, and dependencies so builds and tests can be repeated.
- The short README must state model(s), engine, host requirements, image loading, start and stop, endpoints, minimal STT and TTS examples, and relevant limitations. Keep long details in `.workspace/`.

### Validation

- Before declaring a delivery ready, verify the Compose file and artifact integrity, load the exported images, and start without a rebuild. Test transcription and synthesis in all three languages, and joint STT/TTS residency within budget.
- Record idle and peak VRAM, cold start, STT latency to partial and final results, TTS latency to first playable audio, and real-time factor (RTF).
- State chunk size, concurrency, input length, precision, and warm-up. Separate end-of-turn detection latency from model latency.
- Third-party figures are references, not measurements on this machine. Do not claim linguistic quality, real streaming, Blackwell compatibility, or local performance without matching evidence. Identify the automated checks and any human listening still pending.
- Do not stop or reconfigure other GPU services to fit an experiment without user authorization.
