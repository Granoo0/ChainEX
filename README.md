# ChainEX — Windows Macro Automation Platform

> **DISCLAIMER — READ BEFORE USING**
> This project is provided **for educational purposes only**. It demonstrates
> Windows system programming (Win32 API, GDI), computer vision (OpenCV template
> matching), and finite state machine design in Python. Using automation tools
> against online games or services may violate their Terms of Service and result
> in account suspension or permanent bans. The authors accept no responsibility
> for misuse. Only use this on software where automation is explicitly permitted.

---

## What is ChainEX?

ChainEX is a professional Windows desktop macro automation platform with a
modern dark-themed GUI. It lets you build, edit, and run sequences of automated
actions (clicks, waits, key presses, and template-image searches) against any
target application — all without touching the app's source code.

**Key features:**
- Visual macro editor with drag-and-drop step reordering
- Image template matching via OpenCV (finds UI elements on screen)
- Profile system — save and switch between different macro configurations
- Secondary macro switching (hotkey-triggered mid-run profile swap)
- Sub-loop support — run a nested sequence every N main loops
- Live session dashboard with loop counter, timing stats, and live feed
- Session history with per-run records
- Remote dashboard — control the bot from your phone over Wi-Fi
- Global hotkeys for start / stop / pause without focusing the window
- Scheduled auto-start / auto-stop by time of day

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| GUI | tkinter + ttk (stdlib) |
| Image matching | OpenCV (`cv2`) + NumPy |
| Image I/O | Pillow (`PIL`) |
| Windows API | pywin32 — window capture + background click injection |
| Input listening | pynput — global hotkeys |
| Remote dashboard | `http.server` (stdlib) |
| Config / data | JSON files (stdlib) |

---

## Directory Structure

```
NS-Panel/
├── launcher.py             Main entry point — run this
├── ChainEX.bat             Double-click launcher (auto-installs deps)
├── bot_engine.py           Core state machine loop
├── window_ctrl.py          Win32 API — capture + PostMessage input
├── image_recognizer.py     OpenCV template matching helpers
├── config_loader.py        JSON config loader with built-in defaults
├── bot_logger.py           Rotating file + coloured console logger
├── paths.py                Portable path resolution helpers
├── remote_server.py        LAN HTTP dashboard server
├── config.json             User settings (edit this)
├── requirements.txt        pip dependencies
├── profiles/               Saved macro profiles (.json)
├── templates/              Template images for screen matching (.png)
└── states/
    ├── base_state.py       Abstract base + StateName enum
    └── macro_state.py      Macro execution state machine
```

---

## Setup

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | 64-bit, Windows only |
| Windows | 10 / 11 | Win32 APIs required |

### Option A — Double-click launcher (recommended)

1. Double-click **`ChainEX.bat`**
2. It installs all dependencies automatically and opens ChainEX

### Option B — Manual install

```bash
pip install -r requirements.txt

# If pywin32 doesn't register properly after install:
python Scripts/pywin32_postinstall.py -install

python launcher.py
```

---

## Quick Start

1. **Set the target window** — open Settings → Window Title, type a substring
   of the title bar of the app you want to automate (e.g. `"Chrome"`, `"Notepad"`).
   Use the **BROWSE WINDOWS** button to pick from running windows.

2. **Add steps** — open the **Macro Editor** tab. Click the step-type buttons
   in the toolbar to add:
   - **POS** — click a screen coordinate (x, y)
   - **WAIT** — pause for N seconds
   - **KEY** — send a key press (e.g. `Enter`, `F5`, `ctrl+c`)
   - **Template** — find an image on screen and click it

3. **Run** — press the **START BOT** button or your configured hotkey (default `F6`).
   Press `F5` to stop.

4. **Save as a profile** — open the **Profiles** tab and click **Save Profile**
   so you can reload this sequence later.

---

## Template Matching Steps

Template steps search the game window for a cropped screenshot image.

1. Take a screenshot of your target app (`Win + Shift + S`).
2. Crop tightly around the button or element you want to detect (30–200 px is ideal).
3. Save the PNG in the `templates/` folder.
4. In the Macro Editor, add a template step and type the filename stem
   (e.g. `btn_ok` for `templates/btn_ok.png`).

Use **Test Step** (right-click a template step) to do a dry-run match and
preview the result without starting the full bot.

---

## Configuration

All settings are exposed in the **Settings tab** of the GUI and saved to
`config.json` automatically. The most commonly changed options:

| Setting | What it does |
|---|---|
| Window Title | Substring of the target app's title bar |
| Match Confidence | How closely a screen region must match a template (0.0–1.0) |
| Template Wait Timeout | Seconds to look for a template before skipping the step |
| Max Runtime | Auto-stop after N minutes (0 = run forever) |
| Playback Speed | Multiplier applied to all WAIT durations |
| Hotkeys | Global key combos for start / stop / pause |

Paths in `config.json` are stored as relative paths so the config is portable
across machines — you can copy the whole folder and it just works.

---

## Remote Dashboard

ChainEX hosts a lightweight web page on your local network so you can monitor
and control the bot from a phone or tablet on the same Wi-Fi.

1. Start ChainEX — the Dashboard tab shows the URL (e.g. `http://192.168.1.5:8765`).
2. Open that URL in a phone browser.
3. The page updates every second and has Start / Stop / Pause buttons.

The port can be changed in **Settings → Remote Dashboard → Dashboard Port**.
Changes take effect on the next app launch.

---

## Profiles

Profiles are snapshots of your entire configuration saved as JSON files in the
`profiles/` folder. Use them to switch between completely different macro
sequences without editing `config.json` manually.

**Secondary macro** — configure a profile in Settings → Secondary Macro and
press the Switch hotkey (default `F8`) while the bot is running. The current
loop finishes, then the secondary profile runs. Enable **Run Once then Return**
to run the secondary profile for a single pass and then automatically switch
back to the primary.

---

## Hotkeys (defaults)

| Key | Action |
|---|---|
| `F6` | Start bot |
| `F5` | Stop bot |
| `F1` | Pause / Resume |
| `F8` | Trigger secondary macro switch |

All hotkeys are configurable in **Settings → Hotkeys**.

---

## Performance Notes

- Template matching on a grayscale capture typically takes 2–10 ms per template.
- The engine polls at 50 ms intervals; CPU usage is well under 5% during a run.
- `PrintWindow` captures the target window even when it is minimised or behind
  other windows — no need to keep it visible.
- For hardware-accelerated windows that `PrintWindow` can't capture, swap
  `window_ctrl.capture()` for a DirectX capture library such as `dxcam`.
