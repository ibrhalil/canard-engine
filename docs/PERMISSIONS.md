# Permissions & privacy

What Canard asks macOS for, what the content it shows may access, and what is stored where. Only macOS is supported today.

## Privacy

Canard collects no data: no account, telemetry, analytics or ads, and it never connects to the internet on its own. It only loads the sources you add. Settings and content data stay on this computer, and the screensaver stream never leaves it.

The web content you add runs under its own rules. **Add only sources you trust** — see [What content may access](#what-content-may-access).

## Who gets the permission

- **Canard.app (recommended):** macOS asks on behalf of "Canard" and remembers the decision for Canard. Open it from Finder, Spotlight or `open`.
- **Command line (`uv run canard`):** permissions belong to the terminal app that started it (Terminal, iTerm, …). Restart the terminal after changing one.

An ad-hoc signed Canard.app may be asked again after each rebuild; signing with a stable certificate keeps the decisions (see [USAGE.md](USAGE.md#menu-bar-app)).

## macOS permissions

| Permission | When | Where to change |
|---|---|---|
| System audio recording | Content asks for system audio (macOS 14.2+, Core Audio tap) | Privacy & Security › Screen & System Audio Recording › *System Audio Recording Only* |
| Microphone | Content asks for the microphone | Privacy & Security › Microphone |
| Screen recording | Only if content asks to share the screen as video | Privacy & Security › Screen & System Audio Recording |
| Camera | Only if content asks for the camera | Privacy & Security › Camera |
| Accessibility | **Not needed** — shortcuts use Carbon hotkeys; the keyboard is not monitored | — |

If a permission was denied, Canard shows a one-time dialog with **Open Settings**, the menu's status line shows "⚠︎ permission needed", and **Settings ▸ Permissions ▸** lists each state. Restart Canard after changing a permission.

Audio capture runs only while a visible page uses it; it stops when the content stops, the wallpaper is covered or Canard quits. macOS may show a recording indicator meanwhile.

Reset decisions:

```bash
tccutil reset All dev.canard.app
```

## What content may access

To start on its own as a wallpaper, content gets permissive defaults, the same with both engines (`canard/web/chromium/policy.py`, `canard/web/webkit/engine.py`):

| Web permission | Decision |
|---|---|
| Microphone, camera | Allowed |
| Screen sharing (`getDisplayMedia`) | Chromium: allowed, first screen picked automatically. WebKit: macOS shows its screen sharing picker |
| System audio (`getDisplayMedia({audio: true})`) | Canard's captured system audio (off with `--no-system-audio` or the menu) |
| Notifications, location, clipboard, others | Denied |
| Autoplay | Allowed without a user gesture |

> ⚠️ A source you add can listen to the microphone and system audio without asking. Per-source permissions are planned.

System audio reaches the page through the engine's in-process bridge (Chromium: QWebChannel; WebKit: script messages): no network port, no other app or browser tab can read it.

With WebKit, Canard's pages can be inspected with Safari's Web Inspector on this Mac (Develop menu); nothing is exposed over the network.

## Network

- **Remote sources:** only the URL you added.
- **Local builds:** served on `127.0.0.1` only, requests outside the folder are refused. The port is saved per source so the content's local data (bound to its address) survives restarts.
- **Screensaver stream:** `127.0.0.1:47391`, local only. The sandboxed screensaver cannot read a key, so there is no authentication: another local process could read the wallpaper frames (only Canard's page, not the rest of the screen). Turn off *Stream to Canard.saver* to send no frames.

## Stored data

| What | Where |
|---|---|
| Sources, active source, boot scripts, menu choices | `~/Library/Application Support/Canard/Canard/settings.json` |
| Content data, WebKit (localStorage, IndexedDB, cookies, cache) | `~/Library/WebKit/python/WebsiteDataStore/c4a7d0e2-5b13-4f6a-9e28-7d1b3c6f0a54/` |
| Content data, Chromium | `~/Library/Application Support/Canard/Canard/webprofile/` |
| Logs (may contain page addresses and console messages) | `~/Library/Logs/Canard/` |

All sources share one web profile per engine; data is separated per site address (origin), and the two engines never share data. Removing a source does not delete its data. The About window links to the current engine's folder. The WebKit folder is named after the process (`python` while Canard runs from the project's environment) and managed by macOS' WebKit. To delete everything, quit Canard and run `rm -rf ~/Library/Application\ Support/Canard ~/Library/WebKit/python/WebsiteDataStore/c4a7d0e2-5b13-4f6a-9e28-7d1b3c6f0a54`.
