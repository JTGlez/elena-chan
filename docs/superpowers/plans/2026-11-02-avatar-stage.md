# Avatar Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every avatar of the `elena-stackchan` board one stable UI and boot screen, and make a new avatar an added folder.

**Architecture:** `ElenaAvatarDisplay` splits into `AvatarStage`, which owns the UI, and an `AvatarRenderer`, which draws the avatar in a layer below it. The folder `avatars/<name>/` selects the renderer, colors and art at build time through a validated manifest. `Application` reports boot steps to the display, and a TCP probe gates boot on the server for this board only.

**Tech Stack:** ESP-IDF v6.1, C++20, LVGL 9.5, Python standard library in the build, host tests with `c++` and `unittest`.

**Spec:** [`docs/superpowers/specs/2026-11-02-avatar-stage-design.md`](../specs/2026-11-02-avatar-stage-design.md). Read it first. When this plan and the spec disagree, stop and report it.

**Who runs what:** Tasks 1 to 5 are for an implementer on a PC, with no robot. Task 6 is for a human operator. An implementer never writes to the robot and never pushes.

## How to work

1. Do the tasks in order. Do each step in order. Do not skip a step that looks unnecessary.
2. Before writing code in a file, read that file, and read every test that names it.
3. A test step comes before its code. Run the test, and see it fail for the expected reason, before you implement.
4. If a test written by this plan seems wrong, do not change its meaning to make it pass. Stop and report it.
5. Never delete or weaken an existing test, except where a step says exactly which one.
6. **Stop and report** if: a command in this plan fails twice for a reason you do not understand; a name or line in this plan does not exist; you would have to change a file not listed in the task; or the same failure survives two attempts to fix it. Report what you ran, the exact output, and what you tried.
7. Report what was tested and what was not. A successful build proves only that it builds.

## Global Constraints

- Start from the commit that contains this plan, on the branch your operator names. The default is `feature/elena-avatar-stage`, in the root and in `firmware/`, each branched with `git switch -c` from its current commit. `<start>` below means the `firmware/` commit you branched from. `firmware/` changes are committed inside `firmware/`, and the root records the pointer with `git add firmware`.
- Commits are Conventional Commits in both repositories. End each message with the line `Co-Authored-By:` your operator specifies, if any. Do not push.
- A commit that changes robot behavior says in its body that it has not run on the robot.
- `main/application.cc`, `main/display/display.h` and `main/CMakeLists.txt` are shared with other boards. Change only what a step names. Every other board must behave exactly as it does today.
- Do not edit `firmware/dependencies.lock`, `firmware/sdkconfig*`, `firmware/build/`, `firmware/managed_components/`, or anything under `dist/` or `vendor/`.
- No IP addresses, MAC addresses or SSIDs in tracked files. Test addresses come from `192.0.2.0/24`.
- `tools/` and everything the firmware build runs use only the Python standard library.
- The boot art is already committed: `avatars/{elena,emoji}/boot.png`, `boot_image.c`, `boot_name.c`, and `avatars/fonts/`, with `scripts/avatar_boot_assets.py` and its test. **Do not regenerate or edit them,** and do not run that script.
- `clang-format` is not installed here. Follow the style of the file you edit: 4 spaces, 100 columns.
- Exact values, from the spec: accent `#D9A45B`, accent text and icons `#3B2A2A`, name `#8A5A1E`, Elena's background `#F9F0E9`, error `#C62828`, boot image at most 132×123, retry 8 s, probe timeout 3000 ms, reserved top 30 px.
- Exact copy, in Spanish, with plain ASCII dots: `Conectando a Wi-Fi...`, `Buscando al servidor...`, `No encuentro el servidor`, `Reintento en 8 s`. The built-in font has no `…` and no `✕`, so they must not appear in any firmware string. The error mark is `×` (U+00D7), which it does have.

## Known traps in this repository

Each of these has already caused a failure here.

- **Shell:** the default shell is zsh, which does not split `$VAR` into words. Use explicit paths or `bash <<'EOF'`.
- **`pkill -f` and `pgrep -f` match your own command line,** and can kill your own shell. Do not use them.
- **Two Pythons:** `source tools/idf-env.sh` changes `python` and `python3` for the rest of that shell, and that Python has no Pillow. Run IDF commands through `make`, or in a separate shell.
- **Tests that can hang:** a test that calls `screen_viewer.main` or anything with a network loop must run under `timeout 60`. A RED test can hang instead of failing.
- **Builds:** `make firmware-image` rebuilds everything, takes 6 to 8 minutes, and rewrites `firmware/sdkconfig` and `firmware/build/`. Run it with a timeout of at least 15 minutes. Do not edit firmware sources while it runs: the tool stops with `Firmware sources changed during the build`.
- **Host LVGL is a stub:** `scripts/tests/fixtures/elena_display_host/lvgl.h` models only what the code uses. A new LVGL call needs a stub there and in `lvgl_host.c`. It stores values and computes no layout, so a host test proves state, not looks.
- **Compile warnings:** the host tests build production files with `-Wall -Werror`.
- **Locks:** `DisplayLockGuard` is recursive. Failing to take it means do nothing: never touch LVGL objects without it.
- **Reentrancy:** the conversation tick reaches the display 20 times per second on the main task. Taking the display lock on an unchanged tick lost microphone frames on this robot. The test `unchanged_tick_skips_display_lock` protects this.

## Review Focus

Each has a test in the task named. These are the failures most likely to reach a person.

- **Touch or wake word during the failure screen:** `HandleToggleChatEvent` moves `Activating` to `Idle`. The wait must not end because of it, and the avatar must not load (Task 4, by code rule; Task 6 on the robot).
- **No stored WebSocket URL:** stay on the failure screen and retry, with no crash (Task 4).
- **`AVATAR=` naming a missing folder or a path such as `../x`:** the build stops with a clear message (Task 1).
- **A status wider than the bubble:** it ends in dots, and the clock and icons stay in place (Task 3).
- **A long Wi-Fi setup hint on the boot screen:** it stays readable and is not covered (Task 4).
- **The clock at 1970:** hidden until the server's time arrives (Task 3).
- **Corrupted or missing art:** the renderer falls back, and the UI stays intact (Task 2).
- **Power save during boot:** no crash with no renderer attached, and the screen wakes when boot finishes (Task 4).

---

### Task 1: Avatar folders and manifest

**Purpose:** move Elena's art into a folder, describe each avatar in a manifest, and select one by name. Behavior on the robot does not change.

**Files:**
- Create: `firmware/scripts/avatar_pack.py`, `firmware/scripts/tests/test_avatar_pack.py`
- Create: `firmware/main/boards/elena-stackchan/avatars/elena/avatar.json`, `firmware/main/boards/elena-stackchan/avatars/emoji/avatar.json`
- Move with `git mv`: every tracked file of `firmware/main/boards/elena-stackchan/assets/` to `firmware/main/boards/elena-stackchan/avatars/elena/`, keeping the `gifs/` folder
- Modify paths only: `firmware/scripts/elena_avatar_assets.py` (`DEFAULT_ASSET_DIR`), `firmware/scripts/tests/test_elena_avatar_assets.py` (`ASSETS`), `firmware/scripts/tests/test_elena_avatar_display.py` (`GIFS_DIR`, `FALLBACK_RGB565`), `firmware/main/boards/elena-stackchan/avatars/elena/PROVENANCE.md`, `firmware/main/boards/elena-stackchan/README.md`
- Modify: `firmware/main/Kconfig.projbuild`, `firmware/main/CMakeLists.txt`, `firmware/main/boards/elena-stackchan/elena_stackchan.cc`
- Modify: `tools/build_firmware_image.py`, `tools/tests/test_build_firmware_image.py`, `.claude/skills/stackchan-build/SKILL.md`

**Manifest fields.** All are required unless noted.

| Field | Rule |
|---|---|
| `display_name` | Non-empty string, at most 20 characters, without `"` or `\` |
| `renderer` | `gif`, `image` or `custom` |
| `background`, `accent`, `accent_text`, `name_color`, `icon_color` | `#RRGGBB` |
| `emotions` | `gif` only: a folder holding at least one `*.gif`, and named exactly `gifs` because `elena_avatar_assets.py` looks there |
| `aliases` | `gif` only: an existing JSON file |
| `fallback` | `gif` only: an existing 153600-byte RGB565 file, beside the `gifs` folder |
| `emoji_collection` | `image` only: a name matching `^[a-z0-9_-]+$` |

A `custom` manifest needs `renderer.cc` in the folder. Reject unknown fields, so a typo cannot go unnoticed.

Elena's manifest: `display_name` `Elena-Chan`, `renderer` `gif`, background `#F9F0E9`, accent `#D9A45B`, `accent_text` `#3B2A2A`, `name_color` `#8A5A1E`, `icon_color` `#3B2A2A`, `emotions` `gifs`, `aliases` `aliases.json`, `fallback` `neutral.rgb565`.

The emoji's: the same name and colors except background `#FFFFFF`, with `renderer` `image` and `emoji_collection` `noto-color-emoji_64`.

**Interfaces:**
- Produces, in `avatar_pack.py`: `load_manifest(avatar_dir: Path) -> dict`, raising `ManifestError` with a message naming the field or file; `render_header(manifest: dict) -> str`; `render_cmake(manifest: dict, avatar_dir: Path) -> str`. CLI: `header --avatar-dir DIR --out FILE` and `cmake --avatar-dir DIR --out FILE`, exiting 1 with the message on stderr.
- Produces, in the generated `avatar_pack_config.h`: `AVATAR_DISPLAY_NAME` (string); `AVATAR_BACKGROUND_COLOR`, `AVATAR_ACCENT_COLOR`, `AVATAR_ACCENT_TEXT_COLOR`, `AVATAR_NAME_COLOR`, `AVATAR_ICON_COLOR` (`0xRRGGBB`); and exactly one of `AVATAR_RENDERER_GIF 1`, `AVATAR_RENDERER_IMAGE 1`, `AVATAR_RENDERER_CUSTOM 1`.
- Produces, in the generated CMake: `AVATAR_RENDERER` (`gif`, `image` or `custom`), plus for `gif` `DEFAULT_EMOJI_DIRECTORY`, `DEFAULT_EMOJI_ALIASES` and `AVATAR_FALLBACK_RGB565`; for `image` `DEFAULT_EMOJI_COLLECTION`. Absolute paths, quoted.
- Produces, in `tools/build_firmware_image.py`: `set_avatar(sdkconfig: Path, name: str) -> None`, `avatar_dir(name: str) -> Path` (raising `SystemExit` for an invalid or missing avatar), and `check_avatar_assets(assets: Path, directory: Path) -> None`.

- [ ] **Step 1: Write failing tests in `firmware/scripts/tests/test_avatar_pack.py`,** building temp folders, with stdlib only:
  - `test_valid_gif_manifest_generates_header`: output contains `#define AVATAR_ACCENT_COLOR 0xD9A45B`, `#define AVATAR_DISPLAY_NAME "Elena-Chan"`, `#define AVATAR_RENDERER_GIF 1`, and not `AVATAR_RENDERER_IMAGE`.
  - `test_each_missing_required_field_stops`: a loop over each field for its renderer type, each raising `ManifestError` that names the field.
  - `test_unknown_field_stops`, `test_bad_color_stops` (`red`, `#12345`, `#GGGGGG`), `test_unknown_renderer_stops`, `test_display_name_with_quote_stops`.
  - `test_missing_gifs_folder_stops`, `test_gifs_folder_without_gif_stops`, `test_wrong_size_fallback_stops`.
  - `test_custom_requires_renderer_cc`, `test_custom_with_renderer_cc_is_valid`.
  - `test_art_only_avatar_needs_nothing_outside_its_folder`: a complete `gif` avatar in a temp dir validates and renders.
  - `test_cli_exits_1_with_the_message`: runs the CLI through `subprocess` on a bad folder.
  - `test_real_avatars_are_valid`: loads `avatars/elena` and `avatars/emoji` from the repo.
- [ ] **Step 2: Run** `cd firmware && python3 -m unittest scripts/tests/test_avatar_pack.py`. Expected: an import error for `avatar_pack`.
- [ ] **Step 3: Implement `avatar_pack.py`.** `sys.path` handling as in `test_elena_avatar_assets.py`.
- [ ] **Step 4: Move the art and write both manifests.** Fix the paths listed above. Confirm: `rg -n "elena-stackchan/assets" firmware/main firmware/scripts firmware/docs` prints nothing, and `rg -n '"assets"' firmware/scripts/elena_avatar_assets.py firmware/scripts/tests/test_elena_avatar_assets.py firmware/scripts/tests/test_elena_avatar_display.py` shows no path to the old folder.
- [ ] **Step 5: Run** `cd firmware && python3 -m unittest scripts/tests/test_avatar_pack.py scripts/tests/test_elena_avatar_assets.py scripts/tests/test_elena_avatar_display.py`. Expected: all pass.
- [ ] **Step 6: Replace the Kconfig choice.** In `main/Kconfig.projbuild`, delete `choice ELENA_AVATAR` through its `endchoice`, and add in its place:

```
config ELENA_AVATAR
    string "Elena StackChan avatar folder"
    default "elena"
    depends on BOARD_TYPE_ELENA_STACKCHAN
```

- [ ] **Step 7: Change `main/CMakeLists.txt` in three places.** Find them by the text shown.
  - In `elseif(CONFIG_BOARD_TYPE_ELENA_STACKCHAN)`, replace the `if(CONFIG_ELENA_AVATAR_EMOJI) ... endif()` block with:

```cmake
    set(BUILTIN_ICON_FONT font_material_symbols_16_4)
    set(AVATAR_DIR "${CMAKE_CURRENT_SOURCE_DIR}/boards/elena-stackchan/avatars/${CONFIG_ELENA_AVATAR}")
    idf_build_get_property(AVATAR_PYTHON PYTHON)
    foreach(AVATAR_OUTPUT "cmake;avatar_pack.cmake" "header;avatar_pack_config.h")
        list(GET AVATAR_OUTPUT 0 AVATAR_COMMAND)
        list(GET AVATAR_OUTPUT 1 AVATAR_FILE)
        execute_process(
            COMMAND "${AVATAR_PYTHON}" "${PROJECT_DIR}/scripts/avatar_pack.py" "${AVATAR_COMMAND}"
                    --avatar-dir "${AVATAR_DIR}" --out "${CMAKE_CURRENT_BINARY_DIR}/${AVATAR_FILE}"
            RESULT_VARIABLE AVATAR_RESULT ERROR_VARIABLE AVATAR_ERROR)
        if(NOT AVATAR_RESULT EQUAL 0)
            message(FATAL_ERROR "Avatar '${CONFIG_ELENA_AVATAR}': ${AVATAR_ERROR}")
        endif()
    endforeach()
    include("${CMAKE_CURRENT_BINARY_DIR}/avatar_pack.cmake")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${AVATAR_DIR}/avatar.json")
```

  - After `file(GLOB BOARD_SOURCES ...)`, replace the `if(CONFIG_ELENA_AVATAR_EMOJI)` filter with one that keeps today's rule: exclude `elena_avatar_display.cc` and `avatar_controller.cc` when `AVATAR_RENDERER` is `image`.
  - Replace `if(CONFIG_ELENA_AVATAR_ELENA)` before `set(ELENA_FALLBACK_HEADER ...)` with `if(AVATAR_FALLBACK_RGB565)`, use `${AVATAR_FALLBACK_RGB565}` for the `--rgb565` argument and its dependency, and move `list(APPEND INCLUDE_DIRS "${CMAKE_CURRENT_BINARY_DIR}")` out of it, under `if(CONFIG_BOARD_TYPE_ELENA_STACKCHAN)`, because both avatars need the generated header.
- [ ] **Step 8: In `elena_stackchan.cc`,** replace `#include "sdkconfig.h"` and the `#if CONFIG_ELENA_AVATAR_ELENA` conditions with `#include "avatar_pack_config.h"` and `#if AVATAR_RENDERER_GIF`. `#else` keeps `SpiLcdDisplay` until Task 3.
- [ ] **Step 9: Write failing tool tests.** In `test_build_firmware_image.py`, remove `AvatarTests` and its imports, and add:
  - `test_set_avatar_writes_the_string`: `CONFIG_ELENA_AVATAR="elena"` becomes `CONFIG_ELENA_AVATAR="emoji"`.
  - `test_set_avatar_refuses_a_build_without_the_option`.
  - `test_avatar_dir_refuses_bad_names`: `../x`, `/etc`, `Emoji`, empty, and a valid name with no folder, each raising `SystemExit`. Patch `image.FIRMWARE` to a temp folder.
  - `test_gif_avatar_assets_must_contain_gifs`, `test_image_avatar_assets_must_not_contain_gifs`: `check_avatar_assets` reads `renderer` from the folder's `avatar.json`.
  - In `check_image`, create `firmware/main/boards/elena-stackchan/avatars/elena/avatar.json` containing `{"renderer": "gif"}` so the existing test keeps passing.
- [ ] **Step 10: Run** `timeout 60 python3 -m unittest tools/tests/test_build_firmware_image.py`. Expected: an import error for the new names.
- [ ] **Step 11: Implement the tool.** Remove `select_avatar`, and both `AVATAR_*_OPTION` constants. `--avatar` loses `choices`. `avatar_dir` accepts only `^[a-z0-9][a-z0-9_-]*$` and an existing folder with `avatar.json` under `FIRMWARE/main/boards/elena-stackchan/avatars/`. `set_avatar` follows `set_ota_url`, replacing `^CONFIG_ELENA_AVATAR=".*"$` and raising `SystemExit` unless it matches once. In `main`: passing `--avatar` with another board is a `parser.error`. Validate the name before the build starts, in the same `try` as `resolve_selection`. The default for this board is `elena`. Remove `generated_assets.bin` and rebuild only if the name differs from `elena`. Keep `"avatar"` in `manifest.json`.
- [ ] **Step 12: Update `SKILL.md`:** `AVATAR=<name>` selects a folder of `avatars/`; `emoji` is the simple face.
- [ ] **Step 13: Verify.** Run these, in this order, with a 15-minute timeout for each build:

```sh
make test
make check-locks
make firmware-image PURPOSE="pack elena"
make firmware-image AVATAR=emoji PURPOSE="pack emoji"
make firmware-image AVATAR=nope PURPOSE="pack bad"
```

**Acceptance:**
- `make test` and `make check-locks` pass, and the firmware test count did not fall.
- The Elena image's `generated_assets.bin` has `GIF89a`, and the emoji's does not: `grep -ac GIF89a <file>` prints 0 for emoji.
- Its `manifest.json` says `"avatar": "emoji"`, and its `sdkconfig` has `CONFIG_ELENA_AVATAR="emoji"`.
- `AVATAR=nope` fails in under 30 seconds with a message naming the avatar, before compiling anything.
- `git -C firmware status --short` shows renames for the art, not deletions and additions.

- [ ] **Step 14: Commit,** `refactor(elena): move avatars to folders with a manifest` in `firmware/`, then `refactor(build): select an avatar folder by name` in the root, with `git add firmware`.

### Task 2: Renderer contract and implementations

**Purpose:** add the interface and both renderers beside the current display. Nothing uses them in production yet.

**Files:**
- Create: `firmware/main/boards/elena-stackchan/avatar_renderer.h`
- Create: `firmware/main/boards/elena-stackchan/gif_avatar_renderer.{h,cc}`, `gif_avatar_factory.cc`
- Create: `firmware/main/boards/elena-stackchan/image_avatar_renderer.{h,cc}`, `image_avatar_factory.cc`
- Create: `firmware/scripts/tests/test_avatar_renderers.py`, `firmware/scripts/tests/fixtures/avatar_renderers_test.cc`
- Modify: `firmware/main/CMakeLists.txt`

**Interfaces.** `avatar_renderer.h`, exactly:

```cpp
class AvatarArtSource {
public:
    virtual ~AvatarArtSource() = default;
    // Null until assets load, and it may change when they reload.
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

- `class GifAvatarRenderer final : public AvatarRenderer`, constructed with `explicit GifAvatarRenderer(const AvatarArtSource& art)`. For tests, public const accessors `LvglGif* gif() const` and `lv_obj_t* image() const`.
- `class ImageAvatarRenderer final : public AvatarRenderer`, same constructor. Public const accessors `lv_obj_t* image() const` and `lv_obj_t* label() const`. Public static `const char* MapEmotion(const char* emotion)`.
- Each factory file holds only the definition of `CreateAvatarRenderer`, returning its renderer. A build compiles exactly one.

**Rules for every renderer.** The caller holds the display lock for every call, so a renderer takes no lock. `Attach` runs before anything else, once. `Present` and `SetPowerSave` before `Attach` are a programming error: return without doing anything. The destructor stops any GIF.

**What `GifAvatarRenderer` takes from `elena_avatar_display.cc`.** Copy these now. Task 3 deletes the originals.

| From `ElenaAvatarDisplay` | In `GifAvatarRenderer` |
|---|---|
| `ValidAvatarGif`, `MatchesAvatarIntegrity` | Unchanged, in an anonymous namespace |
| `GetValidGif` | Same logic, using `art_.Collection()` instead of `current_theme_` |
| `ClearExpressionLocked`, `ShowStaticFallbackLocked` | Same logic, on its own `image_` and `gif_` |
| `TryExpression`, with `LcdDisplay::SetEmotion`'s GIF branch inlined | See the algorithm below |
| The second half of `ApplyPresentation`, from `auto lvgl_theme` on | See the algorithm below |
| `elena_fallback_dsc_` setup from the constructor | Constructor |
| Members `validated_*`, `applied_via_gif_`, `applied_valid_`, `applied_collection_`, `applied_identity_`, `failed_*` | Same names |
| `applied_presentation_` | `const char* applied_emotion_`, compared with `strcmp` |

`Attach(layer)`: create `image_ = lv_img_create(layer)`, and `lv_obj_center(image_)`. Never scale it.

`Present(p)`: store `last_emotion_ = p.emotion`, then if power save is off, `Show(p.emotion)`. `p.emotion` points to static storage, so the pointer may be kept. The renderer ignores `notice_active` and `maintenance`.

`SetPowerSave(on)`: store it. When on, `Show("sleeping")`. When off, `Show(last_emotion_)`, starting from `"neutral"` if nothing was presented.

`Show(emotion)`, in this order. It is today's logic: do not simplify it.

1. Let `collection = art_.Collection()` and `ptr = collection.get()`.
2. If `applied_valid_`, `applied_emotion_` equals `emotion`, and `applied_collection_ == ptr`: start the GIF if it exists and is not playing, and return.
3. `target = GetValidGif(emotion)`. If `applied_valid_`, `target` is not null, and `target->image_dsc() == applied_identity_`: record `applied_emotion_` and `applied_collection_`, clear the failure memo, start the GIF as above, and return.
4. If `failed_emotion_` equals `emotion` and `failed_collection_ == ptr`: show the fallback, record applied, and return. No decoder may open.
5. `applied = TryExpression(emotion)`. If that failed and `emotion` is not `neutral`: try `TryExpression("neutral")` once.
6. If applied: record applied, clear the memo, and return.
7. Show the fallback, set the memo to `emotion` and `ptr`, and record applied.

`TryExpression(emotion)`: `GetValidGif` or return false; `ClearExpressionLocked`; `gif_ = make_unique<LvglGif>(image->image_dsc())`; if `!gif_->IsLoaded()`, reset it and return false. Otherwise `SetFrameCallback` to `lv_image_set_src(image_, gif_->image_dsc())`, set the source, `Start()`, show `image_`, set `applied_via_gif_ = true` and `applied_identity_ = gif_->image_dsc()`, and return true. Do not call `LcdDisplay::SetEmotion` or use any emoji-font path.

**`ImageAvatarRenderer`.** `MapEmotion` table, exact, for the names `AvatarController` produces:

| Input | Output | Input | Output |
|---|---|---|---|
| `happy` | `happy` | `speaking` | `neutral` |
| `sad` | `sad` | `listening` | `neutral` |
| `thinking` | `thinking` | `look_left` | `neutral` |
| `sleeping` | `sleepy` | `look_right` | `neutral` |
| `pet` | `loving` | `email` | `neutral` |
| `shake` | `confused` | anything else | `neutral` |

`Attach`: create a centered `lv_img` and a centered label, both hidden. `Show(name)`: get `Collection()`, then its image for `MapEmotion(name)`, then for `neutral`. If one exists: skip all work if it is the one on screen, else set the source, show `image_` and hide `label_`. If none exists: hide `image_`, set `label_` to `AVATAR_DISPLAY_NAME`, and show it. Power save uses `sleeping` the same way as the GIF renderer. A collection image that is a GIF is not expected here: show it as a still source, never open a decoder.

- [ ] **Step 1: Write `avatar_renderers_test.cc`,** a harness in the style of `elena_avatar_display_test.cc`, with `main` selecting a case by `argv[1]` and reading GIFs from `argv[2]`. Reuse its `make_collection` and asset corruption modes. A `TestArt` class implements `AvatarArtSource` with a settable collection. Each case prints `PASS <name>`. Cases and what each asserts:
  - `gif_attach_centers_without_scaling`: `host_lvgl_obj_scale(image()) == 256`.
  - `gif_same_presentation_keeps_decoder`: `Present` of `happy` 20 times: `gif()` is the same pointer, and `host_lvgl_big_alloc_count()` does not change.
  - `gif_changes_ignore_notice_flags`: the same emotion with `notice_active` toggled: same `gif()`.
  - `gif_new_collection_reapplies`: a new collection, then `Present` the same emotion: the big-alloc count rises.
  - `gif_missing_emotion_uses_neutral`: a collection with only `neutral`: `gif()` is loaded.
  - `gif_corrupted_shows_fallback_without_retry`: for each of `#mutated`, `#zero`, `#truncated`, `#notgif`, with no `neutral` in the collection: `gif()` is null, the source has `LV_COLOR_FORMAT_RGB565` and `data_size == 153600`. Presenting 10 more times leaves the big-alloc count unchanged.
  - `gif_null_collection_shows_fallback`: as above, with a null collection.
  - `gif_oom_shows_fallback`: `host_lvgl_fail_next_allocations(10)` before `Present`.
  - `gif_recovers_after_reload`: a broken collection, then a good one: `gif()` loaded.
  - `gif_power_save_shows_sleeping_then_returns`: `Present(happy)`, `SetPowerSave(true)`: source differs from `happy`'s. A `Present(sad)` while on opens no decoder. `SetPowerSave(false)`: shows `sad`.
  - `gif_before_attach_is_harmless`: `Present` and `SetPowerSave` before `Attach`: no crash, no alloc.
  - `gif_destructor_stops_timer`: no live GIF timer after destruction.
  - `image_known_emotion`, `image_mapped_emotion` (`sleeping` shows the `sleepy` image), `image_unknown_uses_neutral` (`speaking` and `nonsense`), `image_empty_collection_shows_name`, `image_null_collection_shows_name`, `image_recovers_after_reload`, `image_power_save_then_returns`, `image_table_matches_plan` (each row above).
- [ ] **Step 2: Write `test_avatar_renderers.py`,** one `unittest` method per case, compiling like `test_elena_avatar_display.py`: the same `C_FILES`, the same `CPP_FILES` except `HARNESS`, `elena_avatar_display.cc` and `avatar_controller.cc`, plus this harness, both renderers, without factories. Add `avatar_controller.cc` if it fails to link. Generate `elena_avatar_fallback.h` the same way, and `avatar_pack_config.h` with `avatar_pack.py header --avatar-dir` for `avatars/elena`. Production files get `-Werror`.
- [ ] **Step 3: Run** `cd firmware && timeout 600 python3 -m unittest scripts/tests/test_avatar_renderers.py`. Expected: a compile error for the missing headers.
- [ ] **Step 4: Implement both renderers and factories.** Add stubs to the host LVGL only if the compiler demands them. A stub must only store a value.
- [ ] **Step 5: In `main/CMakeLists.txt`,** after the `BOARD_SOURCES` glob, always exclude `gif_avatar_renderer.cc`, `gif_avatar_factory.cc`, `image_avatar_renderer.cc` and `image_avatar_factory.cc` with `list(FILTER ... EXCLUDE REGEX ...)`. Nothing in production uses them until Task 3, so the image stays the same.
- [ ] **Step 6: Verify.** Run the new tests, then `make test`.

**Acceptance:**
- Every case passes, and each one failed first.
- `make test` passes. No existing test changed.
- `rg -n "LcdDisplay::SetEmotion|gif_controller_|emoji_image_|current_theme_" firmware/main/boards/elena-stackchan/gif_avatar_renderer.cc firmware/main/boards/elena-stackchan/image_avatar_renderer.cc` prints nothing.

- [ ] **Step 7: Commit** `feat(elena): add the avatar renderer contract and two renderers` in `firmware/`.

### Task 3: AvatarStage replaces ElenaAvatarDisplay

**Purpose:** one UI for both avatars. After this task the robot has the new top bar and bubble. There is no boot screen yet, so the renderer attaches in `SetupUI`.

**Files:**
- Create: `firmware/main/boards/elena-stackchan/avatar_stage.{h,cc}`, `avatar_clock.{h,cc}`
- Delete: `firmware/main/boards/elena-stackchan/elena_avatar_display.{h,cc}`
- Rename with `git mv`: `firmware/scripts/tests/test_elena_avatar_display.py` to `test_avatar_stage.py`; `firmware/scripts/tests/fixtures/elena_avatar_display_test.cc` to `avatar_stage_test.cc`
- Create: `firmware/scripts/tests/fixtures/avatar_stage_contract_test.cc`, `fake_avatar_factory.cc`, `avatar_logic_test.cc`; `firmware/scripts/tests/test_avatar_logic.py`
- Modify: `firmware/main/boards/elena-stackchan/elena_stackchan.cc`, `firmware/main/boards/elena-stackchan/README.md`, `firmware/main/CMakeLists.txt`
- Modify: host stubs in `firmware/scripts/tests/fixtures/elena_display_host/`: `lvgl.h`, `lvgl_host.c`, `assets/lang_config.h`

**Interfaces:**
- Consumes: `CreateAvatarRenderer`, `AvatarArtSource`, `AvatarController`, `avatar_pack_config.h`.
- Produces, in `avatar_stage.h`: `class AvatarStage : public SpiLcdDisplay, private AvatarArtSource`, with the same constructor arguments as `ElenaAvatarDisplay`, and these overrides: `SetupUI`, `SetEmotion`, `SetChatMessage`, `ClearChatMessages`, `SetHideSubtitle`, `SetStatus`, both `ShowNotification`, `SetPreviewImage`, `SetTheme`, `UpdateStatusBar`, `SupportsConversationVisuals` (returns true), `OnConversationVisualEvent`, `SetPowerSaveMode`.
- Protected, for test harnesses: `AvatarRenderer* renderer() const`, `lv_obj_t* status_bubble_`, `lv_obj_t* clock_label_`, `std::array<lv_obj_t*, 4> tail_`.
- Constants: `kAvatarErrorColor = 0xC62828`, `kAvatarBubbleMaxTextWidth = 176`.
- Produces, in `avatar_clock.h`: `bool FormatAvatarClock(time_t now, char* out, size_t size);`. It uses `localtime_r` and returns false when the year is before 2025, the same rule as `LvglDisplay::UpdateStatusBar`.
- Produces, also in `avatar_clock.h`: `int AvatarBubbleLabelWidth(int text_width, int max_width);`, inline, returning `min(text_width + 1, max_width)`, and at least 1.

**What goes where.**

| From `ElenaAvatarDisplay` | In `AvatarStage` |
|---|---|
| `SetupUI` | Rewritten with the layout below |
| `ApplyElenaLayerLocked` | `ApplyStageStyleLocked`, using the manifest's colors |
| `SetTheme` | Same, without the `applied_*` and `validated_*` resets; it sets `presented_valid_ = false` |
| First half of `ApplyPresentation`, up to `auto lvgl_theme` | Same, then the check below |
| `SetEmotion`, `OnConversationVisualEvent`, notice and preview methods, `ClearChatMessages`, `SetChatMessage`, `SetHideSubtitle`, `ReassertOperationalLocked`, `ClearOperationalLocked` | Unchanged |
| Notification timer | Unchanged, renamed `avatar_notification_expiry` |
| `SetStatus`, `UpdateStatusBar` | Changed as described below |
| `ShowStaticFallback`, `GetValidGif`, `TryExpression`, fallback and validation members | Deleted: they live in the GIF renderer |

End of `ApplyPresentation`: get the collection. If `presented_valid_`, the presentation equals `presented_`, and the collection pointer equals `presented_collection_`, return. Otherwise record them, and call `renderer_->Present(presentation)`.

`SetupUI`: return if `setup_ui_called_`, as the idempotence test requires. Keep creating `emoji_label_` hidden with empty text, because `LcdDisplay::SetTheme` writes to it. Keep `emoji_image_` null. `emoji_box_` is the renderer's layer. Create `renderer_ = CreateAvatarRenderer(*this)` and call `Attach(emoji_box_)`, then `ApplyPresentation(ResolveNow())`.

`Collection()`: `current_theme_ ? static_cast<LvglTheme*>(current_theme_)->emoji_collection() : nullptr`.

`SetPreviewImage`: unchanged. The base's `gif_controller_` is now null, so the renderer's GIF keeps running behind the hidden layer for the preview's few seconds. That is accepted.

`SetPowerSaveMode(on)`: take the lock or return; store `power_save_ = on`; forward to `renderer_` if attached. Never call `SetEmotion`.

`SetChatMessage` keeps today's behavior: only `system` text, only while the presentation has `notice_active` or `maintenance`. In this task, that includes the `SystemInfo::GetUserAgent()` line at boot, as it does today.

**Layout.** Positions in pixels. No shadows, gradients, or opacity below cover except the transparent bars.

| Object | Parent | Position and size | Style |
|---|---|---|---|
| `container_` | screen | 0,0, 320×240 | `AVATAR_BACKGROUND_COLOR`, opaque, no border, radius or padding |
| `emoji_box_` | `container_` | centered, 320×240 | Transparent, not scrollable, no padding |
| `top_bar_` | `container_` | 0,0, 320×30 | Transparent, not scrollable, no padding, no flex |
| `status_bubble_` | `top_bar_` | 4,3, width and height `LV_SIZE_CONTENT` | `AVATAR_ACCENT_COLOR`, opaque, radius `LV_RADIUS_CIRCLE`, horizontal padding 10, vertical 1; hidden while its text is empty |
| `status_label_` | `status_bubble_` | Width from `AvatarBubbleLabelWidth` | `AVATAR_ACCENT_TEXT_COLOR`, `LV_LABEL_LONG_MODE_DOTS` |
| `tail_[0..3]` | `container_` | Each a plain object, height 2, widths 14, 10, 6, 2, centered on x=37, at y=27, 29, 31, 33 | Same color as the bubble, no border or radius; hidden with the bubble |
| Right group | `top_bar_` | `LV_ALIGN_TOP_RIGHT`, offset -4,5, `LV_SIZE_CONTENT` | Transparent, flex row, gap 5 |
| `clock_label_`, `network_label_`, `mute_label_`, `battery_label_` | Right group | In this order | `AVATAR_ICON_COLOR`; the clock uses the text font, the rest the icon font |
| `status_bar_` (notices) | `container_` | 4,38, width 312, `LV_SIZE_CONTENT` | `AVATAR_ACCENT_COLOR`, opaque, radius 12, padding 4, hidden at start |
| `notification_label_`, `notice_label_` | `status_bar_` | Width 296 | `AVATAR_ACCENT_TEXT_COLOR`, wrapping, hidden at start |
| `low_battery_popup_`, `preview_image_` | As today | As today | Unchanged |

`SetStatus(status)`: same early returns as today, including the routine flag. Then:
1. `is_error = strcmp(status, Lang::Strings::ERROR) == 0`.
2. Text is `status`, or `"× "` plus `status` when an error. Use a local `std::string`.
3. Measure it: `lv_text_get_size(&size, text, static_cast<LvglTheme*>(current_theme_)->text_font()->font(), 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE)`. Set the label's width to `AvatarBubbleLabelWidth(size.x, kAvatarBubbleMaxTextWidth)`.
4. Show the bubble and tail, and color all of them `kAvatarErrorColor` with white text when an error, or the accent colors otherwise.

`UpdateStatusBar(update_all)`: as today, then `FormatAvatarClock(time(nullptr), ...)`: on true, set `clock_label_` and show it; on false, hide it. The `routine_status_update_` flag still rejects the base's clock.

- [ ] **Step 1: Add host stubs,** storing values only: `void lv_text_get_size(lv_point_t* size_res, const char* text, const lv_font_t* font, int32_t letter_space, int32_t line_space, int32_t max_width, lv_text_flag_t flag)`, with the real LVGL 9.5 signature, returning width `strlen(text) * 10` and height 20; `LV_TEXT_FLAG_NONE`; `LV_COORD_MAX`; `lv_point_t`; `LV_RADIUS_CIRCLE`; and `LV_ALIGN_TOP_RIGHT`, each only if missing. Add `Lang::Strings::ERROR = "XZ_STR_ERROR"`. `host_lvgl_obj_w` already reads widths. Run `python3 -m unittest scripts/tests/test_elena_avatar_display.py` from `firmware/`. Expected: still passes.
- [ ] **Step 2: Write `avatar_logic_test.cc` and `test_avatar_logic.py`,** pure C++ with no LVGL, in the style of `test_screen_stream_core.py`. Compile with `-std=c++20 -Wall -Werror -UNDEBUG`, plus `-Imain/boards/elena-stackchan -Imain/protocols -Imain/display`, and put `AvatarBubbleLabelWidth` where this binary can reach it without LVGL: `avatar_clock.h`. `setenv("TZ", "UTC0", 1); tzset();` first. Cases: `clock_hidden_at_1970`: `FormatAvatarClock(0, ...)` is false; `clock_hidden_at_2024`: a 2024-12-31 timestamp is false; `clock_formats_time`: `1793629920` (2026-11-02 14:32:00 UTC) gives `14:32`; `clock_small_buffer_is_safe`: size 3 does not overflow, and returns false; `bubble_width_keeps_short_text`: `(100, 176)` is 101; `bubble_width_caps_long_text`: `(500, 176)` is 176; `bubble_width_never_zero`: `(0, 176)` is 1.
- [ ] **Step 3: Port the existing harness.** After `git mv`, in `avatar_stage_test.cc`:
  - `ElenaHarness` becomes `class StageHarness : public AvatarStage`.
  - `gif()` returns `static_cast<GifAvatarRenderer*>(renderer())->gif()`, null-safe.
  - `emojiImage()` returns that renderer's `image()`. `emoji_src()` uses it.
  - `elena_notification_expiry` becomes `avatar_notification_expiry` everywhere.
  - `layout_idempotent` keeps every check, on the new objects.
  - No other case changes its assertions. If one cannot pass, stop and report it.
  - In `test_avatar_stage.py`: replace `elena_avatar_display.cc` with `avatar_stage.cc`, `avatar_clock.cc`, `gif_avatar_renderer.cc` and `gif_avatar_factory.cc`, and generate `avatar_pack_config.h` for `avatars/elena`, as in Task 2. Update `PRODUCTION_CPP`.
- [ ] **Step 4: Add new cases to `avatar_stage_test.cc`,** with their `run_case` calls:
  - `bubble_hidden_while_empty`: at start the bubble and every `tail_` are hidden. After `SetStatus("X")`, they show.
  - `bubble_long_text_keeps_dots_and_cap`: a 100-character status: label width is 176, and the mode is dots. The clock label's parent and position are unchanged.
  - `error_status_is_red_with_cross`: `SetStatus(Lang::Strings::ERROR)`: label text is `× XZ_STR_ERROR`, and bubble and tails have `0xC62828`. A following `SetStatus("OK")`: text `OK`, and the accent color.
  - `clock_never_writes_status`: `UpdateStatusBar(true)` leaves the status label's text unchanged.
  - `top_bar_transparent_after_theme`: `SetTheme` keeps `LV_OPA_TRANSP` on the top bar, and the manifest's background on the container.
- [ ] **Step 5: Write the contract harness.** `fake_avatar_factory.cc` defines `CreateAvatarRenderer` returning a `FakeRenderer` that records calls in a global struct: attach count and layer, presentations, power-save values. `avatar_stage_contract_test.cc` cases:
  - `attach_once`: `SetupUI` twice: attach count 1, and the layer is the 320×240 `emoji_box_`.
  - `present_only_on_changes`: 20 unchanged ticks and 20 repeated `SetEmotion("happy")`: one `Present` for it.
  - `present_on_notice_flag_change`: `NoticeStarted`: one more `Present`.
  - `theme_reload_presents_again`: same presentation with a new collection: one more.
  - `same_collection_theme_change_may_present_once`: at most one more, no crash.
  - `preview_blocks_present`: with a preview open, a new emotion gives no `Present`, and closing gives one, with the current emotion.
  - `power_save_forwarded`: `SetPowerSaveMode(true)`, then `false`: both recorded. No `Present` added, and no `SetEmotion` semantic change: `ResolveNow` is unaffected.
  - `failed_lock_calls_nothing`: `host_lvgl_port_fail_next_locks`: no renderer call.
  - `renderer_gets_no_text`: compile-time: `FakeRenderer` overrides only the three methods.
  Add this binary to `test_avatar_stage.py` as a second build, with the fake factory instead of the GIF files.
- [ ] **Step 6: Run** `cd firmware && timeout 900 python3 -m unittest scripts/tests/test_avatar_logic.py scripts/tests/test_avatar_stage.py`. Expected: failures for the missing `avatar_stage.h` and `avatar_clock.h`.
- [ ] **Step 7: Implement `avatar_clock` and `AvatarStage`.** Delete `elena_avatar_display.{h,cc}`.
- [ ] **Step 8: In `elena_stackchan.cc`:** remove the `#if AVATAR_RENDERER_GIF` blocks and `avatar_pack_config.h`. Include `avatar_stage.h`, and create `display_ = new AvatarStage(...)`. Keep `StartScreenStream()` right after it.
- [ ] **Step 9: In `main/CMakeLists.txt`,** replace both filters from Tasks 1 and 2 with this rule. For `gif`, keep `gif_avatar_renderer.cc` and `gif_avatar_factory.cc`, and exclude the image pair. For `image`, the reverse. For `custom`, exclude all four and add `"${AVATAR_DIR}/renderer.cc"` to `SOURCES`. Also in the custom case, add `boards/elena-stackchan` to `INCLUDE_DIRS`, so its `#include "avatar_renderer.h"` works. `avatar_clock.cc`, `avatar_stage.cc` and `avatar_controller.cc` are always kept. Also keep generating `elena_avatar_fallback.h` only when `AVATAR_FALLBACK_RGB565` is set.
- [ ] **Step 10: Update the board's `README.md`:** the avatar is a folder; it has one UI.
- [ ] **Step 11: Verify,** with `make test`, `make check-locks`, then both image builds as in Task 1.

**Acceptance:**
- All tests pass. Every existing display case still runs under its original selector.
- `rg -n "Elena|elena_avatar|gif|emoji" firmware/main/boards/elena-stackchan/avatar_stage.cc` shows no reference to a specific avatar, GIF decoder or art file. `emoji_box_`, `emoji_label_` and `emoji_collection` are allowed.
- `rg -n "avatars/|AVATAR_RENDERER|ElenaAvatar" firmware/main/boards/elena-stackchan/elena_stackchan.cc` prints nothing.
- Both avatars share the controller: `grep -ac audio_idle_timeout <xiaozhi.bin>` prints at least 1 in the emoji image as well, which printed 0 before this task.
- Other boards are untouched: `git diff --stat <start>.. -- firmware/main/display firmware/main/application.cc` is empty.

- [ ] **Step 12: Commit** `refactor(elena): share one UI through AvatarStage` in `firmware/`, saying in the body that it has not run on the robot, and record the pointer in the root.

### Task 4: Boot screen and server gate

**Purpose:** the boot screen, the server check, and loading the avatar only when ready.

**Files:**
- Create: `firmware/main/display/boot_step.h`
- Create: `firmware/main/protocols/server_endpoint.{h,cc}`, `firmware/main/protocols/server_probe.{h,cc}`
- Create: `firmware/main/boards/elena-stackchan/boot_view.{h,cc}`, `boot_screen.{h,cc}`
- Use, already committed: `avatars/elena/boot_image.c`, `boot_name.c`, and the same for `emoji`
- Modify: `firmware/main/display/display.h`, `firmware/main/application.cc`, `firmware/main/application.h`, `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/boards/elena-stackchan/avatar_stage.{h,cc}`
- Modify: `firmware/scripts/tests/fixtures/avatar_logic_test.cc`, `avatar_stage_test.cc`, `avatar_stage_contract_test.cc`, `test_avatar_logic.py`, `test_avatar_stage.py`, and the host stubs

**Interfaces:**
- `boot_step.h`: `enum class BootStep { kFindingServer, kServerFailed, kReady };`. There is no Wi-Fi step: the boot screen starts on it.
- `Display`, after `SupportsConversationVisuals`: `virtual void OnBootProgress(BootStep step, int retry_seconds) { (void)step; (void)retry_seconds; }` and `virtual bool RequiresServerAtBoot() const { return false; }`.
- `server_endpoint.h`: `struct ServerEndpoint { std::string host; uint16_t port = 0; };` and `std::optional<ServerEndpoint> ParseWebsocketEndpoint(std::string_view url);`.
- `server_probe.h`: `bool ProbeServer(const ServerEndpoint& endpoint, int timeout_ms);`.
- `boot_view.h`: `struct BootView { const char* bubble; bool is_error; int retry_seconds; };`, `BootView InitialBootView();`, `BootView ResolveBootView(BootStep step, int retry_seconds);`, `std::string BootCountdownText(int retry_seconds);`.
- `boot_screen.h`: `class BootScreen` with `BootScreen(lv_obj_t* parent, const lv_image_dsc_t* image, const lv_image_dsc_t* name_image, const char* version)`, `~BootScreen()` deleting its root, `void Show(const BootView& view)`, `void ShowBubble(const char* text, bool is_error)`, `void SetNote(const char* text)`, and const accessors `root()`, `image()`, `name()`, `bubble()`, `bubble_label()`, `countdown()`, `note()`, `version()`.
- Symbols from each avatar's committed files, declared in `avatar_stage.cc`: `extern "C" const lv_image_dsc_t avatar_boot_image;` and `extern "C" const lv_image_dsc_t avatar_boot_name;`. Both are `LV_COLOR_FORMAT_RGB565A8`. The name is an image, already in `name_color`: do not recolor it.

**Copy.** `InitialBootView`: `Conectando a Wi-Fi...`. `kFindingServer`: `Buscando al servidor...`. `kServerFailed`: `× No encuentro el servidor`, `is_error` true. `kReady`: empty text. `BootCountdownText(8)`: `Reintento en 8 s`, and empty for 0.

**Boot screen layout,** its root filling 320×240 over everything, as the last child of the screen, in `AVATAR_BACKGROUND_COLOR`:

| Object | Position | Style |
|---|---|---|
| `image` | Top center, y=8 | `avatar_boot_image` |
| `name` | Top center, y=133 | An `lv_img` showing `avatar_boot_name` |
| `bubble` | Top center, y=181, `LV_SIZE_CONTENT` | Radius `LV_RADIUS_CIRCLE`, accent colors; `kAvatarErrorColor` with white text on error |
| `countdown` | Top center, y=211 | `AVATAR_ACCENT_TEXT_COLOR`; hidden when empty |
| `version` | Bottom right, offset -8,-5 | `AVATAR_ICON_COLOR`, text `v` plus the version |
| `note` | 10,44, width 300, wrapping | `AVATAR_ACCENT_TEXT_COLOR`; hidden when empty |

`SetNote` with text hides `image` and `name`, and moves the bubble to y=8, because hints with a Wi-Fi name and URL are long. `SetNote("")` restores them.

**Stage behavior while booting.** `boot_` is a `std::unique_ptr<BootScreen>`, created last in `SetupUI`, and `ready_` starts false. `top_bar_`, the tail and `emoji_box_` stay hidden until ready. Move `CreateAvatarRenderer` and `Attach` from `SetupUI` to the ready transition.

| Call while booting | Behavior |
|---|---|
| `SetStatus(s)` | If `ResolveNow().notice_active`: `boot_->ShowBubble(s, s is the error string)`. Otherwise ignore it: maintenance status must not replace the step |
| `SetChatMessage("system", s)` | If `notice_active`: `boot_->SetNote(s)`. Otherwise ignore it, which drops the user-agent line |
| `ShowNotification` | Ignored. The SSID must not appear there, and the bubble has the step |
| `SetPreviewImage` with an image | Ignored, with a log line |
| `ApplyPresentation` | Stores `last_requested_`; no renderer call. If `notice_active` is false: `boot_->SetNote("")` |
| `SetPowerSaveMode(on)` | Stores `power_save_` only |
| `UpdateStatusBar` | As usual: the icons update while hidden |
| `SetTheme` | Applies style; no renderer call |

`OnBootProgress(step, seconds)`, taking the lock or returning. After ready, it does nothing. For `kReady`: create and `Attach` the renderer, forward `power_save_` if on, delete `boot_`, show `emoji_box_` and `top_bar_`, set `ready_`, and `ApplyPresentation(ResolveNow())`. Otherwise: `boot_->Show(ResolveBootView(step, seconds))`, and `SetNote("")` for `kServerFailed`.

`RequiresServerAtBoot()` returns true.

**`ParseWebsocketEndpoint`:** accepts only `ws://` and `wss://`, case-sensitive. The host runs to the next `:`, `/` or end, and must not be empty. A `:port` must be 1 to 65535, digits only. Defaults: 80 for `ws`, 443 for `wss`. IPv6 literals in brackets return `nullopt`: the project's addresses never use them.

**`ProbeServer`:** `getaddrinfo` with `AF_INET`, `SOCK_STREAM`; false on failure. A socket; set `O_NONBLOCK`; `connect`. On immediate success, true. On `EINPROGRESS`, `select` for write with `timeout_ms`, then check `SO_ERROR` is 0. On every path, `close` the socket and `freeaddrinfo`. Send nothing. Include `lwip/sockets.h` and `lwip/netdb.h`.

**`application.cc` edits.** Only these five. Find each by the text shown.

1. `application.h`, private members: `std::atomic<bool> waiting_for_server_{false};` and `void WaitForServerAtBoot();`. Add `#include <atomic>` if it is missing.
2. In `ActivationTask`, as the first line: `Board::GetInstance().GetDisplay()->OnBootProgress(BootStep::kFindingServer, 0);`. After `CheckNewVersion();`, add `WaitForServerAtBoot();`.
3. New function, near `CheckNewVersion`:

```cpp
void Application::WaitForServerAtBoot() {
    auto display = Board::GetInstance().GetDisplay();
    if (!display->RequiresServerAtBoot() || ota_->HasMqttConfig()) {
        return;
    }
    while (true) {
        Settings settings("websocket", false);
        auto endpoint = ParseWebsocketEndpoint(settings.GetString("url"));
        if (endpoint && ProbeServer(*endpoint, 3000)) {
            waiting_for_server_.store(false);
            display->OnBootProgress(BootStep::kFindingServer, 0);
            return;
        }
        ESP_LOGW(TAG, "Server not reachable, retrying");
        // Lets the power save timer run: in Activating, CanEnterSleepMode says no.
        waiting_for_server_.store(true);
        for (int seconds = 8; seconds > 0; --seconds) {
            display->OnBootProgress(BootStep::kServerFailed, seconds);
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
        display->OnBootProgress(BootStep::kFindingServer, 0);
    }
}
```

   **Do not** copy the `GetDeviceState() == kDeviceStateIdle` check from `CheckNewVersion`. A touch moves `Activating` to `Idle`, and that must not end this wait.

4. In `HandleActivationDoneEvent`, right after `SetDeviceState(kDeviceStateIdle);`:

```cpp
    auto boot_display = Board::GetInstance().GetDisplay();
    boot_display->OnBootProgress(BootStep::kReady, 0);
    if (boot_display->RequiresServerAtBoot()) {
        // Brings the screen back if the boot wait dimmed it. LOW_POWER follows below.
        Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    }
```

5. In `CanEnterSleepMode`, change the first condition to `if (GetDeviceState() != kDeviceStateIdle && !waiting_for_server_.load()) {`.

`application.cc` needs `#include "server_endpoint.h"` and `#include "server_probe.h"`, flat like its `#include "websocket_protocol.h"`, and `#include "settings.h"` if missing. `display.h` includes `boot_step.h`. Add `protocols/server_endpoint.cc` and `protocols/server_probe.cc` to the common source list beside the other `protocols/` entries in `main/CMakeLists.txt`.

**Assets.** In the avatar branch of CMake, add `"${AVATAR_DIR}/boot_image.c"` and `"${AVATAR_DIR}/boot_name.c"` to `SOURCES`. `avatar_pack.py`'s `load_manifest` also requires both to exist, plus `boot.png`, for every renderer type. Add `test_missing_boot_files_stop` and update its temp-folder fixtures. A fixture's boot files may be empty: `load_manifest` checks only that they exist.

- [ ] **Step 1: Extend `avatar_logic_test.cc`:**
  - `parse_ws_with_port`: `ws://192.0.2.1:8000/xiaozhi/v1/` gives `192.0.2.1`, 8000.
  - `parse_defaults`: `ws://example.test/` gives 80, and `wss://example.test` gives 443.
  - `parse_rejects`: empty, `http://x`, `ws://`, `ws://:80`, `ws://x:0`, `ws://x:65536`, `ws://x:80a`, `ws://[::1]:80`, each `nullopt`.
  - `boot_views`: each exact string and `is_error` value above.
  - `boot_countdown`: `Reintento en 8 s`, `Reintento en 1 s`, and empty for 0.
  - `boot_copy_has_only_available_glyphs`: no byte sequence for `…` or `✕` in any string.
- [ ] **Step 2: Extend `avatar_stage_test.cc` and its contract harness.** Add a stub `esp_app_desc.h` to the host folder, with `esp_app_get_description()` returning version `2.5.0` and defined in `host_support.cc`. Define `avatar_boot_image` and `avatar_boot_name` as zeroed `lv_image_dsc_t` objects in each harness. Before this task's stage changes, make every existing case call `display->OnBootProgress(BootStep::kReady, 0)` right after each `SetupUI`, through one helper, so their assertions stay unchanged. New cases, in the contract harness unless noted:
  - `boot_shows_wifi_first`: after `SetupUI`: bubble text `Conectando a Wi-Fi...`, and version `v2.5.0`.
  - `boot_hides_ui`: top bar, tail and layer are hidden.
  - `no_attach_before_ready`: attach count 0, through ticks, emotions and every step but ready.
  - `boot_steps_update_the_bubble`, including the countdown `Reintento en 8 s` and `is_error` color, then `Buscando al servidor...` again with the countdown hidden.
  - `failure_then_ready_loads_avatar`: failure, then ready: attach 1, boot objects gone (`host_lvgl_live_obj_count` falls), top bar and layer visible.
  - `ready_applies_the_latest_presentation`: `SetEmotion("happy")` before ready: the first `Present` has `happy`.
  - `ready_twice_is_harmless`: attach stays 1, no crash.
  - `progress_after_ready_is_ignored`: `kServerFailed` after ready: no crash, nothing re-created.
  - `user_agent_not_shown`: `SetChatMessage("system", "UA-STRING")` with maintenance: `UA-STRING` is absent from the tree.
  - `notice_during_boot_shows_note`: `NoticeStarted`, `SetStatus("ST")`, `SetChatMessage("system", "LONG-HINT")`: the bubble says `ST`, the note says `LONG-HINT`, and image and name are hidden. `NoticeCleared`: note hidden, image and name visible.
  - `error_alert_during_boot_uses_error_color`.
  - `notification_during_boot_hidden`: `ShowNotification("Conectado a MI-RED")` leaves `MI-RED` absent from the tree.
  - `preview_during_boot_ignored`: no crash, and the preview stays hidden.
  - `power_save_during_boot`: `SetPowerSaveMode(true)` before ready: no crash. After ready: `SetPowerSave(true)` reaches the renderer.
  - `failed_lock_during_progress_does_nothing`.
  - `real_gif_after_ready` (in `avatar_stage_test.cc`): `gif()` is null before ready, and loaded after.
- [ ] **Step 3: Run them.** Expected: failures for the missing files.
- [ ] **Step 4: Implement** in this order, running tests after each: `server_endpoint`, `boot_view`, `BootScreen`, stage behavior.
- [ ] **Step 5: Implement `ProbeServer`, the five application edits, and CMake.**
- [ ] **Step 6: Check other boards are untouched by behavior:** `git -C firmware diff <start>.. -- main/application.cc` shows only the five edits and two includes.
- [ ] **Step 7: Verify,** with `make test`, `make check-locks`, then:

```sh
make firmware-image PURPOSE="stage elena"
make firmware-image AVATAR=emoji PURPOSE="stage emoji"
make firmware BOARD=m5stack/core-s3 BOARD_VARIANT=m5stack-core-s3
```

**Acceptance:**
- All tests pass, and all three builds succeed.
- The app stays under its partition. Compare the size of `xiaozhi.bin` in `stage elena` with the `pack elena` image from Task 1: the growth is under 100 KB.
- `grep -ac "No encuentro el servidor" <xiaozhi.bin>` is at least 1 in both avatar images, and the app of `m5stack/core-s3` still links.
- `rg -n "…|✕" firmware/main/boards/elena-stackchan/*.cc` prints nothing.
- `rg -n "kDeviceStateIdle" firmware/main/application.cc` shows no new line inside `WaitForServerAtBoot`.
- `git -C firmware status --short main/boards/elena-stackchan/avatars/fonts` is empty, and `git -C firmware diff <start>.. --stat -- '*boot_image.c' '*boot_name.c' '*boot.png'` is empty.

- [ ] **Step 8: Commit** `feat(elena): add the boot screen and wait for the server` in `firmware/`, saying in the body that it has not run on the robot, and record the pointer in the root.

### Task 5: Documentation and handoff

**Files:**
- Create: `docs/avatars.md`
- Modify: `AGENTS.md`, `docs/screen-stream.md`, `docs/tested-versions.md`, `firmware/main/boards/elena-stackchan/README.md`

- [ ] **Step 1: Write `docs/avatars.md`:** what stays fixed; the manifest's fields from Task 1; how to add an art-only avatar in numbered steps, including generating its boot art with the command at the top of `scripts/avatar_boot_assets.py`, which needs Pillow and is run by hand; how to add a custom renderer, with the renderer rules from Task 2; that a `gif` avatar's GIFs must be 320×240 with a same-named fallback, because the integrity check requires them.
- [ ] **Step 2: Update `AGENTS.md`:** the `firmware-image` line says `AVATAR=<folder>`, and the layout mentions `docs/avatars.md`.
- [ ] **Step 3: Update `docs/tested-versions.md`:** the commit, marked as built and host-tested, not run on the robot. Add to `docs/screen-stream.md`: the stream reads the panel flush, so it needs no change per avatar.
- [ ] **Step 4: Run** `make test` and `make check-locks`.
- [ ] **Step 5: Commit** `docs: explain how to add an avatar`, with the final pointer, in the root.
- [ ] **Step 6: Report to the operator:** each commit; the test counts; both image paths; anything skipped; every place you deviated from this plan and why. **Stop here.** Do not flash.

### Task 6: Validation on the robot (operator only)

Each write needs the user's confirmation, through the `stackchan-flash` skill.

- [ ] **Step 1: Build with the stream:** `make firmware-image SCREEN_STREAM=1 PURPOSE="stage elena"` and the same with `AVATAR=emoji`.
- [ ] **Step 2: Flash Elena.** Keep the serial log open except during the write.
- [ ] **Step 3: Check the boot with the server on:** steps in order, the sound, then the avatar. No `Guru`, `task_wdt` or `abort` in the log. Elena's art and name look right on the panel.
- [ ] **Step 4: Stop the server and reset.** Expected: the failure screen in red, a countdown, no avatar.
- [ ] **Step 5: Touch the screen during the failure, and say the wake word.** Expected: it stays on the failure screen, with no crash. The log may say `Protocol not initialized`.
- [ ] **Step 6: Start the server.** Expected: it finishes without a reset, the sound plays, the avatar appears at full brightness.
- [ ] **Step 7: On battery, leave the failure screen for over 5 minutes.** Expected: it dims after about 1 minute, and powers off after about 5. Starting the server before that restores full brightness.
- [ ] **Step 8: Wi-Fi setup path, if practical:** with no saved network, the hint is readable on the boot screen. Record it as not tested if you skip it.
- [ ] **Step 9: Measure voice:** a 5-minute story with a viewer. Expected: under 0.1% `audio_dropped` with a warm cache, 0 `mic_frames_dropped`, and `hook_us_per_frame` near 2.5 ms. A worse figure stops the work for a decision.
- [ ] **Step 10: Compare the UI:** capture `latest.png` in each state on Elena, flash the emoji, and repeat Steps 3, 4, 6 and 9. The bar, bubble and tail match in position. Check that the tail and bubble do not hide the figure on any GIF.
- [ ] **Step 11: Server failure after boot:** stop the server while idle, and speak. Expected: the avatar stays, and the bubble turns red with `×`.
- [ ] **Step 12: Record the results,** with what was and was not tested, in `docs/avatar-validation.md`, `docs/screen-stream.md` and `docs/tested-versions.md`. Commit, and ask before pushing.

## Definition of done

- Tasks 1 to 5 pass their acceptance with no robot.
- Task 6 passes on the robot, or each gap is recorded.
- A new art-only avatar needs only its folder. That is proven by `test_art_only_avatar_needs_nothing_outside_its_folder`, and `avatar_stage.cc`, `elena_stackchan.cc` and `application.cc` name no avatar.
