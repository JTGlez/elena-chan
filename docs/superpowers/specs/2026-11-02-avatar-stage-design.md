# Avatar stage: one UI, interchangeable avatars

Approved in conversation on 2026-11-02. Mockups used for the visual decisions are in `.superpowers/brainstorm/` (not tracked).

## Goal

Every avatar on the `elena-stackchan` board shares one stable UI, so the robot behaves and looks the same and captures from the screen stream are comparable. A new avatar is an addition, not a change: it brings its own drawing and art, and nothing in the UI changes (open for extension, closed for modification).

The robot shows a themed boot screen until everything is ready. If the server is unavailable, the error appears there, and the avatar never loads.

## Decisions

| Topic | Decision |
|---|---|
| Choosing an avatar | At build time: `make firmware-image AVATAR=<name>`. One avatar per image |
| Kinds of avatar | Any drawing method, behind an interface: GIF, still image, later drawn by code |
| Status | A status bubble only. No conversation subtitles on any avatar |
| Top bar | Transparent, so it never cuts the avatar. Bubble on the left with a small tail; clock, Wi-Fi and battery on the right |
| Boot layout | Medallion: image, name, and the current step in a floating bubble below |
| Boot image for Elena | `assets/elena-readme.png`, reduced to 132×123 |
| Boot name | "Elena-Chan" in Cherry Bomb One, drawn as an image, because it is the only text in that typeface |
| Palette | Laurel: accent `#D9A45B`, accent text and icons `#3B2A2A`, name `#8A5A1E`, background `#F9F0E9` |
| Error color | Red `#C62828` with a `×`, used only for failures and common to every avatar. The built-in font has no `✕` |
| No server at boot | Stays on the boot screen, retrying without limit. The avatar does not load |

## Architecture

| Unit | Responsibility | Changes for a new avatar? |
|---|---|---|
| `AvatarStage` | Boot screen, top bar, status bubble, notices. A `SpiLcdDisplay` | No |
| `AvatarRenderer` | Interface that draws the avatar in its own layer | No |
| `GifAvatarRenderer`, `ImageAvatarRenderer` | The two first implementations | Only for a new way of drawing |
| `AvatarController` | Exists today. Turns conversation events into an `AvatarPresentation` | No |
| `avatars/<name>/` | One avatar's manifest, art, and optional renderer | It is the addition |

`ElenaAvatarDisplay` is split: its layout goes to `AvatarStage`, and its GIF handling to `GifAvatarRenderer`. Its integrity checks on GIFs stay with that renderer. The `AVATAR=emoji` path through a plain `SpiLcdDisplay` is removed.

### The renderer contract

```cpp
class AvatarRenderer {
public:
    virtual ~AvatarRenderer() = default;
    virtual void Attach(lv_obj_t* layer) = 0;
    virtual void Present(const AvatarPresentation& presentation) = 0;
    virtual void SetPowerSave(bool on) = 0;
};
```

Rules, each from something measured on this robot:

- The stage calls every method holding the LVGL lock.
- `Present` runs only when the presentation changes. Taking that lock on every tick cost microphone frames before.
- A renderer never blocks and does no heavy work in `Present`. Timed animation runs from LVGL timers.
- A renderer draws only inside its layer, which fills 320×240 under the UI.
- The top 30 pixels are reserved: they may show background, not content that matters.
- A renderer must always show something. If its art fails, it falls back as the GIF path does today.
- `SetPowerSave(true)` shows a sleeping look until `SetPowerSave(false)`, without changing the conversation state.

A renderer receives no conversation text, no state text and no network state. That is what keeps the UI uniform. Its only other input is the art a build gave it, through a small read-only source: when assets reload, the stage presents again, and the renderer notices the new art.

### The avatar folder

```text
firmware/main/boards/elena-stackchan/avatars/<name>/
├── avatar.json        # Manifest
├── renderer.cc        # Optional: a renderer of its own
├── boot.png           # Source of the boot image, with transparency
├── boot_image.c       # Generated, committed: the boot image as C data
├── boot_name.c        # Generated, committed: the display name as an image
└── ...                # The renderer's art: GIFs and aliases, for example
```

```json
{
  "display_name": "Elena-Chan",
  "renderer": "gif",
  "background": "#F9F0E9",
  "accent": "#D9A45B",
  "accent_text": "#3B2A2A",
  "name_color": "#8A5A1E",
  "icon_color": "#3B2A2A",
  "emotions": "gifs",
  "aliases": "aliases.json",
  "fallback": "neutral.rgb565"
}
```

`renderer` names a built-in renderer, or `custom` when the folder has its own `renderer.cc`. The emoji avatar uses `"renderer": "image"` and `"emoji_collection": "noto-color-emoji_64"` in place of `emotions`, `aliases` and `fallback`.

Selection:

- `AVATAR` defaults to `elena`. `CONFIG_ELENA_AVATAR` becomes a string option, so a new folder needs no Kconfig entry. It replaces the current `ELENA_AVATAR_ELENA` and `ELENA_AVATAR_EMOJI` choice.
- `main/CMakeLists.txt` reads the manifest through a new script, `scripts/avatar_pack.py`. The script validates it and generates a header with the colors and the name, and selects the renderer's source. A missing field or file stops the build.
- `tools/build_firmware_image.py` keeps checking that the assets match the selected avatar, and records `avatar` in the manifest. The check reads the manifest instead of assuming GIFs mean Elena.
- `boot_image.c` and `boot_name.c` are generated by `scripts/avatar_boot_assets.py` and committed, so a build does not need Pillow. The typeface and its OFL license live in `avatars/fonts/`.

The board's own files must not name any specific avatar.

## Boot

The boot image and name are compiled into the app, because assets load only after Wi-Fi, about 2.4 s into a boot. Everything else in the boot screen uses the built-in Noto Sans.

| Step | Bubble |
|---|---|
| Wi-Fi | "Conectando a Wi-Fi..." |
| Server | "Buscando al servidor..." |
| Failure | `× No encuentro el servidor`, with a countdown below it |
| Ready | The existing ready sound plays, then the avatar and top bar appear |

New `Display` virtuals, with defaults that do nothing so other boards are unaffected:

```cpp
virtual void OnBootProgress(BootStep step, int retry_seconds) {}  // Finding server, failed, ready
virtual bool RequiresServerAtBoot() const { return false; }
```

`Application` calls `OnBootProgress` at each step. The boot screen starts on the Wi-Fi step by itself. The changes to `application.cc` are these calls, the probe, and the power-off condition.

### Server check

Today the firmware boots without the server on purpose: `CheckNewVersion` falls back to stored settings. That stays for other boards. When `RequiresServerAtBoot()` is true, `ActivationTask` runs a probe after `CheckNewVersion`:

- It takes the host and port from the WebSocket URL in the settings, and attempts a TCP connection with a 3 s timeout. No WebSocket or audio session opens.
- On failure it reports the failure step, waits 8 s, and tries again without limit.
- With an MQTT configuration, such as the Xiaozhi cloud, it skips the probe, because there is no WebSocket URL to test.
- URL parsing lives in a unit that host tests can run.

### Other states

- Wi-Fi setup and activation codes show on the boot screen, using the same bubble and the notice area below it. The avatar does not load.
- The automatic power-off must also run during retries, so a robot left without a server does not drain its battery. It dims after 1 minute and turns off after 5. Today `CanEnterSleepMode` allows this only in `Idle`, so `application.cc` lets it count while the server wait is failing. When boot finishes, the screen returns to full brightness.
- A touch or the wake word during the wait must not end it or load the avatar. Today a touch moves `Activating` to `Idle`, so the wait must not depend on the state.
- If the server fails after boot, the avatar stays, and the bubble turns red with a `×`. It does not return to the boot screen.

## Running UI

- Status text comes from `SetStatus`. Routine updates stay rejected as they are today, and the clock moves to its own label.
- The clock stays hidden until the server's time arrives. The icons are always shown.
- The bubble uses dots for long text and never scrolls. A moving subtitle made nearly every frame new and defeated the stream's strip cache.
- Conversation text never shows, for any avatar. Operational notices keep their current path through `SetChatMessage`.
- Notices keep their own strip below the top bar, in the same bubble style.

## Screen stream

The stream reads pixels at the panel flush, so any renderer is captured with no changes to it. The existing `SCREEN_STREAM=1` flag combines with `AVATAR=<name>`.

## Testing

Tests come first for anything that can run on the host.

| Where | What |
|---|---|
| Host | The stage drives a fake renderer: `Present` only on changes, `Attach` once, power save forwarded |
| Host | Boot transitions, including failure and retry, and that the avatar loads only after ready |
| Host | WebSocket URL parsing for the probe |
| Host | `scripts/avatar_pack.py`: valid manifests, and each missing field or file stopping the build |
| Build | Images for `elena` and `emoji`, with the expected code and assets |
| Robot | Boot with and without the server, from the viewer |
| Robot | Captures of every state on both avatars, compared side by side |
| Robot | One voice session with a viewer on each avatar, comparing `audio_dropped`, microphone frames and refresh time with `docs/screen-stream.md` |

Acceptance on the robot:

- Without a server, the failure shows and the avatar does not load. Starting the server lets it finish without a reset.
- Both avatars show the same top bar and bubble in the same place.
- Bubble and top bar never hide a figure below the reserved area.
- Voice with a viewer connected stays at the current level: under 0.1% of packets dropped on Elena with a warm cache, and no lost microphone frames.
- A new avatar made only of art builds by adding a folder, with no change outside it. This is proven with a small test avatar in the host tests of the build script.

## Out of scope

- Choosing an avatar at run time. The interface does not prevent adding it later.
- A renderer drawn by code. Only the interface that allows it is built now.
- Boot animation beyond the changing bubble.
- The frame queue that would make the stream smoother.
- The TTS and LLM failures.

## Risks

- A rounded bubble over a full-screen GIF redraws with every GIF frame. The strip it sits on already redraws, but its cost is measured on the robot rather than assumed.
- The boot image adds about 48 KB and the name image about 8 KB to the app. It uses 72% of its partition today, so this fits.
- The probe adds a server dependency to boot on this board only. That is the requested behavior, and other boards keep theirs.
- `application.cc` is an upstream file. Changes there stay limited to the hooks named here, to keep merges simple.
