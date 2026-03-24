#!/usr/bin/env python3
"""
Orbit 周天
- 菜单栏显示当前任务 + 计时（每30秒刷新）
- 切换任务时弹窗拦截
- 自定义任务（可新增/删除）
- 每10分钟提醒「你还在做XX吗？」
- 写 state.json 供浮动 widget 实时读取
- 自动记录到 SQLite，导出 Blockytime 兼容 CSV
"""

import sys

# py2app bundled Python 缺少部分 codec，导致 subprocess 失败
# 预先 import codec 模块并 patch locale，两者都做确保兼容
if getattr(sys, 'frozen', False):
    import encodings.ascii
    import encodings.utf_8
    import encodings.latin_1
    import locale
    locale.getpreferredencoding = lambda *a, **kw: 'UTF-8'

import rumps
import sqlite3
import csv
import json
import re
import subprocess
import socket
import threading
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, date, timedelta
from pathlib import Path

_HERE = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
         else Path(__file__).parent)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import uuid
import orbit_config


def _helper_cmd(name):
    """返回启动辅助脚本的命令列表，兼容开发模式和 .app 打包模式。"""
    if getattr(sys, 'frozen', False):
        # .app 打包模式：extra_scripts 编译成同目录的独立可执行文件
        exe = Path(sys.executable).parent / name
        return [str(exe)]
    else:
        # 开发模式：用当前 Python 解释器运行 .py 文件
        script = Path(__file__).parent / f"{name}.py"
        return [sys.executable, str(script)]


OBSIDIAN_LOG_DIR = orbit_config.obsidian_log_dir()

_obs_projs  = orbit_config.load_obsidian_projects()
_cfg_study  = orbit_config.get("study_tasks", [])
_cfg_work   = orbit_config.get("work_tasks",  [])
# Fall back: if not configured, all obsidian_projects count as Study tasks
STUDY_TASKS = set(_cfg_study) if _cfg_study else {p[0] for p in _obs_projs}
WORK_TASKS  = set(_cfg_work)

HTTP_PORT = 5678

def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _stable_url() -> str:
    """优先用 IP 地址，确保手机等设备能正常连接。mDNS作为fallback。"""
    ip = _local_ip()
    if ip and ip != "127.0.0.1":
        return f"http://{ip}:{HTTP_PORT}"

    # 如果IP获取失败，fallback到mDNS
    try:
        r = subprocess.run(
            ['scutil', '--get', 'LocalHostName'],
            capture_output=True, text=True, timeout=2,
        )
        name = r.stdout.strip()
        if name:
            return f"http://{name}.local:{HTTP_PORT}"
    except Exception:
        pass
    return f"http://127.0.0.1:{HTTP_PORT}"

# ── 路径 ──────────────────────────────────────────────────────────────
APP_SUPPORT      = Path.home() / "Library" / "Application Support" / "Orbit"
ICLOUD           = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Orbit"
SCRIPTABLE_DIR     = Path.home() / "Library" / "Mobile Documents" / "iCloud~dk~simonbs~Scriptable" / "Documents"
DB_PATH            = APP_SUPPORT / "focus.db"
STATE_FILE         = APP_SUPPORT / "state.json"
SCRIPTABLE_STATE   = SCRIPTABLE_DIR / "orbit_state.json"    # iOS widget 读
SCRIPTABLE_TIMELINE= SCRIPTABLE_DIR / "orbit_timeline.json" # iOS 时间块查看
COMMAND_FILE       = APP_SUPPORT / "command.json"                # 桌面 widget 写命令
SCRIPTABLE_COMMAND = SCRIPTABLE_DIR / "orbit_command.json"  # iOS Scriptable 写命令
TASKS_FILE         = APP_SUPPORT / "custom_tasks.json"
CHECKIN_FILE       = APP_SUPPORT / "checkin.json"
EXPORT_DIR         = Path.home() / "Desktop"

# ── 内置任务 ──────────────────────────────────────────────────────────
DEFAULT_WORK_TASKS = [p[0] for p in _obs_projs]

DEFAULT_LIFE_TASKS = [
    "😴 睡眠",
    "📱 刷手机",
    "☕ 放松/休息",
    "🏃 运动",
    "🍽️ 吃饭/家务",
    "🧘 冥想",
    "📔 日记",
    "🗂️ 杂事",
]

DEFAULT_CHECKIN_ITEMS = [
    "🏃 运动",
    "📖 阅读",
    "🧘 冥想",
    "📔 日记",
]

# CSV 导出：每个任务对应的 Blockytime 大类/小类
CATEGORY_MAP = orbit_config.load_category_map()

CSV_HEADERS = ["开始时间", "持续时间", "事件大类", "事件类别", "内容类型", "事件内容", "备注", "Tag", "状态"]

TASK_COLORS = orbit_config.load_task_colors()

REMINDER_INTERVAL = 10 * 60   # 每 10 分钟提醒一次（秒）


# ── 工具 ─────────────────────────────────────────────────────────────
def fmt(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _timeline_stats(rows):
    """Compute {category: minutes} using 15-min cell 'most overlap wins' logic.

    Same algorithm as orbit_timeline._task_at() — prevents inflated totals when
    overlapping DB entries exist (e.g. auto-tracked vs manually edited).
    rows: [(start_time_str, end_time_str, duration, category), ...]
    """
    if not rows:
        return {}
    parsed = []
    for start_str, end_str, _dur, cat in rows:
        try:
            s = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
            e = datetime.strptime(end_str,   "%Y-%m-%d %H:%M")
            if e > s:
                parsed.append((s, e, cat))
        except Exception:
            pass
    if not parsed:
        return {}
    scan_start = min(s for s, e, c in parsed).replace(hour=0, minute=0, second=0, microsecond=0)
    scan_end   = max(e for s, e, c in parsed)
    cat_mins   = {}
    t = scan_start
    while t < scan_end:
        t_end = t + timedelta(minutes=15)
        best_cat, best_ov = None, timedelta(0)
        for s_dt, e_dt, cat in parsed:
            ov = min(e_dt, t_end) - max(s_dt, t)
            if ov > timedelta(0) and ov >= best_ov:
                best_ov, best_cat = ov, cat
        if best_cat:
            cat_mins[best_cat] = cat_mins.get(best_cat, 0) + 15
        t = t_end
    return cat_mins


# ── 主应用 ────────────────────────────────────────────────────────────
class Orbit(rumps.App):

    def __init__(self):
        super().__init__("⚡ 未开始", quit_button=None)
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        ICLOUD.mkdir(parents=True, exist_ok=True)
        self._init_db()

        self.current_task = None
        self.task_start   = None
        self._last_reminder = 0
        self._last_ip = _local_ip()
        self._server_url  = _stable_url()   # 稳定地址（mDNS 优先）

        # ── 恢复上次会话状态（FG 重启后不丢失进行中的任务） ────────────
        try:
            if STATE_FILE.exists():
                prev = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                ct = prev.get("current_task")
                ts = prev.get("task_start")
                if ct and ts:
                    self.current_task = ct
                    self.task_start   = datetime.fromisoformat(ts)
        except Exception:
            pass

        self._http_queue = queue.Queue()
        self._auto_log_done: set = set()
        self._morning_plan_done: set = set()
        self._start_http_server()

        self._load_custom_tasks()
        self._load_checkin()
        self._build_menu()
        self._write_state()
        self._write_timeline()
        self._launch_widget()

    # ── Widget 子进程 ────────────────────────────────────────────────
    def _launch_widget(self):
        import os
        # 先杀掉已有的 widget 进程，避免两个 Tk/AppKit 实例冲突 → SIGABRT
        subprocess.run(
            ["pkill", "-f", "orbit_widget"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        import time as _time; _time.sleep(0.4)   # 等进程彻底退出

        # 优先用 Homebrew python3（tkinter 兼容新 macOS），
        # 避免 CommandLineTools Python 3.9 在 macOS 26 上 SIGABRT
        HOMEBREW_PY = "/opt/homebrew/bin/python3"
        base = Path(__file__).parent
        # bundle 里无 .py 后缀，开发模式有 .py 后缀，两种都找
        script = base / "orbit_widget.py"
        if not script.exists():
            script = base / "orbit_widget"
        if Path(HOMEBREW_PY).exists() and script.exists():
            cmd = [HOMEBREW_PY, str(script)]
        else:
            cmd = _helper_cmd("orbit_widget")

        # 清除 py2app 注入的 PYTHONPATH，避免子进程加载 bundle 内模块
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        except Exception as e:
            with open(str(APP_SUPPORT / "widget_launch.log"), "w") as f:
                f.write(f"Popen failed: {e}\ncmd: {cmd}\n")

    # ── 任务列表 ─────────────────────────────────────────────────────
    def _load_custom_tasks(self):
        if TASKS_FILE.exists():
            data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            self.work_tasks = data.get("work", list(DEFAULT_WORK_TASKS))
            self.life_tasks = data.get("life", list(DEFAULT_LIFE_TASKS))
            # 迁移旧任务名
            changed = False
            # 🔧 系统/杂事 → 🗂️ 系统/杂事（旧版）
            if "🔧 系统/杂事" in data.get("work", []):
                self.work_tasks = [("🗂️ 系统/杂事" if t == "🔧 系统/杂事" else t)
                                   for t in data["work"]]
                if "💻 折腾/开发" not in self.work_tasks:
                    self.work_tasks.insert(-1, "💻 折腾/开发")
                changed = True
            # 🗂️ 系统/杂事 → 🗂️ 杂事，从 work 移到 life
            if "🗂️ 系统/杂事" in self.work_tasks:
                self.work_tasks = [t for t in self.work_tasks if t != "🗂️ 系统/杂事"]
                if "🗂️ 杂事" not in self.life_tasks:
                    self.life_tasks.append("🗂️ 杂事")
                changed = True
            if changed:
                self._save_custom_tasks()
        else:
            self.work_tasks = list(DEFAULT_WORK_TASKS)
            self.life_tasks = list(DEFAULT_LIFE_TASKS)

    def _save_custom_tasks(self):
        TASKS_FILE.write_text(json.dumps(
            {"work": self.work_tasks, "life": self.life_tasks},
            ensure_ascii=False, indent=2
        ), encoding="utf-8")

    # ── 菜单 ─────────────────────────────────────────────────────────
    def _build_menu(self):
        self.menu.clear()

        # 工作类任务
        for t in self.work_tasks:
            self.menu.add(rumps.MenuItem(t, callback=self._on_select))

        self.menu.add(None)

        # 生活类任务
        for t in self.life_tasks:
            self.menu.add(rumps.MenuItem(t, callback=self._on_select))

        self.menu.add(None)

        # 打卡区
        header = rumps.MenuItem("── 今日打卡 ──")
        self.menu.add(header)
        for item in self.checkin_items:
            mi = rumps.MenuItem(item, callback=self._on_checkin_toggle)
            mi.state = 1 if item in self.checkin_done else 0
            self.menu.add(mi)

        self.menu.add(None)

        # 自定义管理
        add_menu = rumps.MenuItem("＋ 新增任务")
        add_menu.add(rumps.MenuItem("新增工作任务", callback=self._add_work_task))
        add_menu.add(rumps.MenuItem("新增生活任务", callback=self._add_life_task))
        add_menu.add(rumps.MenuItem("新增打卡项目", callback=self._add_checkin_item))
        self.menu.add(add_menu)

        del_menu = rumps.MenuItem("－ 删除任务")
        del_menu.add(rumps.MenuItem("删除工作任务", callback=self._del_work_task))
        del_menu.add(rumps.MenuItem("删除生活任务", callback=self._del_life_task))
        del_menu.add(rumps.MenuItem("删除打卡项目", callback=self._del_checkin_item))
        self.menu.add(del_menu)

        self.menu.add(None)
        self.menu.add(rumps.MenuItem("⏹  结束当前任务",    callback=self._on_finish))
        self.menu.add(rumps.MenuItem("🌅  安排明天上午",    callback=lambda _: self._show_morning_planner()))

        self.menu.add(None)
        self.menu.add(rumps.MenuItem("🪟  重新打开 Widget",  callback=lambda _: self._launch_widget()))
        self.menu.add(rumps.MenuItem("📋  积压任务",        callback=self._on_backlog))
        self.menu.add(rumps.MenuItem("🕐  时间块视图",      callback=self._on_timeline))
        self.menu.add(rumps.MenuItem("📈  项目甘特图",      callback=self._on_gantt))
        self.menu.add(rumps.MenuItem("📉  时间趋势",        callback=self._on_trend))
        self.menu.add(rumps.MenuItem("🗓️  周热力图",        callback=self._on_heatmap))
        self.menu.add(rumps.MenuItem("📊  今日记录",        callback=self._on_today))
        self.menu.add(rumps.MenuItem("📅  本周记录",        callback=self._on_week))
        self.menu.add(rumps.MenuItem("💾  导出 CSV",        callback=self._on_export))
        self.menu.add(rumps.MenuItem("📝  现在更新日志",    callback=lambda _: self._update_obsidian_log()))

        self.menu.add(None)
        self.menu.add(rumps.MenuItem(f"📡  {self._server_url}", callback=self._on_copy_url))
        self.menu.add(rumps.MenuItem("退出",               callback=self._on_quit))

    # ── 自定义任务 ────────────────────────────────────────────────────
    def _add_work_task(self, _):
        w = rumps.Window(
            title="新增工作任务",
            message="输入任务名（建议加 emoji，如 🎨 视频剪辑）：",
            default_text="",
            ok="添加", cancel="取消",
            dimensions=(300, 30),
        )
        r = w.run()
        if r.clicked and r.text.strip():
            task = r.text.strip()
            if task not in self.work_tasks:
                self.work_tasks.append(task)
                self._save_custom_tasks()
                self._build_menu()
                self._write_state()

    def _add_life_task(self, _):
        w = rumps.Window(
            title="新增生活任务",
            message="输入任务名（如 🛁 洗澡）：",
            default_text="",
            ok="添加", cancel="取消",
            dimensions=(300, 30),
        )
        r = w.run()
        if r.clicked and r.text.strip():
            task = r.text.strip()
            if task not in self.life_tasks:
                self.life_tasks.append(task)
                self._save_custom_tasks()
                self._build_menu()
                self._write_state()

    def _del_work_task(self, _):
        if not self.work_tasks:
            return
        items = "\n".join(f"{i+1}. {t}" for i, t in enumerate(self.work_tasks))
        w = rumps.Window(
            title="删除工作任务",
            message=f"输入要删除的序号：\n\n{items}",
            default_text="",
            ok="删除", cancel="取消",
            dimensions=(200, 30),
        )
        r = w.run()
        if r.clicked and r.text.strip().isdigit():
            idx = int(r.text.strip()) - 1
            if 0 <= idx < len(self.work_tasks):
                self.work_tasks.pop(idx)
                self._save_custom_tasks()
                self._build_menu()
                self._write_state()

    def _del_life_task(self, _):
        if not self.life_tasks:
            return
        items = "\n".join(f"{i+1}. {t}" for i, t in enumerate(self.life_tasks))
        w = rumps.Window(
            title="删除生活任务",
            message=f"输入要删除的序号：\n\n{items}",
            default_text="",
            ok="删除", cancel="取消",
            dimensions=(200, 30),
        )
        r = w.run()
        if r.clicked and r.text.strip().isdigit():
            idx = int(r.text.strip()) - 1
            if 0 <= idx < len(self.life_tasks):
                self.life_tasks.pop(idx)
                self._save_custom_tasks()
                self._build_menu()
                self._write_state()

    # ── 打卡 ─────────────────────────────────────────────────────────
    def _load_checkin(self):
        today = date.today().isoformat()
        if CHECKIN_FILE.exists():
            data = json.loads(CHECKIN_FILE.read_text(encoding="utf-8"))
            self.checkin_items = data.get("items", list(DEFAULT_CHECKIN_ITEMS))
            if data.get("date") == today:
                self.checkin_done = set(data.get("done", []))
            else:
                self.checkin_done = set()
        else:
            self.checkin_items = list(DEFAULT_CHECKIN_ITEMS)
            self.checkin_done = set()
        self._checkin_date = today

    def _save_checkin(self):
        CHECKIN_FILE.write_text(json.dumps({
            "items": self.checkin_items,
            "date":  date.today().isoformat(),
            "done":  list(self.checkin_done),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _on_checkin_toggle(self, sender):
        item = sender.title
        if item in self.checkin_done:
            self.checkin_done.discard(item)
            sender.state = 0
        else:
            self.checkin_done.add(item)
            sender.state = 1
        self._save_checkin()

    def _add_checkin_item(self, _):
        w = rumps.Window(
            title="新增打卡项目",
            message="输入打卡项目名（如 🥗 健康饮食）：",
            default_text="",
            ok="添加", cancel="取消",
            dimensions=(300, 30),
        )
        r = w.run()
        if r.clicked and r.text.strip():
            item = r.text.strip()
            if item not in self.checkin_items:
                self.checkin_items.append(item)
                self._save_checkin()
                self._build_menu()

    def _del_checkin_item(self, _):
        if not self.checkin_items:
            return
        items = "\n".join(f"{i+1}. {t}" for i, t in enumerate(self.checkin_items))
        w = rumps.Window(
            title="删除打卡项目",
            message=f"输入要删除的序号：\n\n{items}",
            default_text="",
            ok="删除", cancel="取消",
            dimensions=(200, 30),
        )
        r = w.run()
        if r.clicked and r.text.strip().isdigit():
            idx = int(r.text.strip()) - 1
            if 0 <= idx < len(self.checkin_items):
                removed = self.checkin_items.pop(idx)
                self.checkin_done.discard(removed)
                self._save_checkin()
                self._build_menu()

    # ── 数据库 ────────────────────────────────────────────────────────
    def _init_db(self):
        with sqlite3.connect(DB_PATH) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT    NOT NULL,
                    end_time   TEXT    NOT NULL,
                    duration   INTEGER NOT NULL,
                    category   TEXT    NOT NULL,
                    note       TEXT    DEFAULT '',
                    status     TEXT    DEFAULT '已完成'
                )
            """)

    # ── 状态 ─────────────────────────────────────────────────────────
    def _write_state(self):
        """把当前任务 + 任务列表写到 state.json，供桌面 widget / iOS Scriptable widget 读取"""
        state = {
            "current_task":  self.current_task,
            "task_start":    self.task_start.isoformat() if self.task_start else None,
            "work_tasks":    self.work_tasks,
            "life_tasks":    self.life_tasks,
            "server_url":    self._server_url,
            "checkin_items": self.checkin_items,
            "checkin_done":  list(self.checkin_done),
            "checkin_date":  self._checkin_date,
        }
        payload = json.dumps(state, ensure_ascii=False)
        STATE_FILE.write_text(payload, encoding="utf-8")
        # 同步写一份给 iOS Scriptable widget
        try:
            if SCRIPTABLE_DIR.exists():
                SCRIPTABLE_STATE.write_text(payload, encoding="utf-8")
        except Exception:
            pass
        self._write_timeline()

    def _write_timeline(self):
        """把今日时间块写到 iCloud，供 iOS 离线查看"""
        try:
            if not SCRIPTABLE_DIR.exists():
                return
            today     = date.today()
            today_str = today.strftime("%Y-%m-%d")
            d_start   = today_str + " 00:00"
            d_end     = (today + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00"
            with sqlite3.connect(DB_PATH) as c:
                rows = c.execute(
                    "SELECT start_time, end_time, duration, category, COALESCE(note, '') as note FROM entries "
                    "WHERE end_time > ? AND start_time < ? ORDER BY start_time",
                    (d_start, d_end)
                ).fetchall()
            entries = [
                {
                    "start":    max(s, d_start)[11:16],
                    "end":      min(e, d_end)[11:16],
                    "duration": dur,
                    "category": cat,
                    "color":    TASK_COLORS.get(cat, '#94A3B8'),
                    "note":     note,
                }
                for s, e, dur, cat, note in rows
            ]
            current = None
            if self.current_task and self.task_start:
                current = {
                    "task":    self.current_task,
                    "start":   self.task_start.strftime("%H:%M"),
                    "elapsed": int(self.elapsed),
                    "color":   TASK_COLORS.get(self.current_task, '#94A3B8'),
                }
            SCRIPTABLE_TIMELINE.write_text(
                json.dumps({
                    "date":    today,
                    "entries": entries,
                    "current": current,
                    "updated": datetime.now().strftime("%H:%M"),
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    @property
    def elapsed(self) -> float:
        if self.task_start:
            return (datetime.now() - self.task_start).total_seconds()
        return 0.0

    def _save_current(self):
        if not self.current_task or not self.task_start:
            return
        dur = int(self.elapsed // 60)
        if dur < 1:
            return
        end = datetime.now()
        with sqlite3.connect(DB_PATH) as c:
            c.execute(
                "INSERT INTO entries (start_time, end_time, duration, category) VALUES (?,?,?,?)",
                (
                    self.task_start.strftime("%Y-%m-%d %H:%M"),
                    end.strftime("%Y-%m-%d %H:%M"),
                    dur,
                    self.current_task,
                ),
            )

    def _get_entries_by_date(self, date_str: str):
        """获取指定日期的所有entries数据"""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row  # 使结果可以按列名访问
                cursor = conn.execute("""
                    SELECT id, start_time, end_time, duration, category,
                           COALESCE(note, '') as note,
                           COALESCE(status, '已完成') as status
                    FROM entries
                    WHERE start_time LIKE ?
                    ORDER BY start_time
                """, (f"{date_str}%",))

                entries = []
                for row in cursor.fetchall():
                    entries.append({
                        "id": row["id"],
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "duration": row["duration"],
                        "category": row["category"],
                        "note": row["note"],
                        "status": row["status"]
                    })
                return entries
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return []

    def _start(self, task: str):
        self.current_task = task
        self.task_start   = datetime.now()
        self._last_reminder = datetime.now().timestamp()
        self.title        = task
        self._write_state()

    def _clear(self, new_title="⚡ 未开始"):
        self.current_task = None
        self.task_start   = None
        self.title        = new_title
        self._write_state()

    # ── 任务回调 ──────────────────────────────────────────────────────
    def _on_select(self, sender):
        new = sender.title
        if self.current_task == new:
            return

        if self.current_task:
            resp = rumps.alert(
                title="切换任务",
                message=(
                    f"当前：{self.current_task}（{fmt(self.elapsed)}）\n\n"
                    f"切换到「{new}」？"
                ),
                ok="切换",
                cancel="取消",
            )
            if resp == 0:
                return
            self._save_current()

        self._start(new)

    def _on_pause(self, _):
        if not self.current_task:
            return
        self._save_current()
        self._clear("⏸ 暂停中")

    def _on_finish(self, _):
        if not self.current_task:
            rumps.alert(title="没有进行中的任务", message="先从菜单选一个任务开始", ok="好")
            return
        if self.current_task in self.work_tasks and self.elapsed < 60 * 60:
            resp = rumps.alert(
                title="⚠️  还没满 1 小时",
                message=(
                    f"你正在做：{self.current_task}\n"
                    f"已专注：{fmt(self.elapsed)}\n\n"
                    f"真的要结束吗？"
                ),
                ok="结束",
                cancel="继续专注",
            )
            if resp == 0:
                return
        elapsed_str = fmt(self.elapsed)
        task = self.current_task
        self._save_current()
        self._clear()
        rumps.notification(
            title="✅  任务完成",
            subtitle=task,
            message=f"专注了 {elapsed_str}",
        )

    # ── HTTP Server（本地网络，Scriptable 直连，无 iCloud 延迟） ─────
    def _start_http_server(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): pass   # 静默日志

            def _send_json(self, data, status=200):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/state":
                    self._send_json({
                        "current_task":  app.current_task,
                        "task_start":    app.task_start.isoformat() if app.task_start else None,
                        "work_tasks":    app.work_tasks,
                        "life_tasks":    app.life_tasks,
                        "checkin_items": app.checkin_items,
                        "checkin_done":  list(app.checkin_done),
                        "checkin_date":  app._checkin_date,
                        "server_url":    app._server_url,
                    })
                elif self.path == "/entries" or self.path == "/entries/today":
                    # 获取今天的entries数据
                    today = date.today().strftime("%Y-%m-%d")
                    entries = app._get_entries_by_date(today)
                    self._send_json({
                        "date": today,
                        "entries": entries,
                        "total_duration": sum(e.get("duration", 0) for e in entries)
                    })
                elif self.path.startswith("/entries/"):
                    # 获取指定日期的entries数据
                    target_date = self.path.split("/")[-1]
                    try:
                        # 验证日期格式
                        datetime.strptime(target_date, "%Y-%m-%d")
                        entries = app._get_entries_by_date(target_date)
                        self._send_json({
                            "date": target_date,
                            "entries": entries,
                            "total_duration": sum(e.get("duration", 0) for e in entries)
                        })
                    except ValueError:
                        self._send_json({"error": "Invalid date format. Use YYYY-MM-DD"}, 400)
                elif self.path == "/icon.png":
                    icon = Path(__file__).parent / "icon.png"
                    try:
                        body = icon.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception:
                        self.send_response(404); self.end_headers()
                elif self.path == "/" or self.path == "/index.html":
                    pwa = Path(__file__).parent / "pwa.html"
                    try:
                        body = pwa.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception:
                        self.send_response(404); self.end_headers()
                elif self.path == "/backlog":
                    try:
                        pending = APP_SUPPORT / "pending.json"
                        data = json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else {}
                        items = data.get("items", data) if isinstance(data, dict) else data
                        self._send_json({"items": items})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                elif self.path.startswith("/backlog/"):
                    # GET /backlog/{id} not needed, handled above
                    self.send_response(404); self.end_headers()
                elif self.path.startswith("/planner"):
                    parts = [p for p in self.path.split("/") if p]
                    target = parts[1] if len(parts) > 1 else date.today().strftime("%Y-%m-%d")
                    try:
                        sched = APP_SUPPORT / "schedules.json"
                        all_data = json.loads(sched.read_text(encoding="utf-8")) if sched.exists() else {}
                        day = all_data.get(target, {})
                        self._send_json({"date": target, "blocks": day.get("blocks", [])})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                else:
                    self.send_response(404); self.end_headers()

            def do_DELETE(self):
                if self.path.startswith("/backlog/"):
                    item_id = self.path.split("/")[-1]
                    try:
                        pending = APP_SUPPORT / "pending.json"
                        data = json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else {}
                        items = data.get("items", []) if isinstance(data, dict) else data
                        items = [i for i in items if str(i.get("id","")) != item_id]
                        if isinstance(data, dict):
                            data["items"] = items
                        else:
                            data = {"items": items}
                        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        self._send_json({"ok": True})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                elif self.path.startswith("/entries/"):
                    entry_id = self.path.split("/")[-1]
                    try:
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
                        self._send_json({"ok": True})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                elif self.path.startswith("/planner/") and "/block/" in self.path:
                    parts = [p for p in self.path.split("/") if p]
                    target, block_id = parts[1], parts[3]
                    try:
                        sched = APP_SUPPORT / "schedules.json"
                        all_data = json.loads(sched.read_text(encoding="utf-8")) if sched.exists() else {}
                        day = all_data.setdefault(target, {})
                        day["blocks"] = [b for b in day.get("blocks", []) if b.get("id") != block_id]
                        sched.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        self._send_json({"ok": True})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                else:
                    self.send_response(404); self.end_headers()

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_POST(self):
                if self.path == "/entries":
                    # POST /entries — add manual entry
                    n = int(self.headers.get("Content-Length", 0))
                    try:
                        data = json.loads(self.rfile.read(n))
                        st  = data.get("start_time")
                        et  = data.get("end_time")
                        cat = data.get("category", "")
                        note = data.get("note", "补录")
                        dur = data.get("duration")
                        if dur is None:
                            from datetime import datetime as _dt
                            dur = max(1, int((_dt.fromisoformat(et) - _dt.fromisoformat(st)).total_seconds() / 60))
                        with sqlite3.connect(DB_PATH) as conn:
                            cur = conn.execute(
                                "INSERT INTO entries (start_time, end_time, duration, category, note) VALUES (?,?,?,?,?)",
                                (st, et, dur, cat, note))
                            self._send_json({"ok": True, "id": cur.lastrowid})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                elif self.path.startswith("/planner/") and self.path.endswith("/blocks"):
                    # POST /planner/YYYY-MM-DD/blocks — create block
                    parts = [p for p in self.path.split("/") if p]
                    target = parts[1]
                    n = int(self.headers.get("Content-Length", 0))
                    try:
                        data = json.loads(self.rfile.read(n))
                        sched = APP_SUPPORT / "schedules.json"
                        all_data = json.loads(sched.read_text(encoding="utf-8")) if sched.exists() else {}
                        blocks = all_data.setdefault(target, {}).setdefault("blocks", [])
                        block = {
                            "id":        uuid.uuid4().hex[:8],
                            "text":      data.get("text", ""),
                            "color":     data.get("color", "#94A3B8"),
                            "start_min": int(data.get("start_min", 480)),
                            "end_min":   int(data.get("end_min", 540)),
                            "done":      False,
                        }
                        blocks.append(block)
                        sched.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        self._send_json({"ok": True, "block": block})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                elif self.path.startswith("/planner/") and "/block/" in self.path and self.path.endswith("/toggle"):
                    # POST /planner/YYYY-MM-DD/block/{id}/toggle
                    parts = [p for p in self.path.split("/") if p]
                    target, block_id = parts[1], parts[3]
                    try:
                        sched = APP_SUPPORT / "schedules.json"
                        all_data = json.loads(sched.read_text(encoding="utf-8")) if sched.exists() else {}
                        blocks = all_data.setdefault(target, {}).setdefault("blocks", [])
                        for b in blocks:
                            if b.get("id") == block_id:
                                b["done"] = not b.get("done", False)
                                break
                        sched.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        self._send_json({"ok": True})
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                elif self.path == "/command":
                    n = int(self.headers.get("Content-Length", 0))
                    try:
                        cmd = json.loads(self.rfile.read(n))
                        # 预测新状态：让手机端无需等待下次轮询即可刷新 UI
                        action = cmd.get("action")
                        task   = cmd.get("task")
                        if action == "start" and task:
                            predicted_task  = task
                            predicted_start = datetime.now().isoformat()
                        elif action in ("pause", "finish"):
                            predicted_task  = None
                            predicted_start = None
                        else:
                            predicted_task  = app.current_task
                            predicted_start = app.task_start.isoformat() if app.task_start else None
                        app._http_queue.put(cmd)
                        self._send_json({
                            "ok":           True,
                            "current_task": predicted_task,
                            "task_start":   predicted_start,
                            "server_url":   app._server_url,
                        })
                    except Exception as e:
                        self._send_json({"error": str(e)}, 400)
                else:
                    self.send_response(404); self.end_headers()

        try:
            server = HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        except OSError:
            pass  # 端口占用时跳过，不影响主功能

    # ── 命令执行（桌面 widget / iPhone Scriptable 均可触发） ──────────
    def _execute_command(self, cmd: dict, confirm: bool = False):
        """
        confirm=True  → 桌面 widget 按钮（Mac 端，切换时弹确认框）
        confirm=False → 手机 HTTP / iCloud Scriptable（静默切换）
        """
        action = cmd.get("action")
        task   = cmd.get("task")

        if action == "start" and task:
            if self.current_task == task:
                return
            if self.current_task and confirm:
                resp = rumps.alert(
                    title="切换任务",
                    message=(
                        f"当前：{self.current_task}（{fmt(self.elapsed)}）\n\n"
                        f"切换到「{task}」？"
                    ),
                    ok="切换",
                    cancel="取消",
                )
                if resp == 0:
                    return
            if self.current_task:
                self._save_current()
            self._start(task)

        elif action == "pause":
            if self.current_task:
                self._save_current()
                self._clear("⏸ 暂停中")

        elif action == "checkin_toggle":
            item = cmd.get("item")
            if item and item in self.checkin_items:
                if item in self.checkin_done:
                    self.checkin_done.discard(item)
                else:
                    self.checkin_done.add(item)
                self._save_checkin()
                self._write_state()
                try:
                    self.menu[item].state = 1 if item in self.checkin_done else 0
                except Exception:
                    pass

        elif action == "add_item":
            section = cmd.get("section")
            item    = cmd.get("item", "").strip()
            if not item:
                return
            if section == "checkin" and item not in self.checkin_items:
                self.checkin_items.append(item)
                self._save_checkin()
                self._write_state()
                self._build_menu()
            elif section == "work" and item not in self.work_tasks:
                self.work_tasks.append(item)
                self._save_custom_tasks()
                self._write_state()
                self._build_menu()
            elif section == "life" and item not in self.life_tasks:
                self.life_tasks.append(item)
                self._save_custom_tasks()
                self._write_state()
                self._build_menu()

        elif action == "del_item":
            section = cmd.get("section")
            item    = cmd.get("item", "").strip()
            if not item:
                return
            if section == "checkin" and item in self.checkin_items:
                self.checkin_items.remove(item)
                self.checkin_done.discard(item)
                self._save_checkin()
                self._write_state()
                self._build_menu()
            elif section == "work" and item in self.work_tasks:
                self.work_tasks.remove(item)
                self._save_custom_tasks()
                self._write_state()
                self._build_menu()
            elif section == "life" and item in self.life_tasks:
                self.life_tasks.remove(item)
                self._save_custom_tasks()
                self._write_state()
                self._build_menu()

        elif action == "morning_planner":
            self._show_morning_planner()

        elif action == "finish":
            if self.current_task:
                if (self.current_task in self.work_tasks and confirm and self.elapsed < 60 * 60):
                    resp = rumps.alert(
                        title="⚠️  还没满 1 小时",
                        message=(
                            f"你正在做：{self.current_task}\n"
                            f"已专注：{fmt(self.elapsed)}\n\n"
                            f"真的要结束吗？"
                        ),
                        ok="结束",
                        cancel="继续专注",
                    )
                    if resp == 0:
                        return
                elapsed_str = fmt(self.elapsed)
                task_name   = self.current_task
                self._save_current()
                self._clear()
                rumps.notification(
                    title="✅  任务完成",
                    subtitle=task_name,
                    message=f"专注了 {elapsed_str}",
                )

    @rumps.timer(1)
    def _poll_command(self, _):
        # 1. HTTP queue（手机 Scriptable 直连）→ 无弹窗
        try:
            cmd = self._http_queue.get_nowait()
            self._execute_command(cmd, confirm=False)
            return
        except queue.Empty:
            pass
        # 2. 桌面 widget command.json → 弹确认框
        if COMMAND_FILE.exists():
            try:
                cmd = json.loads(COMMAND_FILE.read_text(encoding="utf-8"))
                COMMAND_FILE.unlink()
                self._execute_command(cmd, confirm=True)
            except Exception:
                pass
            return
        # 3. iOS Scriptable iCloud 备用命令 → 无弹窗
        if SCRIPTABLE_COMMAND.exists():
            try:
                cmd = json.loads(SCRIPTABLE_COMMAND.read_text(encoding="utf-8"))
                SCRIPTABLE_COMMAND.unlink()
                self._execute_command(cmd, confirm=False)
            except Exception:
                pass

    # ── 定时器 ────────────────────────────────────────────────────────
    @rumps.timer(30)
    def _tick(self, _):
        """每30秒：更新菜单栏标题 + 检查是否该提醒"""
        # IP 变化检测（现在用 mDNS 不依赖 IP，仅保留以备不时之需）
        current_ip = _local_ip()
        if current_ip != self._last_ip:
            self._last_ip = current_ip
            self._server_url = _stable_url()   # 重新计算（IP fallback 路径会更新）
            self._write_state()

        # 每日重置打卡
        today = date.today().isoformat()
        if today != self._checkin_date:
            self._checkin_date = today
            self.checkin_done = set()
            self._save_checkin()
            self._build_menu()

        # 定期同步状态 + 时间记录 → iCloud JSON（让手机 widget 实时更新）
        self._write_state()
        self._write_timeline()

        if not self.current_task or not self.task_start:
            return

        # 更新标题
        self.title = f"{self.current_task}  {fmt(self.elapsed)}"

        # 每 10 分钟提醒（仅工作类任务）
        now = datetime.now().timestamp()
        if (self.current_task in self.work_tasks
                and now - self._last_reminder >= REMINDER_INTERVAL):
            self._last_reminder = now
            rumps.notification(
                title=f"⏰  还在做「{self.current_task}」吗？",
                subtitle=f"已专注 {fmt(self.elapsed)}",
                message="如果跑偏了，回来吧 👆",
            )

    # ── 统计 ─────────────────────────────────────────────────────────
    def _show_summary(self, where_clause, params, title):
        with sqlite3.connect(DB_PATH) as c:
            rows = c.execute(
                f"SELECT start_time, end_time, duration, category FROM entries "
                f"WHERE {where_clause} ORDER BY start_time, id",
                params,
            ).fetchall()

        cat_mins = _timeline_stats(rows)
        if not cat_mins:
            msg = "暂无记录"
        else:
            total = sum(cat_mins.values())
            lines = [f"{cat}：{fmt(m * 60)}" for cat, m in sorted(cat_mins.items(), key=lambda x: -x[1])]
            lines += ["─" * 24, f"合计：{fmt(total * 60)}"]
            if self.current_task and self.task_start:
                lines += ["", f"▶ 进行中：{self.current_task}（{fmt(self.elapsed)}）"]
            msg = "\n".join(lines)

        rumps.alert(title=title, message=msg, ok="好的")

    def _on_today(self, _):
        today = date.today().strftime("%Y-%m-%d")
        self._show_summary("start_time LIKE ?", (f"{today}%",), "今日记录")

    def _on_week(self, _):
        from datetime import timedelta
        start = (date.today() - timedelta(days=6)).strftime("%Y-%m-%d")
        self._show_summary("start_time >= ?", (start,), "本周记录（近7天）")

    def _on_export(self, _):
        today = date.today().strftime("%Y%m%d")
        path  = EXPORT_DIR / f"orbit_export_{today}.csv"
        with sqlite3.connect(DB_PATH) as c:
            rows = c.execute(
                "SELECT start_time, duration, category, note, status FROM entries ORDER BY start_time"
            ).fetchall()

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADERS)
            for start, dur, cat, note, status in rows:
                major, minor = CATEGORY_MAP.get(cat, ("未分类", cat))
                w.writerow([start, dur, major, minor, "未分类", "", note, "", status])

        rumps.notification(title="✅  导出完成", subtitle=str(path), message=f"共 {len(rows)} 条记录")

    # ── 时间块视图 ────────────────────────────────────────────────────
    def _on_backlog(self, _):
        subprocess.Popen(_helper_cmd("orbit_backlog"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _on_timeline(self, _):
        subprocess.Popen(_helper_cmd("orbit_timeline"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── 项目甘特图 ────────────────────────────────────────────────────
    def _on_gantt(self, _):
        subprocess.Popen(_helper_cmd("orbit_gantt"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── 时间趋势图 ────────────────────────────────────────────────────
    def _on_trend(self, _):
        subprocess.Popen(_helper_cmd("orbit_trend"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── 周热力图 ──────────────────────────────────────────────────────
    def _on_heatmap(self, _):
        subprocess.Popen(_helper_cmd("orbit_heatmap"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── 每分钟检查定时任务 ────────────────────────────────────────────
    @rumps.timer(60)
    def _check_daily_log(self, _):
        now   = datetime.now()
        today = date.today().isoformat()
        # 23:00 自动更新 Obsidian 日志
        if now.hour == 23 and now.minute == 0 and today not in self._auto_log_done:
            self._auto_log_done.add(today)
            self._update_obsidian_log()
        # 21:30–23:00 之间，只要还没弹过就弹出明天上午规划对话框
        total_min = now.hour * 60 + now.minute
        if 21 * 60 + 30 <= total_min < 23 * 60 and today not in self._morning_plan_done:
            self._morning_plan_done.add(today)
            self._show_morning_planner()

    def _show_morning_planner(self):
        """弹出对话框让用户填写明天上午任务，创建 9:00 iOS 提醒"""
        from datetime import timedelta
        w = rumps.Window(
            title="🌅 安排明天上午",
            message="明天上午想做什么？（会在手机创建 8:15 提醒）",
            default_text="",
            ok="创建提醒", cancel="跳过",
            dimensions=(320, 30),
        )
        r = w.run()
        if not r.clicked or not r.text.strip():
            return
        task = r.text.strip().replace("\\", "\\\\").replace('"', '\\"')
        # AppleScript：locale-independent，用相对时间计算「明天 9:00」
        script = f'''
tell application "Reminders"
    set d to (current date)
    set time of d to 0
    set d to d + (1 * days) + (8 * hours) + (15 * minutes)
    make new reminder with properties {{name:"🌅 {task}", remind me date:d}}
end tell'''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode == 0:
            rumps.notification(
                title="✅ 提醒已创建",
                subtitle=f"明天 08:15：{r.text.strip()}",
                message="在手机「提醒事项」查看",
            )
        else:
            rumps.alert(
                title="创建提醒失败",
                message=result.stderr or "osascript 执行出错",
                ok="好"
            )

    def _update_obsidian_log(self):
        try:
            # 查询 DB 中所有有记录的日期
            with sqlite3.connect(DB_PATH) as c:
                dates = [r[0] for r in c.execute(
                    "SELECT DISTINCT substr(start_time,1,10) FROM entries ORDER BY 1"
                ).fetchall()]

            if not dates:
                rumps.notification(title="📊 日志更新", subtitle="暂无记录", message="")
                return

            updated = []
            for date_str in dates:
                self._update_log_for_date(date_str)
                updated.append(date_str)

            rumps.notification(
                title="📊 Obsidian 日志已更新",
                subtitle=f"共更新 {len(updated)} 天",
                message=f"{updated[0]} ~ {updated[-1]}",
            )
        except Exception as ex:
            rumps.notification(title="日志更新失败", subtitle=str(ex), message="")

    def _update_log_for_date(self, date_str: str):
        if OBSIDIAN_LOG_DIR is None:
            return
        from datetime import date as _date
        _d = _date.fromisoformat(date_str)
        day_start = datetime(_d.year, _d.month, _d.day, 0, 0)
        day_end   = day_start + timedelta(days=1)
        prev_date = (_d - timedelta(days=1)).isoformat()

        with sqlite3.connect(DB_PATH) as c:
            raw_rows = c.execute(
                "SELECT start_time, end_time, duration, category FROM entries "
                "WHERE start_time LIKE ? "
                "UNION "
                "SELECT start_time, end_time, duration, category FROM entries "
                "WHERE start_time LIKE ? AND end_time > ? "
                "ORDER BY start_time",
                (f"{date_str}%", f"{prev_date}%", f"{date_str} 00:00")
            ).fetchall()

        # 裁剪到当天边界 [day_start, day_end)
        all_rows = []
        for s_str, e_str, _dur, cat in raw_rows:
            try:
                s = max(datetime.strptime(s_str, "%Y-%m-%d %H:%M"), day_start)
                e = min(datetime.strptime(e_str, "%Y-%m-%d %H:%M"), day_end)
                if e > s:
                    all_rows.append((s.strftime("%Y-%m-%d %H:%M"),
                                     e.strftime("%Y-%m-%d %H:%M"),
                                     int((e - s).total_seconds() / 60), cat))
            except Exception:
                pass

        if not all_rows:
            return

        # ── 分类汇总（timeline-accurate：15min cell most-overlap-wins） ──
        cat_totals = _timeline_stats(all_rows)

        def _main_period(task_set):
            """找 task_set 中最长连续块（≤5min 间隙视为连续），返回 (start, end) 或 (None, None)。"""
            sessions = []
            for start_str, end_str, _dur, cat in all_rows:
                if cat in task_set:
                    try:
                        s = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                        e = datetime.strptime(end_str,   "%Y-%m-%d %H:%M")
                        sessions.append((s, e))
                    except Exception:
                        pass
            if not sessions:
                return None, None
            merged = [list(sessions[0])]
            for s, e in sessions[1:]:
                if (s - merged[-1][1]).total_seconds() <= 5 * 60:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            ms, me = max(merged, key=lambda x: (x[1] - x[0]).total_seconds())
            return ms, me

        # ── 格式化表格 ────────────────────────────────────────────
        lines = ["**数据速览**（周天）", "",
                 "| 类别 | 时长 |", "|------|------|"]

        for label, task_set in [("Study", STUDY_TASKS), ("工作", WORK_TASKS)]:
            total = sum(cat_totals.get(t, 0) for t in task_set)
            if total > 0:
                h, m = divmod(total, 60)
                time_str = f"{h}h {m}m" if h else f"{m} min"
                ms, me = _main_period(task_set)
                if ms and me:
                    time_str += f"（主力：{ms.strftime('%H:%M')}–{me.strftime('%H:%M')}）"
                lines.append(f"| {label} | {time_str} |")

        life_rows = [(c, m) for c, m in cat_totals.items()
                     if c not in STUDY_TASKS and c not in WORK_TASKS]
        for cat, mins in sorted(life_rows, key=lambda x: -x[1]):
            h, m = divmod(mins, 60)
            time_str = f"{h}h {m}m" if h else f"{m} min"
            lines.append(f"| {cat} | {time_str} |")

        total = sum(cat_totals.values())
        th, tm = divmod(total, 60)
        lines.append(f"| **合计** | **{th}h {tm}m** |")
        block = "\n".join(lines)

        # ── 写入 Obsidian log ──────────────────────────────────────
        fname = _d.strftime("%m-%d-%a")   # e.g. 03-18-Wed
        log_path = OBSIDIAN_LOG_DIR / f"{fname}.md"
        # 兼容旧格式：若旧文件存在则迁移
        old_path = OBSIDIAN_LOG_DIR / f"{date_str}.md"
        if old_path.exists() and not log_path.exists():
            old_path.rename(log_path)
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            pattern = r'\*\*数据速览\*\*.*?(?=\n\n---|\n\n#|\Z)'
            if re.search(pattern, content, re.DOTALL):
                new_content = re.sub(pattern, block, content, flags=re.DOTALL)
            else:
                new_content = content.rstrip() + "\n\n" + block + "\n"
        else:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            new_content = (f"# {fname}\n\n---\n\n"
                           f"**今天完成的事**\n\n\n\n---\n\n{block}\n")

        log_path.write_text(new_content, encoding="utf-8")

    # ── 退出 ─────────────────────────────────────────────────────────
    def _on_quit(self, _):
        if self.current_task:
            resp = rumps.alert(
                title="退出周天",
                message=f"当前任务「{self.current_task}」（{fmt(self.elapsed)}）将保存后退出。",
                ok="保存并退出",
                cancel="取消",
            )
            if resp == 0:
                return
            self._save_current()
        self._write_state()
        rumps.quit_application()

    def _on_copy_url(self, _):
        try:
            from AppKit import NSPasteboard, NSStringPboardType
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self._server_url, NSStringPboardType)
        except Exception:
            pass
        rumps.notification(
            title="📡 服务地址已复制",
            subtitle=self._server_url,
            message="将此地址填入 Scriptable 脚本",
        )


if __name__ == "__main__":
    Orbit().run()
