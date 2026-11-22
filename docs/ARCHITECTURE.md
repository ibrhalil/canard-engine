# Architecture

Canard hosts web content as a desktop wallpaper and screensaver from one Python core. The content is never modified; host needs (autostart, system audio) are met by injected scripts and a bridge.

## Overview

```text
canard/
├── app/        CLI (main), CanardApp, Runtime, WallpaperManager, menu bar, Canard.saver stream, logging
├── content/    source registry (remote / build / dev server), local static server
├── web/        Qt WebEngine host, permission policy, window.canard bridge, system audio shim (JS)
├── audio/      system audio hub: capture on demand, fan out to pages
├── platform/   PlatformAdapter + macos/ (window levels, occlusion, hotkeys, audio tap, HEVC encoder, app integration)
└── i18n/       interface language: English source strings, Turkish catalog
native/
├── macos-app/    Canard.app launcher (Swift)
└── macos-saver/  Canard.saver screensaver (Swift)
tools/          probes and benchmarks used to make decisions (not shipped)
tests/          pytest
```

- **Single renderer.** Every mode uses the same Qt WebEngine (Chromium) profile; only the window around it changes.
- **Platform code only in adapters.** The core talks to `PlatformAdapter`; unsupported features raise `NotImplementedError` and degrade gracefully.
- **Runtime** owns everything that can change while running (source, boot script, wallpaper, interactive mode, system audio, stream); the CLI and the menu bar use the same object.

## Processes (macOS)

```text
Canard.app (Swift launcher, TCC "responsible process")
  └─ .venv/bin/python -m canard.app.main --tray
       ├─ QtWebEngineProcess   (renderer, GPU, audio service)
       └─ Core Audio tap       (system audio, in the Python process)
legacyScreenSaver → Canard.saver ──TCP 127.0.0.1:47391──► Canard (frame stream)
```

The launcher starts Python as a direct child and lives until it exits, so macOS attributes permissions to Canard.app. Open at Login is a LaunchAgent that runs `open -a Canard.app`.

## How the pieces work

- **Wallpaper.** One frameless window per screen at desktop-icon level −1 (interactive mode: +1, above icons), on all Spaces. When macOS reports a window as occluded the page is hidden and frozen (`Frozen` lifecycle); when visible again it resumes and the window is re-ordered to refresh a stale frame.
- **Screens.** Display changes are debounced (AppKit moves windows itself). A removed screen's page is parked for 10 minutes and restored on the same screen without reloading.
- **Screensaver.** macOS hides app windows on the lock screen, and WebKit animation is throttled inside `legacyScreenSaver`, so Canard.saver cannot render the content itself. Canard keeps drawing the wallpaper page, grabs frames from the GPU (`grabFramebuffer`), encodes them once per screen with VideoToolbox HEVC and streams them to every connected Canard.saver, which plays them with a fixed delay. Protocol: `canard/app/saver_stream.py`.
- **System audio.** A Core Audio process tap (macOS 14.2+) captures output, excluding Canard. Pages that call `getDisplayMedia({audio: true})` receive a `MediaStream` built from PCM chunks delivered over QWebChannel. Capture runs only while a visible page uses it.
- **Local builds.** A small static server on `127.0.0.1` with HTTP range support serves the build from its root; the port is kept per source so the origin (and its localStorage) stays stable.
- **Interface language.** User-facing strings are English in code and wrapped in `t()`; `canard/i18n/tr.py` holds the Turkish catalog and `tests/test_i18n.py` keeps them in sync. Diagnostic logs are English only. Swift components follow the system language.

## Platform matrix

| Feature | macOS | Windows | Linux |
|---|---|---|---|
| Live wallpaper | ✅ desktop window level | Planned (WorkerW) | Planned (X11 desktop window; Wayland depends on the compositor) |
| Screensaver | ✅ Canard.saver + idle fallback | Planned (`.scr`) | Planned |
| System audio | ✅ Core Audio tap (14.2+) | Planned (WASAPI loopback) | Planned (PipeWire) |
| Multi-monitor | ✅ | — | — |
| Packaging | Developer launcher; signed package planned | Planned | Planned |

Where true integration is impossible on a platform, Canard will say so rather than fake a wallpaper with a fullscreen window.

## Known limits

- The desktop window level is public API but not documented for wallpapers; an OS update could change its behavior.
- The screensaver stream is ~30 fps (frame copy is the only path that works while locked).
- No licensed codecs in the Qt WebEngine wheels (no AAC / H.264).
- System audio permission state is read through an undocumented TCC call (read-only); if it breaks, the menu shows "unknown" and capture still works.
- The screensaver stream port has no authentication (see [PERMISSIONS.md](PERMISSIONS.md#network)).

## Tests

- `uv run pytest` — keeps the Turkish catalog in sync with the code.
- Before closing a phase: a manual matrix per OS — single/multi monitor, Spaces, fullscreen apps, sleep/wake, lock screen, screensaver start/stop, hot-plugging displays, real content (mscillus with system audio).
