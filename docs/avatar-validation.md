# Elena avatar validation

Results for the `elena-stackchan` board, by where they were checked. A successful build does not validate hardware.

Host checks on 2026-11-02 at firmware `fe366d4`, ESP-IDF v6.1. The robot runs that same build, through the local server with MiniMax for STT, LLM, and TTS.

## Host and build

| Check | Result |
|---|---|
| `make test` | Passes on `fe366d4`: 138 firmware host tests, 48 server tests with 2 skipped, and the tools and skill suites |
| Firmware host tests, `firmware/scripts/tests` | 138 pass on `fe366d4` |
| GIF decoding | The firmware decoder matches Pillow frame by frame, on all 12 GIFs |
| `elena-stackchan` image | Builds at `fe366d4`. App 72% of its slot (2983248 of 4128768 bytes), assets 35% of their partition |
| Assets inside the built image | Every one of the 12 GIFs appears byte for byte in `generated_assets.bin` of `20261102-041708-elena-stronger-reds-fe366d4`, once each, and the previous art is absent |
| CoreS3 and OLED or no-display builds | Not run in this round |
| Clean clone without the kit, incremental GIF change | Not run |

## On the robot

Automatic listening over WebSocket only. Figures come from the serial log. A dropped frame is `Encode queue is full`.

| Image | Time | Turns | Dropped frames | `task_wdt` | Crashes |
|---|---|---|---|---|---|
| First, animated `listening`, lock on every tick | 631 s | Not counted | 119 | 2 | 0 |
| With the lock fix | 730 s | 25 | 6 | 0 | 0 |
| Current, static `listening` | 300 s | 12 | 0 | 0 | 0 |

The `task_wdt` warnings did not reset the robot. Each run is one session, so these figures show a direction, not a limit. The current image has one 300 s sample.

Seen by the user on the screen: state text in Spanish on the left, indicators on the right, the speaking animation starting with the voice, one mouth, and the complete thought bubble and music note.

The `fe366d4` write kept NVS and phy: the script's plan listed no region at `0x9000`, and its post-write check of the protected ranges reported them unchanged. After it booted, the server shows an OTA request, a WebSocket session for this build, ASR initialized, and Spanish speech transcribed. Frame and timing figures for this image have not been measured yet.

NVS and phy data were unchanged after each write.

## Acceptance criteria

| Criteria | Status |
|---|---|
| AC-01, AC-02, AC-03 | Seen on the robot and covered by host tests |
| AC-04, AC-05, AC-06, AC-07, AC-08, AC-11, AC-12, AC-13 | Host tests only |
| AC-09 | Host tests. No conversation text was reported on the robot |
| AC-10 | Host tests. The failure was seen on the first image; the fix was not retried on the robot with the server off |
| AC-14 | Not run |
| AC-15 | Not run: 100 changes for latency, 500 for memory, 10 minutes of audio, comparison without the avatar |
| AC-16 | Automatic mode only. Manual and realtime modes not run, and no interruption by touch appears in any log |

## Changes from the design

Decided after seeing the first image on the robot:

- The top strip shows the state text beside the indicators. Conversation text stays hidden.
- Notices use a second strip below the indicators, so they never replace them.
- The figure sits at x=4, not x=20, to fit the art the kit had cropped. See `assets/PROVENANCE.md` in the board.
- `listening` is one frame, so nothing redraws while the microphone is encoded.

## Panel

The board reads the touch controller at boot and logs the panel, as M5Stack's firmware does. This robot reports `FIRMID:0x10`, an ILI9342C, so it keeps the default initialization. Only an ILI9342E (`FIRMID:0x12`) gets its own gamma curves. Those commands have not run on real hardware.

The washed-out red came from the art, not from the GIF format: the ribbon's average color barely changes from the master sheet to the GIF. `elena_avatar_refit.py` now saturates the reds. Judged on the robot by the user on 2026-11-02 and accepted.

## Known limits

- Emotions rarely show in a normal conversation. Listening has priority over the resting emotion, and a new turn clears it. The server also sends `happy` unless the reply contains an emoji.
- A late `tts.start` from before a new turn does not start the speaking animation. Audio still plays.
- Assets replace the emoji collection without a lock while the conversation tick may read it, once per boot. Not observed as a failure.
- Latency is 4.2 s from the end of speech to the voice, estimated from separate measurements: MiniMax STT 1.8 s, LLM 0.75 s, TTS 0.37 s, and about 1.1 s unexplained on our server.

## OTA and migration

Moving from the generic CoreS3 needs a reviewed `stackchan-flash` write that keeps NVS. The firmware no longer installs updates: it ignores the `firmware` section of the OTA response.
