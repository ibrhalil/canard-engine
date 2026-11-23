# Canard

Canard runs any web content — a remote site, a local production build or a dev server — as a **live desktop wallpaper** and a **real screensaver**. One Python core (PySide6) with thin per-OS adapters; on macOS the page is drawn by WebKit (the system engine, default) or Chromium (Qt WebEngine), elsewhere by Chromium.

- **Live wallpaper** behind the desktop icons, one page per screen, paused automatically when covered.
- **Real macOS screensaver** (`Canard.saver`) that keeps showing the live page on the lock screen.
- **System audio** for the content: `getDisplayMedia({audio: true})` receives what the computer is playing, so audio visualizers work unchanged.
- **Menu bar app** with global shortcuts; English and Turkish interface.

First sample content: [mscillus](https://github.com/ibrhalil/mscillus), a music visualizer.

## Status

| Platform | State |
|---|---|
| macOS 14+ | Wallpaper, screensaver, system audio (14.2+), menu bar app — working |
| Windows | Planned |
| Linux | Planned (X11 first, Wayland best effort) |

Roadmap and decisions: [PLAN.md](PLAN.md) (Turkish) · Requirements: [docs/PROMPT.md](docs/PROMPT.md) (Turkish)

## Quick start (macOS)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
native/macos-app/build.sh --install && open ~/Applications/Canard.app   # menu bar app
uv run canard --url https://example.com                                 # preview window
uv run canard --mode wallpaper                                          # active source as wallpaper
```

## Documentation

- [Usage](docs/USAGE.md) — menu, command line, shortcuts, screensaver, troubleshooting
- [Permissions & privacy](docs/PERMISSIONS.md) — macOS permissions, what content may access, stored data
- [Architecture](docs/ARCHITECTURE.md) — how it works, platform notes, known limits

## Privacy

Canard collects no data; everything stays on this computer. See [Permissions & privacy](docs/PERMISSIONS.md).

## License

GPL-3.0 — see [LICENSE](LICENSE).
