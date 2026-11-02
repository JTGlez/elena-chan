# Avatar Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every avatar of the `elena-stackchan` board one stable UI and boot screen, and make a new avatar an added folder.

**Architecture:** `ElenaAvatarDisplay` splits into `AvatarStage`, which owns the UI, and an `AvatarRenderer`, which draws the avatar in a layer below it. The folder `avatars/<name>/` selects the renderer, colors and art at build time through a validated manifest. `Application` reports boot steps to the display, and a TCP probe gates boot on the server for this board only.

**Tech Stack:** ESP-IDF v6.1, C++20, LVGL 9.5, Python standard library in the build, host tests with `c++` and `unittest`.

**Spec:** [`docs/superpowers/specs/2026-11-02-avatar-stage-design.md`](../specs/2026-11-02-avatar-stage-design.md)

## Global Constraints

- Work on `feature/elena-avatar-stage` in the root and in `firmware/`, branched from `feature/elena-pc-recording`. Do not push without being asked.
- Commits are Conventional Commits in both repositories. A commit that changes robot behavior says what was tested on the robot.
- `firmware/` commits come first; the root pointer goes in the next root commit.
- Do not write to the robot except through the `stackchan-flash` skill, with confirmation for that write.
- No IP addresses, MAC addresses or SSIDs in tracked files.
- `main/application.cc` and other upstream files get only the hooks named here. Other boards must behave exactly as they do today.
- Do not edit `dependencies.lock`, `sdkconfig*`, or `managed_components/`.
- Colors, copy and sizes are the spec's: accent `#D9A45B`, accent text `#3B2A2A`, name `#8A5A1E`, background `#F9F0E9`, error `#C62828`, boot image 132×123, retry 8 s, probe timeout 3 s, reserved top 30 px.
- Run each suite with `python3 -m unittest` from `firmware/` or the root. `make test` and `make check-locks` must pass before each task's commit. Use a timeout on any new test that could start a loop.

## Review Focus

- No stored WebSocket URL at boot, as on a first boot with no server: the robot stays on the failure screen and retries, and does not crash.
- `AVATAR=` naming a missing folder, or a path such as `../x`: the build stops with a clear message.
- A status text wider than the bubble: it ends in dots, and the clock and icons stay where they are.
- `time()` still at 1970 because the server's time has not arrived: the clock stays hidden.
- A corrupted or missing emotion in an avatar's art: the renderer shows its fallback and the UI stays intact.

---

### Task 1: Avatar folders and manifest

**Files:**
- Create: `firmware/scripts/avatar_pack.py`, `firmware/scripts/tests/test_avatar_pack.py`
- Create: `firmware/main/boards/elena-stackchan/avatars/elena/avatar.json`, `.../avatars/emoji/avatar.json`
- Move: `firmware/main/boards/elena-stackchan/assets/*` to `.../avatars/elena/`, with `git mv`
- Modify: `firmware/scripts/elena_avatar_assets.py`, `firmware/scripts/elena_avatar_refit.py`, `firmware/scripts/tests/test_elena_avatar_assets.py`, `firmware/scripts/tests/test_elena_avatar_display.py` (paths only)
- Modify: `firmware/main/Kconfig.projbuild`, `firmware/main/CMakeLists.txt:232-241,1106-1120`
- Modify: `tools/build_firmware_image.py`, `tools/tests/test_build_firmware_image.py`, `Makefile`, `.claude/skills/stackchan-build/SKILL.md`

**Interfaces:**
- Produces: `CONFIG_ELENA_AVATAR` string, default `"elena"`. It replaces `ELENA_AVATAR_ELENA` and `ELENA_AVATAR_EMOJI`.
- Produces: generated `avatar_pack_config.h` with `AVATAR_DISPLAY_NAME` (string), and `AVATAR_BACKGROUND_COLOR`, `AVATAR_ACCENT_COLOR`, `AVATAR_ACCENT_TEXT_COLOR`, `AVATAR_NAME_COLOR` (`0xRRGGBB`).
- Produces: `avatar_pack.py` subcommands `validate --avatar-dir DIR`, `header --avatar-dir DIR --out FILE`, `cmake --avatar-dir DIR --out FILE`. The generated CMake sets `AVATAR_RENDERER_SOURCE` and either `DEFAULT_EMOJI_DIRECTORY` with `DEFAULT_EMOJI_ALIASES`, or `DEFAULT_EMOJI_COLLECTION`.
- Produces: `AVATAR_RENDERER_SOURCE` is `avatars/<name>/renderer.cc` for `custom`, `gif_avatar_renderer.cc` for `gif`, `image_avatar_renderer.cc` for `image`.

- [ ] **Step 1: Write failing tests in `test_avatar_pack.py`.** Cases: `test_valid_gif_manifest_generates_header`, asserting `#define AVATAR_ACCENT_COLOR 0xD9A45B` and `#define AVATAR_DISPLAY_NAME "Elena-Chan"`; `test_each_missing_field_stops_the_build`, over every required field; `test_missing_emotions_folder_stops_the_build`; `test_bad_color_stops_the_build` for `"red"`; `test_unknown_renderer_stops_the_build`; `test_custom_renderer_requires_renderer_cc`; `test_art_only_avatar_needs_nothing_outside_its_folder`, using a temp folder for the spec's acceptance check; and `test_real_avatars_are_valid`, over `avatars/elena` and `avatars/emoji`.
- [ ] **Step 2: Write failing tests in `test_build_firmware_image.py`.** Cases: `test_set_avatar_writes_kconfig_string`; `test_avatar_outside_avatars_folder_is_refused`, for `../x`, an absolute path and a missing name; `test_assets_check_reads_the_manifest`, where a `gif` avatar needs `GIF89a` in its assets and an `image` avatar must not have it. Remove `select_avatar` and its tests.
- [ ] **Step 3: Run both and confirm they fail because the code is missing.**
- [ ] **Step 4: Implement `avatar_pack.py` with the standard library only,** because it runs in the ESP-IDF environment. Exit with a message naming the field or file.
- [ ] **Step 5: Move the art and write the manifests.** `elena`: the spec's manifest. `emoji`: `"renderer": "image"`, `"emoji_collection": "noto-color-emoji_64"`, background `#FFFFFF`, and the Laurel accent colors. Fix the script and test paths, and confirm `test_elena_avatar_assets.py` and `test_elena_avatar_display.py` pass unchanged otherwise.
- [ ] **Step 6: Wire the build.** Replace the Kconfig choice with the string option. In the `CONFIG_BOARD_TYPE_ELENA_STACKCHAN` branch, run `avatar_pack.py cmake` through `execute_process` with `${PYTHON}` and include its output, guarded against `CMAKE_BUILD_EARLY_EXPANSION`. Fail the configure on a non-zero exit. Generate the header as a custom command on the manifest. Generate `elena_avatar_fallback.h` only when the manifest has `fallback`. Keep the current exclusion of `elena_avatar_display.cc` for the emoji avatar until Task 3.
- [ ] **Step 7: Update `build_firmware_image.py`, `Makefile` and the skill** so `AVATAR=<name>` sets the string, removes `generated_assets.bin` when it differs from the default, and records it in `manifest.json`.
- [ ] **Step 8: Verify.** Run `make test`, `make check-locks`, and `make firmware-image AVATAR=emoji PURPOSE="pack emoji"`. Expected: the image has no `GIF89a`, and the app still reports `screen_stream: false`.
- [ ] **Step 9: Commit** `refactor(elena): move avatars to folders with a manifest` in `firmware/`, then `refactor(build): select an avatar folder by name` in the root.

### Task 2: Renderer contract and implementations

**Files:**
- Create: `firmware/main/boards/elena-stackchan/avatar_renderer.h`, `gif_avatar_renderer.{h,cc}`, `image_avatar_renderer.{h,cc}`
- Create: `firmware/scripts/tests/test_avatar_renderers.py`, `firmware/scripts/tests/fixtures/avatar_renderers_test.cc`
- Modify: `firmware/scripts/tests/fixtures/elena_display_host/` (add LVGL stubs only if needed)

**Interfaces:**
- Consumes: `AvatarPresentation` from `avatar_controller.h`.
- Produces, in `avatar_renderer.h`:

```cpp
class AvatarArtSource {
public:
    virtual ~AvatarArtSource() = default;
    virtual std::shared_ptr<EmojiCollection> Collection() const = 0;
};

class AvatarRenderer {
public:
    virtual ~AvatarRenderer() = default;
    virtual void Attach(lv_obj_t* layer) = 0;
    virtual void Present(const AvatarPresentation& presentation) = 0;
    virtual void SetPowerSave(bool on) = 0;
};

std::unique_ptr<AvatarRenderer> CreateAvatarRenderer(const AvatarArtSource& art);
```

Each renderer source defines `CreateAvatarRenderer`, and the build compiles exactly one. The art source keeps the spec's three-method contract: when assets reload, the stage calls `Present` again, and the renderer compares the collection's identity.

- [ ] **Step 1: Write failing host tests.** For GIF: `test_same_presentation_keeps_the_running_gif`; `test_new_collection_reapplies_the_expression`; `test_missing_emotion_falls_back_to_neutral`; `test_corrupted_gif_shows_static_fallback_once`, asserting no second decoder attempt; `test_power_save_shows_sleeping`. For image: `test_known_emotion_shows_its_image`; `test_unknown_emotion_falls_back_to_neutral`, with `speaking` and `pet` not in the collection; `test_empty_collection_keeps_a_visible_label`, for the fallback rule.
- [ ] **Step 2: Run them and confirm they fail because the files are missing.**
- [ ] **Step 3: Implement `GifAvatarRenderer`** by moving from `elena_avatar_display.cc`: `ValidAvatarGif`, `MatchesAvatarIntegrity`, `GetValidGif`, `TryExpression`, `ClearExpressionLocked`, `ShowStaticFallbackLocked`, and their members (`validated_*`, `applied_*`, `failed_*`, `elena_fallback_dsc_`). It owns its `lv_img` and `LvglGif`, and no longer uses `gif_controller_` or `emoji_image_` from `LcdDisplay`. Do not change `ElenaAvatarDisplay` yet: copy now, delete in Task 3.
- [ ] **Step 4: Implement `ImageAvatarRenderer`:** a centered `lv_img` from `Collection()->GetEmojiImage(emotion)`, falling back to `neutral`, then to an empty label.
- [ ] **Step 5: Run the tests and `make test`.** Expected: all pass.
- [ ] **Step 6: Commit** `feat(elena): add the avatar renderer contract and two renderers`.

### Task 3: AvatarStage replaces ElenaAvatarDisplay

**Files:**
- Create: `firmware/main/boards/elena-stackchan/avatar_stage.{h,cc}`
- Delete: `firmware/main/boards/elena-stackchan/elena_avatar_display.{h,cc}`
- Rename and modify: `firmware/scripts/tests/test_elena_avatar_display.py` to `test_avatar_stage.py`, and its fixture to `avatar_stage_test.cc`
- Modify: `firmware/main/boards/elena-stackchan/elena_stackchan.cc`, `firmware/main/CMakeLists.txt`, `firmware/main/boards/elena-stackchan/README.md`

**Interfaces:**
- Consumes: `CreateAvatarRenderer`, `AvatarArtSource`, `avatar_pack_config.h`.
- Produces: `class AvatarStage : public SpiLcdDisplay, private AvatarArtSource`, with the constructor of `ElenaAvatarDisplay`. Constant `kAvatarErrorColor = 0xC62828` and `kAvatarReservedTopPx = 30`.

- [ ] **Step 1: Port the existing display tests to a fake renderer** that records its calls. Keep every behavior they cover: notices, previews, routine status rejection, no conversation text. Add `test_present_runs_only_on_changes`; `test_attach_runs_once`; `test_power_save_is_forwarded`; `test_theme_reload_presents_again`; `test_status_label_uses_dots_and_keeps_the_icons_in_place`, with a text wider than the bubble; `test_clock_hidden_until_server_time`, at 1970 and then at a valid time; `test_clock_is_a_separate_label_from_status`; `test_error_status_turns_the_bubble_red_with_a_cross`; `test_conversation_text_never_appears`, on `user` and `assistant` roles.
- [ ] **Step 2: Run them and confirm they fail because `AvatarStage` is missing.**
- [ ] **Step 3: Implement `AvatarStage`.** Layers, bottom to top: renderer layer filling 320×240, background from `AVATAR_BACKGROUND_COLOR`, top bar, notices, preview. Top bar: bubble on the left with `LV_RADIUS_CIRCLE`, `AVATAR_ACCENT_COLOR`, `AVATAR_ACCENT_TEXT_COLOR`, a small tail below it, and `LV_LABEL_LONG_MODE_DOTS`; on the right, the clock, Wi-Fi, mute and battery. No shadows. `ApplyPresentation` keeps its comparison with the last applied presentation, and calls `renderer_->Present`. `SetChatMessage` keeps today's rule: only an operational notice or maintenance owner shows text. Error means `SetStatus` receives `Lang::Strings::ERROR`, or an alert: show `✕ ` plus the text on `kAvatarErrorColor`. `UpdateStatusBar` reuses the base with the routine flag, then updates the clock label when `tm_year` is valid.
- [ ] **Step 4: Replace the display in `elena_stackchan.cc`,** with no mention of a specific avatar in that file. Remove the `#if CONFIG_ELENA_AVATAR_*` blocks. Delete the old files, and drop the source filter in CMake for those two files: `avatar_controller.cc` stays for every avatar, and `gif_avatar_renderer.cc` and `image_avatar_renderer.cc` are filtered so only `AVATAR_RENDERER_SOURCE` compiles.
- [ ] **Step 5: Verify.** Run `make test`, then `make firmware-image PURPOSE="stage elena"` and `make firmware-image AVATAR=emoji PURPOSE="stage emoji"`. Expected: both build, and `elena_stackchan.cc` matches no avatar name.
- [ ] **Step 6: Commit** `refactor(elena): share one UI through AvatarStage`, and say the images have not run on the robot.

### Task 4: Boot screen and server gate

**Files:**
- Create: `firmware/main/display/boot_step.h`
- Create: `firmware/main/protocols/server_probe.{h,cc}`
- Create: `firmware/main/boards/elena-stackchan/boot_view.{h,cc}`, `boot_screen.{h,cc}`
- Create: `firmware/scripts/avatar_boot_assets.py`, `firmware/main/boards/elena-stackchan/avatars/fonts/CherryBombOne-Regular.ttf`, `.../avatars/fonts/OFL.txt`
- Create: `.../avatars/<name>/boot.png`, `boot_image.c`, `boot_font.c` for `elena` and `emoji`
- Create: `firmware/scripts/tests/test_boot_flow.py`, `firmware/scripts/tests/fixtures/boot_flow_test.cc`
- Modify: `firmware/main/display/display.h:60`, `firmware/main/application.cc`, `firmware/main/application.h`, `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/boards/elena-stackchan/avatar_stage.{h,cc}`, and the stage tests

**Interfaces:**
- Produces, in `boot_step.h`: `enum class BootStep { kConnectingWifi, kFindingServer, kServerFailed, kReady };`
- Produces, in `Display`: `virtual void OnBootProgress(BootStep step, int retry_seconds)` with an empty default, and `virtual bool RequiresServerAtBoot() const`, default `false`.
- Produces, in `server_probe.h`: `struct ServerEndpoint { std::string host; uint16_t port; };`, `std::optional<ServerEndpoint> ParseWebsocketEndpoint(std::string_view url);`, `bool ProbeServer(const ServerEndpoint& endpoint, int timeout_ms);`
- Produces, in `boot_view.h`: `struct BootView { const char* bubble; bool is_error; int retry_seconds; bool show_avatar; }; BootView ResolveBootView(BootStep step, int retry_seconds);`
- Produces: symbols `avatar_boot_image` and `avatar_boot_font`, from each avatar's generated C files.

- [ ] **Step 1: Write failing host tests.** `test_parse_ws_with_port`, `ws://192.0.2.1:8000/xiaozhi/v1/` to host and 8000; `test_parse_defaults`, 80 for `ws` and 443 for `wss`; `test_parse_rejects_empty_and_other_schemes`, including an empty string and `http://`. `test_boot_views`, asserting the exact copy: `Conectando a Wi-Fi…`, `Buscando al servidor…`, `✕ No encuentro el servidor`, with `is_error` true only there, and `show_avatar` true only for `kReady`. In the stage tests: `test_avatar_does_not_attach_before_ready`; `test_presentation_before_ready_applies_on_ready`; `test_ready_removes_the_boot_screen`; `test_failure_shows_the_countdown`, `Reintento en 8 s`; `test_failure_then_ready_loads_the_avatar_without_reset`; `test_top_bar_is_hidden_during_boot`.
- [ ] **Step 2: Run them and confirm they fail because the files are missing.**
- [ ] **Step 3: Implement `ParseWebsocketEndpoint` and `ResolveBootView`.** Boot copy lives in `boot_view.cc` in Spanish, since this board's firmware language is Spanish and these strings have no other users.
- [ ] **Step 4: Implement `ProbeServer`** with `getaddrinfo` and a non-blocking `connect` under `select`, closing the socket on every path. It sends nothing.
- [ ] **Step 5: Implement `scripts/avatar_boot_assets.py --avatar <name>`,** run by hand and not by the build: it resizes `boot.png` to at most 132×123 with Pillow, writes `boot_image.c` as `LV_COLOR_FORMAT_RGB565A8`, and runs `npx lv_font_conv` over the glyphs of `display_name` at 28 px, 4 bpp, into `boot_font.c`. Elena's `boot.png` comes from `assets/elena-readme.png`, and the emoji's from `noto-color-emoji_64`'s neutral image. Run it for both and commit the output. Run with `uv run --with pillow`.
- [ ] **Step 6: Implement `BootScreen` and stage wiring.** The boot screen uses the medallion layout: image centered near the top, `AVATAR_DISPLAY_NAME` in `avatar_boot_font` with `AVATAR_NAME_COLOR`, the bubble at about y=181, the countdown below it, the version at the bottom right. `RequiresServerAtBoot()` returns `true`. On `kReady`, the stage attaches the renderer, applies the last presentation, shows the top bar and deletes the boot screen. While booting, operational text from `Alert` and `ShowNotification`, including Wi-Fi setup and activation codes, shows in the note area of the boot screen.
- [ ] **Step 7: Add the hooks to `application.cc`.** `kConnectingWifi` before `board.StartNetwork()`. `kFindingServer` at the start of `ActivationTask`. `kReady` right after `SetDeviceState(kDeviceStateIdle)` in the activation-done handler, before the existing ready sound. A new `Application::WaitForServerAtBoot()`, called after `CheckNewVersion()` and only if `display->RequiresServerAtBoot()`: it reads the URL from `Settings("websocket", false)`; with an MQTT configuration it returns; with no URL or a failed probe it reports `kServerFailed` each second for 8 s, and tries again without limit, using `ProbeServer` with 3000 ms.
- [ ] **Step 8: Verify.** `make test`, `make check-locks`, and build for `elena`. Confirm another board still builds: `make firmware BOARD=m5stack/core-s3 BOARD_VARIANT=m5stack-core-s3`. Measure the app against the 72% from `docs/avatar-validation.md`.
- [ ] **Step 9: Commit** `feat(elena): add the boot screen and wait for the server`, and say the flow has not run on the robot.

### Task 5: Validate on the robot and document

**Files:**
- Modify: `AGENTS.md`, `docs/screen-stream.md`, `docs/avatar-validation.md`, `docs/tested-versions.md`, `firmware/main/boards/elena-stackchan/README.md`
- Create: `docs/avatars.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Build** `make firmware-image SCREEN_STREAM=1 PURPOSE="stage elena"` and `make firmware-image AVATAR=emoji SCREEN_STREAM=1 PURPOSE="stage emoji"`.
- [ ] **Step 2: Flash Elena** with `stackchan-flash`: dry run, confirmation from the user, `--execute`.
- [ ] **Step 3: Test boot without the server.** Stop the server, reset, and record it with `tools/screen_viewer.py`. Expected: the failure screen, no avatar, retries. Start the server. Expected: it finishes without a reset, the sound plays, the avatar appears. The user confirms the sound and the look on the panel.
- [ ] **Step 4: Measure voice with a viewer on Elena** in a 5-minute story. Expected: under 0.1% of `audio_dropped` with a warm cache, 0 `mic_frames_dropped`, and `hook_us_per_frame` near the 2.5 ms in `docs/screen-stream.md`. A worse figure stops the task for a decision.
- [ ] **Step 5: Flash the emoji image with its own confirmation, and repeat Steps 3 and 4.** Save a capture of each state for both avatars with `latest.png`, and compare the top bar's position between them.
- [ ] **Step 6: Check the panel's reds and contrast** with the user. Adjust colors in the manifests only.
- [ ] **Step 7: Write `docs/avatars.md`:** how to add an avatar, the manifest's fields, and the renderer rules, including its art-only and custom paths. Add `AVATAR=<name>` to `AGENTS.md`. Record the results in `docs/screen-stream.md`, `docs/avatar-validation.md` and `docs/tested-versions.md`, with what was and was not tested.
- [ ] **Step 8: Commit** `docs: record the avatar stage results and how to add an avatar`, with the pointer. Ask before pushing.
