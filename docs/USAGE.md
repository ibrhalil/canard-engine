# Usage

Canard runs two ways: the **menu bar app** (`Canard.app`, everyday use without a terminal) and the **command line** (`uv run canard …`, every option). Run commands from the project folder. Permissions are explained in [PERMISSIONS.md](PERMISSIONS.md).

## Requirements

- macOS 14+ (system audio needs 14.2+). Windows and Linux are planned.
- Python 3.11+ and [uv](https://docs.astral.sh/uv/): `uv sync` once, and again when `pyproject.toml` changes.

## Concepts

- **Source** — the web content to show: a remote URL, a dev server URL or a local build folder with `index.html`. Sources are saved; the last one used is the *active source*.
- **Mode** — `preview` (a normal window, default), `wallpaper` (behind the desktop icons), `screensaver` (screensaver only, no wallpaper).
- **Boot script** — a JS file that runs on every page load, e.g. to pick mscillus' audio source automatically. Saved with the source.

## Menu bar app

`Canard.app` is a small developer launcher for the project's Python environment: no Dock icon, settings are made in the menu and remembered. It needs the repository in place with `uv sync` done; a self-contained package is planned.

```bash
native/macos-app/build.sh --install   # build and install to ~/Applications (also updates Canard.saver)
open ~/Applications/Canard.app
```

| Menu item | What it does |
|---|---|
| Wallpaper `⌃⌥⌘H` | Show / hide. While hidden, pages stay frozen and the screensaver still gets content |
| Pause `⌃⌥⌘P` | Freeze content (CPU, GPU and audio capture stop); the screensaver revives it while running |
| Reload Page `⌃⌥⌘Y` | Reload if content gets stuck |
| Interactive Mode `⌃⌥⌘K` | Wallpaper receives the mouse (desktop icons are covered meanwhile) |
| Screens ▸ | Turn content on/off per screen; remembered by screen name |
| Source ▸ | Switch (`⌃⌥⌘N` next), Add URL…, Add Folder…, Remove Source ▸ |
| Boot Script ▸ | None · mscillus: system audio · mscillus: microphone · Choose File… |
| System Audio | Give system audio to the content (page reloads on change) |
| Permissions ▸ | State of system audio and microphone permissions, shortcuts to System Settings |
| Start Screensaver `⌃⌥⌘L` | Starts the system screensaver; also locks if the password is required immediately |
| Screensaver ▸ | Install / reinstall Canard.saver, settings shortcuts, stream on/off, frame rate 24/30 |
| Open at Login | Adds / removes a LaunchAgent; shows as "Canard" in Login Items |
| Language ▸ | System Default · English · Türkçe (applies immediately) |
| Open Log Folder · About Canard · Quit Canard | About shows versions and the settings, content data and log folders |

Only one Canard can run at a time; a second one exits with "Canard is already running".

**Code signing.** If a code signing certificate named **Canard Dev** is in your keychain, `build.sh` uses it (another name: `CANARD_SIGN_IDENTITY="<name>"`); otherwise it signs ad-hoc and macOS may ask for permissions again after every rebuild. Create one once, no Apple account needed: Keychain Access › Certificate Assistant › Create a Certificate… → Name `Canard Dev`, Identity Type *Self-Signed Root*, Certificate Type *Code Signing*.

## Command line

| Option | Description |
|---|---|
| `--mode preview\|wallpaper\|screensaver` | Run mode (default `preview`) |
| `--tray` | Menu bar app (what Canard.app runs); not combined with run options |
| `--url URL` / `--path FOLDER` | Add a remote/dev server URL or a local build and make it active |
| `--source ID` · `--list` · `--remove ID` | Switch to, list or remove saved sources |
| `--interactive` | Start the wallpaper in interactive mode |
| `--boot-file FILE` · `--clear-boot` | Attach / remove the active source's boot script |
| `--no-system-audio` | Do not give system audio to the content |
| `--no-saver-stream` · `--saver-fps N` | Canard.saver stream off / frame rate (default 30) |
| `--screensaver-after MIN` | Canard's own idle fullscreen (fallback, see below) |
| `--language system\|en\|tr` | Interface language for this run |
| `--settings FILE` | Use another settings file |
| `--log-level LEVEL` · `-v` · `--log-file [PATH]` | Terminal log level, verbose, also log to a file |

```bash
uv run canard --url http://localhost:5173                 # dev server
uv run canard --path ~/dev/mscillus/dist                  # local build
uv run canard --mode wallpaper --interactive
uv run canard --mode wallpaper --boot-file tools/mscillus-boot-system.js   # mscillus + system audio
```

Quit with **Ctrl+C**. The wallpaper appears on every Space, one window per screen. A disconnected screen's page is parked for 10 minutes and resumes where it left off if the screen returns.

## Global shortcuts

All use `⌃ ⌥ ⌘` (the left hand holds the modifiers, letters are on the right half of the keyboard):

| Keys | Action |
|---|---|
| `⌃⌥⌘ ←` / `→` | Send ← / → to the content (mscillus: previous / next mode) |
| `⌃⌥⌘K` · `L` · `P` · `H` · `Y` · `N` | Interactive mode · screensaver · pause · wallpaper · reload · next source |

## Screensaver

**Canard.saver (recommended)** is a real macOS screensaver: it appears as "Canard" in System Settings, and the timer and password stay with macOS. The running Canard keeps drawing the wallpaper page behind the lock screen and streams each screen to it as hardware HEVC.

```bash
native/macos-saver/build.sh --open   # build and open the installer → "Install for this user only"
```

Then select **Canard** in System Settings › Wallpaper › Screen Saver. After reinstalling, run `killall legacyScreenSaver` (the menu's Reinstall… does it for you). If Canard is not running, the screensaver says so and connects once Canard starts.

The stream runs at about 30 fps at full resolution with a fixed ~100 ms delay; it is sharp but not as smooth as macOS' own 60 fps screensavers.

**Idle fullscreen (fallback)** — `--screensaver-after MIN` shows the content fullscreen after the given idle time without Canard.saver. It is *not* a system screensaver and the session is **not locked** while it shows; it steps aside when the session locks, the display sleeps or the system screensaver starts.

## Logs

Lines look like `time level component message`. The menu bar app always logs to `~/Library/Logs/Canard/canard.log` (rotated at 2 MB, 3 backups); the launcher's raw output is in `launcher.log`.

```bash
uv run canard --mode wallpaper -v --log-file
tail -f ~/Library/Logs/Canard/canard.log
log stream --predicate 'subsystem == "dev.canard.saver"' --style compact   # Canard.saver
```

## Media formats

The Chromium in the Qt wheels has no licensed codecs: mp3, ogg, opus, flac, wav and webm play; m4a/aac and mp4/H.264 do not.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Can't click menus in the content | Turn on interactive mode (`⌃⌥⌘K`) |
| mscillus never shows its source screen | A boot script picks the source: `--clear-boot` or Boot Script › None |
| Visualizer doesn't react to music | Allow system audio recording (Permissions ▸), then restart Canard |
| Shortcuts don't work | Another app took the combination; the startup log lists registered shortcuts |
| "Content could not be loaded" | Check the URL / network and press Retry; a build folder needs `index.html` |
| "Port … is busy" warning | A local build's saved port was taken; the content's local data starts empty |
| Screensaver says Canard is not running | Start Canard and keep *Stream to Canard.saver* on |
| Locking the screen hides Canard | macOS shows only the screensaver above the lock screen: use `⌃⌥⌘L` with the password required *immediately* |
| Reset everything | Quit Canard, then `rm -rf ~/Library/Application\ Support/Canard` |

To report a problem, run with `-v --log-file` and share `~/Library/Logs/Canard/canard.log`.

## Diagnostic tools

```bash
uv run python tools/audio-probe/tap_probe.py               # system audio capture test
uv run canard --path tools/mic-test                        # microphone / codec test page
uv run canard --mode wallpaper --path tools/wallpaper-demo # simple wallpaper demo
```
