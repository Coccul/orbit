#!/usr/bin/env python3
"""
Orbit — central configuration module.
All settings are stored in ~/Library/Application Support/Orbit/config.json.
"""

import json
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Orbit"
CONFIG_FILE = APP_SUPPORT / "config.json"

DEFAULTS: dict = {
    "version": 1,
    # Obsidian integration (optional)
    "obsidian_enabled": False,
    "obsidian_project_file": "",   # abs path to 项目进度.md
    "obsidian_log_dir": "",        # abs path to daily work-log folder
    # HTTP server port (for Scriptable iPhone widget)
    "server_port": 46461,
    # Obsidian projects: [display_label, color, obsidian_heading_key, short_label]
    "obsidian_projects": [
        ["💼 工作", "#5B8FD4", "工作", "工作"],
        ["📚 学习", "#E07068", "学习", "学习"],
        ["🎨 创作", "#9880CC", "创作", "创作"],
        ["🌿 修炼", "#68B868", "修炼", "修炼"],
    ],
    # Task category → display color (used by all modules)
    "task_colors": {
        "💼 工作":      "#5B8FD4",
        "📚 学习":      "#E07068",
        "🎨 创作":      "#9880CC",
        "🌿 修炼":      "#68B868",
        "🏃 运动":      "#72B87A",
        "🍽️ 吃饭/家务": "#DDB86A",
        "🧘 冥想":      "#68B868",
        "📔 日记":      "#E8A0BF",
        "😴 睡眠":      "#7090C8",
        "☕ 放松/休息": "#8B5A38",
        "📱 刷手机":    "#7AB0D4",
        "🗂️ 杂事":      "#64748B",
    },
    # Task category → [major, minor] for Blockytime CSV export
    "category_map": {
        "💼 工作":      ["工作", "Work"],
        "📚 学习":      ["工作", "Study"],
        "🎨 创作":      ["工作", "Study"],
        "🌿 修炼":      ["工作", "Study"],
        "🏃 运动":      ["生活", "Sports"],
        "🍽️ 吃饭/家务": ["生活", "杂事"],
        "🧘 冥想":      ["生活", "Meditation"],
        "📔 日记":      ["生活", "Journal"],
        "😴 睡眠":      ["生活", "Sleep"],
        "☕ 放松/休息": ["生活", "Relax"],
        "📱 刷手机":    ["生活", "Mobile"],
        "🗂️ 杂事":      ["生活", "杂事"],
    },
    # Which tasks count as "Study" / "Work" in Obsidian log (empty = all obsidian_projects)
    "study_tasks": [],
    "work_tasks":  [],
}

PROJECTS_FILE = APP_SUPPORT / "projects.json"


def load_internal_projects() -> list:
    """Returns list of internal project dicts (non-Obsidian mode)."""
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_internal_projects(projects: list) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> dict:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {**DEFAULTS, **data}
    except Exception:
        return dict(DEFAULTS)


def save(cfg: dict) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get(key: str, default=None):
    return load().get(key, default)


def set_value(key: str, value) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)


def project_file() -> Path | None:
    """Returns the configured 项目进度.md Path, or None if not set / not enabled."""
    if not get("obsidian_enabled", False):
        return None
    p = get("obsidian_project_file", "")
    return Path(p) if p else None


def obsidian_log_dir() -> Path | None:
    """Returns the work-log directory Path, or None if not configured."""
    d = get("obsidian_log_dir", "")
    return Path(d) if d else None


def load_obsidian_projects() -> list:
    """Returns list of [label, color, obs_key, short] tuples from config."""
    raw = get("obsidian_projects", DEFAULTS["obsidian_projects"])
    return [tuple(p) for p in raw]


def load_task_colors() -> dict:
    """Returns dict mapping task name → hex color."""
    return get("task_colors", DEFAULTS["task_colors"])


def load_category_map() -> dict:
    """Returns dict mapping task name → (major_cat, minor_cat) tuples."""
    raw = get("category_map", DEFAULTS["category_map"])
    return {k: tuple(v) for k, v in raw.items()}
