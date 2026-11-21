# Canard — CANvas boARD engine

Canard hosts web content — remote URL, local build, dev server, or bundled package — as a live desktop wallpaper and screensaver on Windows, macOS, and Linux, from one Python core. It is a generic web content engine, not a wrapper for a single app. First sample content: mscillus (`~/dev/IdeaProjects/mscillus`), a React music visualizer. Read this file fully before working.

## Read map
- `docs/PROMPT.md` — full requirements, verbatim from the owner. The contract for scope, platforms, and phases.
- `PLAN.md` — the single decision ledger and project memory. Keep it current; it is the exchange format with the owner's external tracker (Noma).
- `README.md` — human-facing overview, root only; no README in subdirectories.

## Working rules
- Start with Phase 0 research (PROMPT.md §9): inspect mscillus, verify every technical assumption against current official documentation, and produce the seven outputs (architecture, technology justification, platform compatibility matrix, folder layout, development stages, test strategy, known risks). Feature code comes only after user approval of that report.
- Record every decision in `PLAN.md` with date and origin label: `insan-imalı` (user's own formulation), `insan-onaylı` (user adopted an agent proposal), `agent önerisi` (not yet approved). When a decision changes, state what changed; never rewrite history silently.
- License is GPL-3.0 (owner's direct choice). Audit dependency licenses as they are added; flag anything incompatible with GPL-3.0 distribution in `PLAN.md`.
- Platform order: macOS first (owner's daily machine), then Windows, then Linux (X11 first, Wayland best-effort). If true integration is impossible on a platform, say so openly; do not fake a wallpaper with a fullscreen window.
- Tech baseline to verify in Phase 0, not a final commitment: Python 3.11+, PySide6 + Qt WebEngine host, per-OS platform adapters, per-OS system-audio capture. Question it; alternatives belong in the Phase 0 report.
- Do not rewrite or fork mscillus; it is one content source among many (PROMPT.md §11–12).
- Keep secrets out of the repo; `.env` and IDE folders stay gitignored.

## Conventions
- Conventional commits in English (`feat: add system audio bridge`). Commits are made only by the owner; agents propose the message and never commit.
- Tests: pytest for engine logic; per-OS manual test matrix before closing a phase.
- Reply to the owner in Turkish.
