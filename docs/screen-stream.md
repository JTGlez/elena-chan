# Elena's screen on the PC

An experiment on branch `feature/elena-pc-recording`. It sends what reaches the panel to the PC over UDP, so the screen can be watched, recorded and measured. Results go in [Results](#results). Nothing here is validated on the robot until that section says so.

## How it works

An image built with `SCREEN_STREAM=1` includes the stream, and its `manifest.json` says so. Without the flag, `firmware/main/boards/elena-stackchan/screen_stream.cc` is not compiled in.

1. A viewer sends a UDP hello to port 12801 of the robot every second. The robot only accepts it from the host in its OTA URL. Without a hello for 3 s, it stops and frees its memory.
2. While a viewer holds the stream, an `LV_EVENT_FLUSH_START` callback reads the pixels LVGL is about to send to the panel. It never redraws, so it costs nothing while the screen is static.
3. A task at the lowest priority on CPU1 compresses and sends. If it falls behind, only the viewer falls behind.

| Mode | What it sends |
|---|---|
| `strips` | Each distinct strip once, then only its hash. The animations loop, so most frames cost one small packet |
| `frames` | Every frame as a whole, for comparison |

`--quality 0` sends raw RGB565, which is pixel exact. Any other value sends JPEG.

## Run it

Build the image with the `stackchan-build` skill, then load it with `stackchan-flash`:

```sh
make firmware-image SCREEN_STREAM=1 PURPOSE="pc recording"
```

Watch the screen at `http://127.0.0.1:8765/`. The robot's address never goes in a tracked file:

```sh
make screen-viewer ROBOT=<robot address>
```

Measure one configuration for 5 minutes and record it under `dist/screen-recordings/`:

```sh
make screen-viewer ROBOT=<robot address> ARGS="--mode strips --quality 50 --duration 300 --record"
```

Turn a recording into PNG frames and a video. This needs Pillow and ffmpeg:

```sh
python3 tools/screen_viewer.py export dist/screen-recordings/<session>
```

For an agent, `http://127.0.0.1:8765/latest.png` returns the current screen. It needs Pillow.

The robot answers to the address the viewer's hello came from. A firewall that tracks connections should let that through without a new rule; this is still unconfirmed on this machine.

## Measuring

Each run prints a report and stores it as `report.json` in the recording. The figures that decide the comparison:

| Figure | Meaning |
|---|---|
| `mic_frames_dropped` | Microphone frames lost during the session, the same event as `Encode queue is full` |
| `audio_decoded`, `audio_played` | Voice packets decoded and played. `stats.jsonl` shows them per second, so a silent stretch while speaking stands out |
| `audio_dropped` | Voice packets lost because the decode queue was full. The serial log shows `Decode queue is full` for the same event, with or without a viewer |
| `hook_us_per_frame`, `hook_us_max` | Time added to LVGL's refresh while it holds its lock |
| `encode_us_average` | Time per compression, including preemption, so it can exceed the CPU it used |
| `kbit_per_second` | Bandwidth used on Wi-Fi |
| `internal_min_free`, `internal_largest_min` | Internal RAM, which the Wi-Fi driver and the voice depend on |
| `stack_high_water` | Unused stack of the stream's task, in bytes |
| `paused_low_memory` | Times the stream stopped sending strips because internal RAM was under 32 KB |

Compare each configuration in the same conditions, listening and speaking, with at least 5 minutes each. The baseline is the same image with no viewer: read its `Encode queue is full` count from the serial log. `task_wdt` warnings also come from the serial log.

## Results

One conversation on 2026-11-02: a story long enough to span all three conditions in the same turn. The server had finished generating its audio before any drop, and it only paced the packets out. Viewer in `strips` with JPEG 50. The user heard no cuts in the last row.

| Stretch of the turn | Length | Voice packets dropped | Microphone frames lost |
|---|---|---|---|
| Viewer connected, compressing new strips | about 134 s | 240 | 0 |
| Viewer connected, only hashes | about 36 s | 0 | 0 |
| No viewer | about 278 s | 8, one at a time | Not measured |

This is one turn, and the clean stretch with a viewer is short. It points at compressing new strips, but the mechanism is unknown: the compression runs at the lowest priority, and the first 46 s of it dropped nothing. Internal RAM fell from about 102 KB to about 61 KB during part of it.

Other figures from that session:

| Figure | Value |
|---|---|
| Callback per frame | 3.2 ms on average, 18 ms at most; 14 ms before the hash fix |
| Compressing one strip, wall time | 351 ms on average, 1.6 s at most |
| Bandwidth | 16.1 kbit/s on average |
| Worst gap in the voice | About 8 s without new audio, then 132 packets dropped |

The `frames` mode took 264 ms per whole frame even while idle, and captured about 8 of 36 frames in 21 s. It lost to `strips` before any voice test.

The viewer then began keeping strips in `dist/screen-cache/` and telling the robot which ones it has when it connects, and the robot stopped restarting a session when its task fell behind. A second session, with both changes, connected the viewer before the conversation:

| Session | Viewer connected | Compressions | Voice packets decoded | Dropped | Share dropped | Microphone frames lost |
|---|---|---|---|---|---|---|
| Before both changes | 179 s | 122 | 2412 | 240 | 10% | 0 |
| Session fix, no cache (`--no-cache`) | 360 s | 96 | 5060 | 20 | 0.4% | 0 |
| Session fix and cache | 358 s | 132 | 3293 | 1 | 0.03% | 0 |

Each row is one session, with a different story. The session fix accounts for most of the change, and the cache for the rest.

| Session | Stretches with compression: dropped of decoded | Stretches without: dropped of decoded |
|---|---|---|
| Session fix, no cache | 17 of 1127 | 3 of 3933 |
| Session fix and cache | 0 of 656 | 1 of 2637 |

Without the cache, most drops came while the robot compressed strips during speech. With the cache, it compressed 59 strips in one 30 s window of speech and dropped nothing, so compression alone does not cause drops. A strip took 72 ms on average with the cache, 153 ms without it, and 351 ms before both changes. The mechanism is unknown.

What was heard matches for the worst moment: without the cache, the user noticed a brief silence at about the time 14 packets were dropped, roughly 0.8 s of voice. They also noticed short sound glitches later, when almost nothing was dropped, so those have another cause. Corrupted LLM words read aloud are one possibility, unconfirmed. With the cache, the user noticed no cuts. The TTS failed about 10 times near the end of that session and 7 times in the other. That loses sentences with nothing dropped, so parts of a story may be missing without an audible gap.

**Decision:** keep the cache on by default. Use `--no-cache` only to measure it.

With `AVATAR=emoji`, a 299 s session dropped 0 of 2537 voice packets and no microphone frames, with the cache on and the TTS working throughout. It compressed about 2,070 strips, far more than any Elena session, because the scrolling subtitle makes nearly every frame new. Most of the compressing happened while the robot was speaking. So compression, even at this rate, does not cause drops by itself. Elena's GIF playback may be part of the cause, unconfirmed.

The viewer shows only part of what the robot draws. It sends the latest screen when its low-priority task wakes, and skips the ones in between:

| Session | Frames the robot drew | Frames at the PC | Captured |
|---|---|---|---|
| Elena | 3.5 to 3.7 per second | 1.6 to 1.9 per second | About half |
| Emoji with a scrolling subtitle | 19.4 per second | 6.8 per second | 35% |

Sequence numbers show the frames are skipped on the robot, not lost on the network. This is a design limit, not a hardware one. A planned improvement: queue each frame's hashes and time on the robot and send them in batches.

## Lost voice from the LLM

A different cut, with no viewer connected: at 13:10 the LLM stalled for 40 s and returned garbage, the TTS failed with an empty error, and the robot stayed in `speaking` without audio. After 30 s the avatar went back to rest while the text still said it was speaking. The LLM's text also contains corrupted words in other turns. This is not related to the stream.

## Checked so far

- Host tests cover the hash, the strip pool, the screen map, the packet format and the viewer.
- The viewer received a real GIF from a simulated robot with 10% packet loss, and an exported frame matched that GIF exactly. That simulation does not run the firmware's own code.
- On the robot, `strips` with JPEG 50 showed the real screen with correct colors while idle, without a new firewall rule. It lost no microphone frames in that session, in which nobody spoke.
- The session ends by itself when the viewer stops.
- The user heard no cuts for the last several minutes of the long story, with no viewer connected.
