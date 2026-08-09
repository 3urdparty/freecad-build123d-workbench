# Repository Media

Required software: `uv`, Chromium, FreeCAD 1.0.2, and FFmpeg.

Generate every committed asset with:

```console
uv run --script assets/media/generate.py all
```

Individual commands are `social`, `gallery`, `opening-frame`, `hero`, `verify`, and
`verify-static`. `opening-frame` writes the uncommitted 1200 x 750 approval image to
`.media-cache/hero-opening-frame.png`; `verify-static` checks only browser-generated
social and gallery assets.

Outputs are `assets/social-preview/social-preview.png`, the three gallery PNGs under
`docs/assets`, and `hero-demo.gif`. The
hero post-processing uses feathered focus apertures, activity rails, and brief result
pulses to hand attention from the edited source line to the recomputed geometry and
parameter-driven bore change.

The hero capture uses locally installed OpenDark/OpenPreferences and FreeCAD Ribbon UI
add-ons in its disposable profile.

Set `FREECAD_BIN`, `FFMPEG_BIN`, or `SOCIAL_PREVIEW_BROWSER` to override executable
discovery. The temporary `.media-cache/` contains a disposable FreeCAD profile,
preserved managed kernel, scenario copies, frames, and result manifests; it is ignored
by Git. No real FreeCAD preferences or example source is modified.

The UI baseline deliberately pins FreeCAD 1.0.2 in `freecad_capture.py`. To update it,
install the intended FreeCAD version, change that checked-in constant, regenerate all
assets, and review the resulting visual diff.

If Chromium is missing, install Playwright Chromium or set `SOCIAL_PREVIEW_BROWSER`.
If FreeCAD or FFmpeg is missing, set their corresponding override variables. Kernel
timeouts identify the bounded operation that failed; remove `.media-cache/kernel` to
re-provision it. A blank OpenGL image usually means the local FreeCAD OpenGL driver
cannot render offscreen; run on the pinned local macOS FreeCAD setup with hardware
acceleration enabled.
