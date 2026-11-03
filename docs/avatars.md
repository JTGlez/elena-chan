# Avatars

Every avatar of the `elena-stackchan` board shares one UI. A new avatar is an added folder of `firmware/main/boards/elena-stackchan/avatars/`, and one avatar goes into each image:

```sh
make firmware-image AVATAR=<folder> PURPOSE="..."
```

The default is `elena`. `emoji` is the simple face. A name with no folder stops the command before it builds.

## What stays fixed

`AvatarStage` owns everything except the avatar's drawing, and it is the same for every avatar:

- The boot screen and its steps, the server check, and the failure screen.
- The transparent top bar: status bubble on the left, clock, connection, mute and battery on the right. The top 30 pixels are reserved for it.
- The strip for operational notices.
- The red error color, `#C62828`, with a `×`.
- No conversation subtitles.

An avatar chooses its colors, its art and how it draws.

## The folder

```text
avatars/<name>/
├── avatar.json     # The manifest
├── boot.png        # Source of the boot image, with transparency
├── boot_image.c    # Generated and committed
├── boot_name.c     # Generated and committed
├── renderer.cc     # Only for a custom renderer
└── ...             # The renderer's art
```

### The manifest

All fields are required unless noted. An unknown field stops the build, so a typo cannot go unnoticed.

| Field | Rule |
|---|---|
| `display_name` | Non-empty string, at most 20 characters, without `"`, `\` or control characters |
| `renderer` | `gif`, `image` or `custom` |
| `background`, `accent`, `accent_text`, `name_color`, `icon_color` | `#RRGGBB` |
| `emotions` | `gif` only: must be `gifs`, a folder holding at least one `.gif` |
| `aliases` | `gif` only: a JSON file in the avatar's folder |
| `fallback` | `gif` only: a 153600-byte RGB565 file in the avatar's folder, beside `gifs` |
| `emoji_collection` | `image` only: a name matching `^[a-z0-9_-]+$` |

`scripts/avatar_pack.py` validates the manifest during the build, and a failure names the field or file.

## Add an avatar made only of art

1. Create `avatars/<name>/`. The name matches `^[a-z0-9][a-z0-9_-]*$`.
2. Add the art: a `gifs` folder, `aliases.json` and the fallback for `gif`, or nothing for `image`, which uses a built-in collection. See [GIF art](#gif-art).
3. Write `avatar.json`.
4. Add `boot.png`, and generate the boot art. This needs Pillow, so run it by hand from `firmware/` and commit the output:

   ```sh
   ../.venv-firmware/bin/python scripts/avatar_boot_assets.py \
       --avatar-dir main/boards/elena-stackchan/avatars/<name> \
       --source <image> \
       --font main/boards/elena-stackchan/avatars/fonts/CherryBombOne-Regular.ttf \
       --display-name "<display_name>" --name-color "<name_color>"
   ```

5. Build it with `make firmware-image AVATAR=<name> PURPOSE="..."`. Nothing outside the folder changes.
6. Test it on the robot through the `stackchan-flash` skill. A successful build does not validate hardware.

### GIF art

The GIF renderer checks each file before it opens a decoder:

- Each GIF is 320×240 and is named after the emotion it shows, such as `happy.gif`. `avatar_controller.cc` lists those names, and `neutral.gif` is the one the others fall back to.
- The fallback is the still image for when no GIF can be shown. Generate it from the neutral GIF with `elena_avatar_assets.py fallback`.
- The build records each file's size and CRC, so a damaged file falls back instead of reaching the decoder.

The figure should leave the top 30 pixels free, because the top bar covers them.

## Add a custom renderer

Set `"renderer": "custom"` and add `renderer.cc` to the folder. It includes `avatar_renderer.h`, implements `AvatarRenderer`, and defines the factory:

```cpp
std::unique_ptr<AvatarRenderer> CreateAvatarRenderer(const AvatarArtSource& art) {
    return std::make_unique<MyRenderer>(art);
}
```

Rules, each from something measured on this robot:

- The stage holds the display lock for every call, so a renderer takes none.
- `Attach` runs first, once, when the robot is ready. Before it, the other methods do nothing.
- `Present` runs only when the presentation changes. It must not block or do heavy work; timed animation runs from LVGL timers. Taking the display lock on every tick once cost microphone frames.
- Draw only inside the layer, which fills 320×240 under the UI.
- Always show something. If the art fails, fall back.
- `SetPowerSave(true)` shows a sleeping look until `SetPowerSave(false)`, without changing the conversation state.
- `AvatarArtSource::Collection()` is null until assets load, and it may change when they reload.

A renderer receives no conversation, state or network text. That is what keeps the UI uniform.
