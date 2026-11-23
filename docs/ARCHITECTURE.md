# Architecture

Canard hosts web content as a desktop wallpaper and screensaver from one Python core. The content is never modified; host needs (autostart, system audio) are met by injected scripts and a bridge.

## Overview

```text
canard/
├── app/        CLI (main), CanardApp, Runtime, WallpaperManager, menu bar, Canard.saver stream, logging
├── content/    source registry (remote / build / dev server), local static server
├── web/        engine interface (base), window.canard bridge
│   ├── chromium/   Qt WebEngine host, permission policy, system audio shim (QWebChannel)
│   └── webkit/     WKWebView host (PyObjC), system audio shim (Web Audio), console bridge
├── audio/      system audio hub: capture on demand, fan out to pages
├── platform/   PlatformAdapter + macos/ (window levels, occlusion, hotkeys, audio tap, HEVC encoder, app integration)
└── i18n/       interface language: English source strings, Turkish catalog
native/
├── macos-app/    Canard.app launcher (Swift)
└── macos-saver/  Canard.saver screensaver (Swift)
tools/          probes and benchmarks used to make decisions (not shipped)
tests/          pytest
```

- **Selectable renderer.** On macOS the page is drawn by **WebKit** (WKWebView, the system engine; default) or **Chromium** (Qt WebEngine); other platforms use Chromium. Only the chosen engine is loaded, and it can be switched while running (pages reload). Everything else — windows, menu, shortcuts, Canard.saver stream, audio hub — talks to `WebEngine` / `WebHost` in `canard/web/base.py`. Why two: on macOS WebKit needs 2–4× less GPU energy per frame for the same content (measurements in PLAN.md); Chromium stays for content that relies on Chromium-only APIs.
- **Platform code only in adapters.** The core talks to `PlatformAdapter`; unsupported features raise `NotImplementedError` and degrade gracefully.
- **Runtime** owns everything that can change while running (source, boot script, wallpaper, interactive mode, system audio, stream); the CLI and the menu bar use the same object.

## Processes (macOS)

```text
Canard.app (Swift launcher, TCC "responsible process")
  └─ .venv/bin/python -m canard.app.main --tray
       ├─ WebKit: com.apple.WebKit.WebContent / .GPU / .Networking   (XPC services)
       │  or Chromium: QtWebEngineProcess   (renderer, GPU, audio service)
       └─ Core Audio tap       (system audio, in the Python process)
legacyScreenSaver → Canard.saver ──TCP 127.0.0.1:47391──► Canard (frame stream)
```

The launcher starts Python as a direct child and lives until it exits, so macOS attributes permissions to Canard.app. Open at Login is a LaunchAgent that runs `open -a Canard.app`.

## How the pieces work

- **Wallpaper.** One frameless Qt window per screen at desktop-icon level −1 (interactive mode: +1, above icons), on all Spaces. When macOS reports a window as occluded the page is suspended; when visible again it resumes and the window is re-ordered to refresh a stale frame. Chromium suspends by hiding the page and freezing it (`Frozen` lifecycle: no JS at all). WebKit has no public freeze: the WKWebView is hidden (`visibilityState` hidden, no animation frames, timers throttled) and its media playback suspended. WebKit's own occlusion detection is turned off, so Canard alone decides when a page stops drawing (as with Chromium).
- **WebKit inside Qt.** The WKWebView is a subview of a plain `NSView` that Qt adopts as a foreign window (`QWindow.fromWinId` + `QWidget.createWindowContainer`), so Qt layouts, margins and window levels apply unchanged. Keyboard focus is handed to the WKWebView whenever the window becomes key. Pages are separate per screen but share one persistent `WKWebsiteDataStore`. Scripts are injected at document start (`WKUserScript`); page ↔ Canard messages go through `WKScriptMessageHandler`, and the console is forwarded to the log by an injected script.
- **Closing pages.** Before pages are destroyed (quit, engine switch) they navigate to an empty page so the content's `pagehide`/`unload` handlers run and can save state; neither engine runs them when a page is simply deleted.
- **Menu bar color.** The macOS menu bar takes its color only from the system desktop picture and recomputes it whenever the window layout changes. While Canard runs, `MenuBarTint` keeps one content frame per screen as the desktop picture, refreshed after a page loads and when interactive mode ends (then Canard's window is re-ordered so the menu bar picks it up); the user's picture is saved in settings.json first and put back on quit, when the wallpaper is hidden, or on the next start after a crash. Only done when the desktop is a still image file, since colors and dynamic wallpapers cannot be restored exactly through the public API; the choice type is read from the system wallpaper store (read-only, undocumented).
- **Screens.** Display changes are debounced (AppKit moves windows itself). A removed screen's page is parked for 10 minutes and restored on the same screen without reloading.
- **Screensaver.** macOS hides app windows on the lock screen, and WebKit animation is throttled inside `legacyScreenSaver`, so Canard.saver cannot render the content itself. Canard keeps drawing the wallpaper page, grabs frames (Chromium: `grabFramebuffer` from the GPU; WebKit: the window's latest frame from the window server via `CGWindowListCreateImage`, drawn straight into the encoder's pixel buffer; `takeSnapshot` re-renders the page and is used only as a fallback), encodes them once per screen with VideoToolbox HEVC on a separate thread (the page keeps its own frame rate) and streams them to every connected Canard.saver, which plays them with a fixed delay. Protocol: `canard/app/saver_stream.py`.
- **System audio.** A Core Audio process tap (macOS 14.2+) captures output, excluding Canard. Pages that call `getDisplayMedia({audio: true})` receive a `MediaStream` built from 20 ms PCM chunks: in Chromium delivered over QWebChannel into a `MediaStreamTrackGenerator`, in WebKit (which has no audio track generator) evaluated into the page and played by an `AudioWorklet` into a `MediaStreamAudioDestinationNode`. Capture runs only while a visible page uses it.
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
- The screensaver stream is 30 fps by default, at most 60, and never smoother than the page itself. Each frame is read back from the GPU and encoded on a separate thread (~14 ms per frame at 3420×2224), so a 60 fps stream costs a CPU core about 70% busy while locked.
- WebKit frames for the stream come from `CGWindowListCreateImage` (deprecated since macOS 14 in favor of ScreenCaptureKit, which needs the Screen Recording permission); Canard only captures its own windows. If the call stops working, Canard falls back to `takeSnapshot` (correct but slow with heavy WebGL content: ~10 fps at 3420×2224) and logs a warning.
- No licensed codecs in the Qt WebEngine wheels (no AAC / H.264) — Chromium engine only; WebKit plays them.
- Content data (localStorage, IndexedDB, cookies) is kept per engine: switching engines starts the content with the other engine's data.
- WebKit chooses its data folder from the process name (`~/Library/WebKit/python/…` while Canard runs from the project's Python); a packaged app will move it.
- WebKit streaming relies on an undocumented WKWebView setting (`_windowOcclusionDetectionEnabled`): with WebKit's own occlusion detection on, the lock screen hides the page and it stays hidden after Canard.saver connects. If the method disappears the call is skipped, a warning is logged, and the stream would freeze while locked.
- System audio permission state is read through an undocumented TCC call (read-only); if it breaks, the menu shows "unknown" and capture still works.
- The menu bar color trick reads the system wallpaper store (`com.apple.wallpaper/Store/Index.plist`, read-only, undocumented) to know whether the desktop is a still picture; if it cannot be read, Canard does not touch the desktop.
- The screensaver stream port has no authentication (see [PERMISSIONS.md](PERMISSIONS.md#network)).

## Tests

- `uv run pytest` — keeps the Turkish catalog in sync with the code; engine selection and the `window.canard` payload.
- Before closing a phase: a manual matrix per OS (on macOS for both engines) — single/multi monitor, Spaces, fullscreen apps, sleep/wake, lock screen, screensaver start/stop, hot-plugging displays, interactive mode, real content (mscillus with system audio).
