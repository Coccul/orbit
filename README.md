# Orbit

**macOS menu-bar focus tracker with a floating widget window**

[中文版](README_CN.md) | English

![Platform](https://img.shields.io/badge/Platform-macOS%2012%2B-lightgrey?logo=apple)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- **Menu-bar timer** — switch tasks, get nudge reminders every 10 min
- **Floating widget** — home screen, daily planner, timeline, project tracker, backlog kanban, Gantt chart, heatmap, trends
- **Obsidian integration** (optional) — sync project todos from a Markdown file, check them off directly
- **Internal projects mode** — manage projects and todos without Obsidian
- **Blockytime CSV export** — compatible with Blockytime time-tracking app
- **iPhone widget** — local HTTP server readable by Scriptable

---

## Download

### Option A — Prebuilt .app (recommended)

Download the latest `.app` from [Releases](https://github.com/Coccul/orbit/releases).

Unzip and move `Orbit.app` to your Applications folder, then double-click to launch.

> First launch: macOS may block the app. Go to **System Settings → Privacy & Security** and click "Open Anyway".

### Option B — Run from source

**Requirements:** macOS 12+, Python 3.11+

```bash
git clone https://github.com/Coccul/orbit.git
cd orbit
pip install rumps pyobjc-framework-Cocoa --break-system-packages
python focus_guard.py
```

---

## Configuration

On first launch, a config file is created at:

```
~/Library/Application Support/Orbit/config.json
```

You can also edit settings in the widget's ⚙️ Settings page.

### Key fields

| Field | Type | Description |
|-------|------|-------------|
| `obsidian_enabled` | bool | Enable Obsidian project file sync |
| `obsidian_project_file` | string | Absolute path to your project Markdown file |
| `server_port` | int | HTTP port for iPhone widget (default: 46461) |
| `obsidian_projects` | array | Project list for the new-block dialog and Gantt view |
| `task_colors` | object | Task name → hex color for timeline coloring |
| `category_map` | object | Task name → Blockytime export categories |

### `obsidian_projects` format

Each entry: `[display_label, hex_color, obsidian_heading_key, short_label]`

```json
"obsidian_projects": [
  ["💼 Work", "#5B8FD4", "Work", "Work"],
  ["📚 Study", "#E07068", "Study", "Study"]
]
```

---

## Data storage

All data is stored in `~/Library/Application Support/Orbit/`:

| File | Contents |
|------|----------|
| `config.json` | User configuration |
| `focus.db` | SQLite database of all focus sessions |
| `schedules.json` | Daily time blocks |
| `pending.json` | Backlog items |
| `projects.json` | Internal projects (non-Obsidian mode) |

---

## License

[MIT](LICENSE)
