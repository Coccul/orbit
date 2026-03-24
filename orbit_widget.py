#!/usr/bin/env python3
"""
Orbit Unified Window
左侧导航栏 + 右侧内容区（🏠 widget / 📅 日程 / ⏱ 时间轴）
工作任务进行中 → 收成正方形迷你播放器，鼠标悬停显示控制栏
"""

import tkinter as tk
from tkinter import simpledialog, filedialog
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path

APP_SUPPORT  = Path.home() / "Library" / "Application Support" / "Orbit"
STATE_FILE   = APP_SUPPORT / "state.json"
COMMAND_FILE = APP_SUPPORT / "command.json"

_HERE = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
         else Path(__file__).parent)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import orbit_config
PROJ_MAP = [(p[0], p[1], p[2]) for p in orbit_config.load_obsidian_projects()]
TASK_BG  = orbit_config.load_task_colors()
TASK_FG  = {t: "#FFFFFF" for t in TASK_BG}

CHECKIN_DONE_BG   = "#72B87A"
CHECKIN_UNDONE_BG = "#F1F5F9"
CHECKIN_DONE_FG   = "#FFFFFF"
CHECKIN_UNDONE_FG = "#64748B"

DEFAULT_FG  = "#94A3B8"
DEFAULT_TXT = "#334155"
BG          = "#FFFFFF"
SIDEBAR_BG  = "#F5F6F7"
SIDEBAR_W   = 44
CONTENT_W   = 364
TOTAL_W     = SIDEBAR_W + CONTENT_W   # 408
WIN_H       = 590
FOCUS_SQ    = 220   # 正方形专注模式边长


_SCHEDULES_FILE = APP_SUPPORT / "schedules.json"
_PROJ_COLORS = ["#5B8FD4", "#E07068", "#9880CC", "#E8956A",
                "#4AABB0", "#68B868", "#DDB86A", "#E8A0BF"]


def _add_schedule_block(target_date: str, text: str, color: str = "#5B8FD4"):
    """Append a 60-min block to schedules.json for target_date."""
    try:
        schedules = json.loads(_SCHEDULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        schedules = {}
    day = schedules.setdefault(target_date, {"blocks": [], "todos": []})
    blocks = day.setdefault("blocks", [])
    last_end = max((b.get("end_min", 0) for b in blocks), default=9 * 60)
    start = max(last_end, 9 * 60)
    blocks.append({
        "id": uuid.uuid4().hex[:8],
        "text": text, "color": color,
        "start_min": start, "end_min": start + 60,
        "done": False, "skip_reason": "",
    })
    _SCHEDULES_FILE.write_text(
        json.dumps(schedules, ensure_ascii=False, indent=2), encoding="utf-8")


def fmt(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}" if h else f"{m}m"


def send_command(action: str, task: str = None, **kwargs):
    try:
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        cmd = {"action": action, "timestamp": datetime.now().isoformat()}
        if task:
            cmd["task"] = task
        cmd.update(kwargs)
        COMMAND_FILE.write_text(json.dumps(cmd, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _open_helper(name: str):
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    for candidate in [base / f"{name}.py", base / name]:
        if candidate.exists():
            cmd = (["/opt/homebrew/bin/python3", str(candidate)]
                   if candidate.suffix == '.py'
                   else [str(candidate)])
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def _brighten(hex_color: str, amount: int = 22) -> str:
    r = min(255, int(hex_color[1:3], 16) + amount)
    g = min(255, int(hex_color[3:5], 16) + amount)
    b = min(255, int(hex_color[5:7], 16) + amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix_white(hex_color: str, ratio: float = 0.82) -> str:
    """Mix hex_color with white (ratio = how much white)."""
    r = int(int(hex_color[1:3], 16) * (1 - ratio) + 255 * ratio)
    g = int(int(hex_color[3:5], 16) * (1 - ratio) + 255 * ratio)
    b = int(int(hex_color[5:7], 16) * (1 - ratio) + 255 * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix_gray(hex_color: str, ratio: float = 0.55) -> str:
    """Dim hex_color toward gray (ratio = how much color to keep)."""
    r = int(int(hex_color[1:3], 16) * ratio + 180 * (1 - ratio))
    g = int(int(hex_color[3:5], 16) * ratio + 180 * (1 - ratio))
    b = int(int(hex_color[5:7], 16) * ratio + 180 * (1 - ratio))
    return f"#{r:02x}{g:02x}{b:02x}"


# ── 圆角按钮 ────────────────────────────────────────────────────────
class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, bg_color, fg_color,
                 command=None, radius=9, btn_h=30, **kwargs):
        super().__init__(parent, height=btn_h,
                         bg=parent["bg"], highlightthickness=0,
                         borderwidth=0, **kwargs)
        self._text  = text
        self._bg    = bg_color
        self._fg    = fg_color
        self._r     = radius
        self._cmd   = command
        self._hover = False

        self.bind("<Configure>",       lambda e: self._draw())
        self.bind("<Enter>",           lambda e: self._set_hover(True))
        self.bind("<Leave>",           lambda e: self._set_hover(False))
        self.bind("<Button-1>",        self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def _press(self, e):
        self._draw(pressed=True)

    def _release(self, e):
        self._draw()
        if self._cmd:
            self._cmd()

    def _draw(self, pressed=False):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._r, w // 2, h // 2)
        fill = (_brighten(self._bg, 35) if pressed
                else _brighten(self._bg, 20) if self._hover
                else self._bg)
        pts = [
            r, 0,    w-r, 0,
            w, 0,    w,   r,
            w, h-r,  w,   h,
            w-r, h,  r,   h,
            0, h,    0, h-r,
            0, r,    0,   0,
        ]
        self.create_polygon(pts, smooth=True, fill=fill, outline="")
        self.create_text(w // 2, h // 2, text=self._text,
                         fill=self._fg,
                         font=("Helvetica Neue", 9, "bold"),
                         anchor="center")


# ── 主应用 ────────────────────────────────────────────────────────────
class FocusApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("")
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception:
            pass
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)
        self.root.configure(bg="#E8E9EA")
        self.root.resizable(False, False)
        self.root.after(200, self._setup_nswindow)

        self._dx = self._dy = 0
        self._current_page  = None
        self._page_inited   = set()
        self._focus_mode    = False
        self._sq_hide_job   = None
        self._sq_ctrl_shown = False
        self._trend_app     = None
        self._stats_app     = None
        self._heatmap_app   = None
        self._planner_app   = None

        # ── 布局：侧边栏 | 分割线 | 内容区 ──────────────────────────
        self._sidebar = tk.Frame(self.root, bg=SIDEBAR_BG, width=SIDEBAR_W)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._divider = tk.Frame(self.root, bg="#E2E4E6", width=1)
        self._divider.pack(side="left", fill="y")

        self._content = tk.Frame(self.root, bg=BG)
        self._content.pack(side="left", fill="both", expand=True)

        # ── Widget 状态数据 ──────────────────────────────────────────
        self._work_tasks:    list = []
        self._life_tasks:    list = []
        self._checkin_items: list = []
        self._checkin_done:  list = []
        self._checkin_date:  str  = ""

        # ── 构建 UI ──────────────────────────────────────────────────
        self._build_sidebar()
        self._build_home_page()
        self._build_focus_square()

        self._show_page("home")
        self._tick()
        self.root.after(120, lambda: self._update_geometry("home"))
        self.root.mainloop()

    # ─────────────────────────────────────────────────────────────────
    # 侧边栏
    # ─────────────────────────────────────────────────────────────────
    _NAV_ALL_ICONS = {
        "home":      "🏠",
        "planner":   "📅",
        "timeline":  "⏱",
        "trend":     "📊",
        "stats":     "🍩",
        "backlog":   "📋",
        "breakdown": "🔍",
        "projects":  "📁",
        "settings":  "⚙️",
    }
    _NAV_DEFAULT_ORDER = ["home", "planner", "timeline", "trend", "stats", "backlog", "breakdown", "projects", "settings"]

    def _load_nav_order(self) -> list:
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            saved = s.get("nav_order", [])
            # Merge: saved order first, then append any new items not yet in saved
            merged = [x for x in saved if x in self._NAV_ALL_ICONS]
            for x in self._NAV_DEFAULT_ORDER:
                if x not in merged:
                    merged.append(x)
            return merged
        except Exception:
            return list(self._NAV_DEFAULT_ORDER)

    def _save_nav_order(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            data["nav_order"] = self._nav_order
            STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _build_sidebar(self):
        self._nav_btns    = {}
        self._nav_order   = self._load_nav_order()
        self._nav_drag    = None   # drag state dict
        self._drop_line   = None   # visual indicator label

        self._nav_container = tk.Frame(self._sidebar, bg=SIDEBAR_BG)
        self._nav_container.pack(fill="x", pady=(0, 0))

        self._rebuild_nav_buttons()

        self._sidebar.bind("<Button-1>",  self._win_drag_start)
        self._sidebar.bind("<B1-Motion>", self._win_drag_move)

    def _rebuild_nav_buttons(self):
        for w in self._nav_container.winfo_children():
            w.destroy()
        self._nav_btns = {}
        self._drop_line = None

        for page_id in self._nav_order:
            icon = self._NAV_ALL_ICONS.get(page_id, "●")
            btn = tk.Label(self._nav_container, text=icon,
                           font=("Helvetica Neue", 18),
                           fg="#94A3B8", bg=SIDEBAR_BG,
                           cursor="hand2", width=2)
            btn.pack(pady=(2, 4))
            btn.bind("<ButtonPress-1>",   lambda e, p=page_id: self._nav_press(e, p))
            btn.bind("<B1-Motion>",       lambda e, p=page_id: self._nav_motion(e, p))
            btn.bind("<ButtonRelease-1>", lambda e, p=page_id: self._nav_release(e, p))
            btn.bind("<Enter>",  lambda e, b=btn: b.config(fg="#334155"))
            btn.bind("<Leave>",  lambda e, b=btn, p=page_id: b.config(
                fg="#334155" if self._current_page == p else "#94A3B8"))
            self._nav_btns[page_id] = btn

    # ── nav 拖拽排序 ───────────────────────────────────────────────────
    def _nav_press(self, e, page_id):
        self._nav_drag = {
            "page_id": page_id,
            "y0":      e.y_root,
            "dragging": False,
        }

    def _nav_motion(self, e, page_id):
        if self._nav_drag is None:
            return "break"
        dy = abs(e.y_root - self._nav_drag["y0"])
        if dy < 8:
            return "break"
        self._nav_drag["dragging"] = True
        self._show_drop_indicator(e.y_root)
        return "break"   # 阻止触发窗口拖拽

    def _nav_release(self, e, page_id):
        if self._nav_drag is None:
            return
        was_dragging = self._nav_drag["dragging"]
        drop_y = e.y_root
        drag_id = self._nav_drag["page_id"]
        self._nav_drag = None
        self._hide_drop_indicator()

        if not was_dragging:
            self._show_page(page_id)
            return

        # 计算插入位置
        insert_idx = self._drop_index_for_y(drop_y)
        old_idx = self._nav_order.index(drag_id)
        self._nav_order.pop(old_idx)
        if insert_idx > old_idx:
            insert_idx -= 1
        self._nav_order.insert(insert_idx, drag_id)
        self._save_nav_order()
        self._rebuild_nav_buttons()
        self._update_nav_highlight()

    def _drop_index_for_y(self, y_root: int) -> int:
        """根据鼠标绝对 y 坐标，找最近的插入位（0 = 最顶部）。"""
        positions = []
        for pid in self._nav_order:
            btn = self._nav_btns.get(pid)
            if btn:
                by = btn.winfo_rooty() + btn.winfo_height() // 2
                positions.append(by)
        if not positions:
            return 0
        for i, mid in enumerate(positions):
            if y_root < mid:
                return i
        return len(positions)

    def _show_drop_indicator(self, y_root: int):
        """在目标插入位上方画一条蓝线。"""
        idx = self._drop_index_for_y(y_root)
        # 找参照 widget 的 y 坐标
        if idx < len(self._nav_order):
            ref_pid = self._nav_order[idx]
            ref_btn = self._nav_btns.get(ref_pid)
            ref_y = (ref_btn.winfo_y() - 2) if ref_btn else 0
        else:
            # 插在最后
            last_pid = self._nav_order[-1]
            ref_btn  = self._nav_btns.get(last_pid)
            ref_y    = (ref_btn.winfo_y() + ref_btn.winfo_height() + 2) if ref_btn else 100

        if self._drop_line is None:
            self._drop_line = tk.Frame(self._nav_container, bg="#5B8FD4", height=2)
        self._drop_line.place(x=4, y=ref_y, width=SIDEBAR_W - 8)

    def _hide_drop_indicator(self):
        if self._drop_line:
            self._drop_line.place_forget()

    def _update_nav_highlight(self):
        for pid, btn in self._nav_btns.items():
            btn.config(fg="#334155" if pid == self._current_page else "#94A3B8")

    # ── 窗口拖拽（只在点击侧边栏空白区时触发） ────────────────────────
    def _win_drag_start(self, e):
        if self._nav_drag:   # nav 按钮正在处理，忽略
            return
        self._dx, self._dy = e.x, e.y

    def _win_drag_move(self, e):
        if self._nav_drag and self._nav_drag.get("dragging"):
            return          # nav 排序中，不移动窗口
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f"+{x}+{y}")

    # ─────────────────────────────────────────────────────────────────
    # 页面切换
    # ─────────────────────────────────────────────────────────────────
    def _show_page(self, name):
        for child in self._content.winfo_children():
            child.pack_forget()

        # Clean up projects scroll routing when leaving projects page
        if self._current_page == "projects" and self._planner_app:
            self._planner_app._dialog_scroll_v = None
            self._planner_app._dialog_scroll_h = None

        if name not in self._page_inited:
            if name == "planner":
                self._init_planner_page()
            elif name == "timeline":
                self._init_timeline_page()
            elif name == "trend":
                self._init_trend_page()
            elif name == "stats":
                self._init_stats_page()
            elif name == "heatmap":
                self._init_heatmap_page()
            elif name == "backlog":
                self._init_backlog_page()
            elif name == "breakdown":
                self._init_breakdown_page()
            elif name == "projects":
                self._init_projects_page()
            elif name == "settings":
                self._init_settings_page()
            self._page_inited.add(name)

        if name == "home":
            self._home_frame.pack(fill="both", expand=True)
        elif name == "planner":
            self._planner_frame.pack(fill="both", expand=True)
        elif name == "timeline":
            self._timeline_frame.pack(fill="both", expand=True)
        elif name == "trend":
            self._trend_frame.pack(fill="both", expand=True)
            if self._trend_app:
                self._trend_app.refresh()
        elif name == "stats":
            self._stats_frame.pack(fill="both", expand=True)
            if self._stats_app:
                self._stats_app.refresh()
        elif name == "heatmap":
            self._heatmap_frame.pack(fill="both", expand=True)
            if self._heatmap_app:
                self._heatmap_app.refresh()
        elif name == "backlog":
            self._backlog_frame.pack(fill="both", expand=True)
            if hasattr(self, "_backlog_app") and self._backlog_app:
                self._backlog_app._refresh()
        elif name == "breakdown":
            self._breakdown_frame.pack(fill="both", expand=True)
            if hasattr(self, "_breakdown_app") and self._breakdown_app:
                self._breakdown_app._refresh()
        elif name == "projects":
            self._projects_frame.pack(fill="both", expand=True)
            # Reset to list if user left while on detail view
            if getattr(self, '_proj_view', None) == "detail":
                self._show_projects_list()
        elif name == "settings":
            self._settings_frame.pack(fill="both", expand=True)
            self._refresh_settings_page()

        self._current_page = name
        self._update_nav_highlight()
        self._update_geometry(name)

    # ─────────────────────────────────────────────────────────────────
    # Home 页
    # ─────────────────────────────────────────────────────────────────
    def _build_home_page(self):
        f = tk.Frame(self._content, bg=BG)
        self._home_frame = f

        top = tk.Frame(f, bg=BG)
        top.pack(fill="x", padx=10, pady=(8, 3))

        self.task_var = tk.StringVar(value="⚡ 未开始")
        self.task_lbl = tk.Label(top, textvariable=self.task_var,
                                  font=("Helvetica Neue", 11, "bold"),
                                  fg=DEFAULT_TXT, bg=BG, anchor="w")
        self.task_lbl.pack(side="left", fill="x", expand=True)

        min_btn = tk.Label(top, text="−", font=("Helvetica Neue", 16, "bold"),
                           fg="#CBD5E1", bg=BG, cursor="hand2")
        min_btn.pack(side="right", padx=(4, 0))
        min_btn.bind("<Button-1>", lambda e: self.root.iconify())
        min_btn.bind("<Enter>", lambda e: min_btn.config(fg="#94A3B8"))
        min_btn.bind("<Leave>", lambda e: min_btn.config(fg="#CBD5E1"))

        self.time_var = tk.StringVar(value="—")
        self.time_lbl = tk.Label(top, textvariable=self.time_var,
                                  font=("Helvetica Neue", 21, "bold"),
                                  fg=DEFAULT_FG, bg=BG, anchor="e")
        self.time_lbl.pack(side="right")

        tk.Frame(f, bg="#E2E8F0", height=1).pack(fill="x", padx=8, pady=(0, 2))

        self.page1_frame = tk.Frame(f, bg=BG)
        self.page1_frame.pack(fill="x")

        self._section_header("工作", "work", parent=self.page1_frame)
        self.work_frame = tk.Frame(self.page1_frame, bg=BG)
        self.work_frame.pack(fill="x", padx=6, pady=(0, 2))

        self._section_header("生活", "life", parent=self.page1_frame)
        self.life_frame = tk.Frame(self.page1_frame, bg=BG)
        self.life_frame.pack(fill="x", padx=6, pady=(0, 2))

        tk.Frame(self.page1_frame, bg="#E2E8F0", height=1).pack(
            fill="x", padx=8, pady=(2, 0))
        self._section_header("今日打卡", "checkin", parent=self.page1_frame)
        self.checkin_frame = tk.Frame(self.page1_frame, bg=BG)
        self.checkin_frame.pack(fill="x", padx=6, pady=(0, 4))

        bot = tk.Frame(f, bg=BG)
        bot.pack(fill="x", padx=6, pady=(2, 8))
        RoundedButton(bot, "⏹  结束", "#72B87A", "#FFFFFF",
                      command=lambda: send_command("finish"),
                      radius=12, btn_h=34).pack(expand=True, fill="x")
        RoundedButton(bot, "🌅  安排明天上午", "#8B9BB4", "#FFFFFF",
                      command=lambda: send_command("morning_planner"),
                      radius=12, btn_h=28).pack(expand=True, fill="x", pady=(4, 0))
        RoundedButton(bot, "📈 甘特图", "#E2E8F0", "#64748B",
                      command=lambda: _open_helper("orbit_gantt"),
                      radius=10, btn_h=26).pack(expand=True, fill="x", pady=(4, 0))

        for w in (top, self.task_lbl, self.time_lbl):
            w.bind("<Button-1>",  self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    # ─────────────────────────────────────────────────────────────────
    # 正方形专注迷你播放器
    # ─────────────────────────────────────────────────────────────────
    def _build_focus_square(self):
        sq_bg = "#EFEFEF"
        sq = tk.Frame(self.root, bg=sq_bg,
                      width=FOCUS_SQ, height=FOCUS_SQ)
        sq.pack_propagate(False)
        self._sq_frame = sq
        # NOT packed initially

        # ── 大 emoji（任务图标，居中偏上）────────────────────────────
        self._sq_emoji = tk.Label(sq, text="⚡",
                                   font=("Apple Color Emoji", 62),
                                   bg=sq_bg, fg="#BBBBBB")
        self._sq_emoji.place(relx=0.5, rely=0.38, anchor="center")

        # ── 计时器 ────────────────────────────────────────────────────
        self._sq_timer = tk.Label(sq, text="—",
                                   font=("Helvetica Neue", 20, "bold"),
                                   bg=sq_bg, fg="#555555")
        self._sq_timer.place(relx=0.5, rely=0.68, anchor="center")

        # ── 任务名（emoji 之后的文字）────────────────────────────────
        self._sq_name = tk.Label(sq, text="",
                                  font=("Helvetica Neue", 10),
                                  bg=sq_bg, fg="#999999")
        self._sq_name.place(relx=0.5, rely=0.80, anchor="center")

        # ── 悬停控制层（初始隐藏，place/forget 切换）─────────────────
        # 右上角 pill：🏠 📅
        pill = tk.Frame(sq, bg="#FFFFFF", padx=2, pady=3,
                        relief="flat", bd=0)
        self._sq_pill = pill
        for icon, page in [("🏠", "home"), ("📅", "planner")]:
            lbl = tk.Label(pill, text=icon,
                           font=("Apple Color Emoji", 13),
                           bg="#FFFFFF", cursor="hand2",
                           padx=6, pady=1)
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, p=page: self._sq_nav(p))
            lbl.bind("<Enter>", self._sq_on_enter)
            lbl.bind("<Leave>", self._sq_on_leave)

        # 底部中央 ▪ 停止按钮（canvas 画圆 + 方块）
        stop_c = tk.Canvas(sq, width=46, height=46,
                           bg=sq_bg, highlightthickness=0)
        self._sq_stop = stop_c
        stop_c.create_oval(1, 1, 45, 45, fill="#E0E0E2", outline="")
        stop_c.create_rectangle(14, 14, 32, 32, fill="#555555", outline="")
        stop_c.bind("<Button-1>", lambda e: send_command("finish"))
        stop_c.bind("<Enter>", self._sq_on_enter)
        stop_c.bind("<Leave>", self._sq_on_leave)

        # hover 绑定（所有基础 widgets）
        for w in (sq, self._sq_emoji, self._sq_timer, self._sq_name):
            w.bind("<Enter>", self._sq_on_enter)
            w.bind("<Leave>", self._sq_on_leave)
            w.bind("<Button-1>",  self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    def _sq_on_enter(self, event=None):
        if self._sq_hide_job:
            self.root.after_cancel(self._sq_hide_job)
            self._sq_hide_job = None
        if not self._sq_ctrl_shown:
            self._sq_ctrl_shown = True
            self._sq_pill.place(relx=1.0, rely=0.0, x=-7, y=7, anchor="ne")
            self._sq_stop.place(relx=0.5, rely=1.0, y=-10, anchor="s")

    def _sq_on_leave(self, event=None):
        if self._sq_hide_job:
            self.root.after_cancel(self._sq_hide_job)
        self._sq_hide_job = self.root.after(180, self._sq_check_hide)

    def _sq_check_hide(self):
        self._sq_hide_job = None
        try:
            mx, my = self.root.winfo_pointerxy()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            if rx <= mx <= rx + FOCUS_SQ and ry <= my <= ry + FOCUS_SQ:
                return
        except Exception:
            pass
        if self._sq_ctrl_shown:
            self._sq_ctrl_shown = False
            self._sq_pill.place_forget()
            self._sq_stop.place_forget()

    def _sq_nav(self, page):
        """从专注模式跳到指定页。"""
        self._exit_focus_mode()
        self._show_page(page)

    def _sq_update(self, task, elapsed, accent):
        """更新正方形显示内容。"""
        sq_bg = _mix_white(accent, 0.84)
        emoji = task.split(" ")[0]
        name  = " ".join(task.split(" ")[1:]) if " " in task else task
        dim   = _mix_gray(accent, 0.35)

        self._sq_frame.config(bg=sq_bg)
        self._sq_emoji.config(text=emoji, bg=sq_bg, fg=dim)
        self._sq_timer.config(text=fmt(elapsed), bg=sq_bg, fg=accent)
        self._sq_name.config(text=name, bg=sq_bg, fg=_mix_gray(accent, 0.25))
        self._sq_stop.config(bg=sq_bg)

    # ─────────────────────────────────────────────────────────────────
    # 专注模式切换
    # ─────────────────────────────────────────────────────────────────
    def _enter_focus_mode(self):
        if self._focus_mode:
            return
        self._focus_mode = True
        # 隐藏普通界面所有元素
        self._sidebar.pack_forget()
        self._divider.pack_forget()
        self._content.pack_forget()
        # 显示正方形（直接挂 root）
        self._sq_frame.pack()
        self._update_geometry()

    def _exit_focus_mode(self):
        if not self._focus_mode:
            return
        self._focus_mode = False
        # 隐藏正方形
        self._sq_frame.pack_forget()
        # 控制层重置
        self._sq_ctrl_shown = False
        self._sq_pill.place_forget()
        self._sq_stop.place_forget()
        # 按正确顺序恢复普通界面
        self._sidebar.pack(side="left", fill="y")
        self._divider.pack(side="left", fill="y")
        self._content.pack(side="left", fill="both", expand=True)
        # 重新显示当前页（确保 home_frame 被 pack）
        page = self._current_page or "home"
        for child in self._content.winfo_children():
            child.pack_forget()
        if page == "home":
            self._home_frame.pack(fill="both", expand=True)
        elif page == "planner":
            self._planner_frame.pack(fill="both", expand=True)
        elif page == "timeline":
            self._timeline_frame.pack(fill="both", expand=True)
        elif page == "trend" and "trend" in self._page_inited:
            self._trend_frame.pack(fill="both", expand=True)
        elif page == "stats" and "stats" in self._page_inited:
            self._stats_frame.pack(fill="both", expand=True)
        elif page == "projects" and "projects" in self._page_inited:
            self._projects_frame.pack(fill="both", expand=True)
        elif page == "settings" and "settings" in self._page_inited:
            self._settings_frame.pack(fill="both", expand=True)
        self._update_geometry()

    # ─────────────────────────────────────────────────────────────────
    # Planner 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_planner_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._planner_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_planner import FocusPlanner
            self._planner_app = FocusPlanner(parent=f, win=self.root)
        except Exception as e:
            self._planner_app = None
            tk.Label(f, text=f"加载日程失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    # ─────────────────────────────────────────────────────────────────
    # Timeline 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_timeline_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._timeline_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_timeline import TimelineApp
            TimelineApp(f, win=self.root)
        except Exception as e:
            tk.Label(f, text=f"加载时间轴失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    # ─────────────────────────────────────────────────────────────────
    # Trend 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_trend_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._trend_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_trend import TrendApp
            self._trend_app = TrendApp(parent=f, win=self.root)
        except Exception as e:
            self._trend_app = None
            tk.Label(f, text=f"加载趋势图失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    # ─────────────────────────────────────────────────────────────────
    # Stats 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_stats_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._stats_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_stats import StatsApp
            self._stats_app = StatsApp(parent=f, win=self.root)
        except Exception as e:
            self._stats_app = None
            tk.Label(f, text=f"加载统计失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    # ─────────────────────────────────────────────────────────────────
    # Heatmap 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_heatmap_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._heatmap_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_heatmap import HeatmapApp
            self._heatmap_app = HeatmapApp(parent=f, win=self.root)
        except Exception as e:
            self._heatmap_app = None
            tk.Label(f, text=f"加载热力图失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    def _init_backlog_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._backlog_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_backlog import BacklogApp
            self._backlog_app = BacklogApp(parent=f, win=self.root)
        except Exception as e:
            self._backlog_app = None
            tk.Label(f, text=f"加载积压列表失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    # ─────────────────────────────────────────────────────────────────
    # Breakdown 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_breakdown_page(self):
        f = tk.Frame(self._content, bg="#FFFFFF")
        self._breakdown_frame = f
        try:
            base = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).parent)
            sys.path.insert(0, str(base))
            from orbit_breakdown import BreakdownApp
            self._breakdown_app = BreakdownApp(parent=f, win=self.root)
        except Exception as e:
            self._breakdown_app = None
            tk.Label(f, text=f"加载断点复盘失败：{e}",
                     font=("Helvetica Neue", 11), fg="#EF4444",
                     bg="#FFFFFF", wraplength=320).pack(pady=40)

    # ─────────────────────────────────────────────────────────────────
    # Projects 页（懒加载）
    # ─────────────────────────────────────────────────────────────────
    def _init_settings_page(self):
        f = tk.Frame(self._content, bg=BG)
        self._settings_frame = f

    def _refresh_settings_page(self):
        for w in self._settings_frame.winfo_children():
            w.destroy()
        cfg = orbit_config.load()
        f = self._settings_frame

        # Header
        tk.Label(f, text="设置", font=("Helvetica Neue", 13, "bold"),
                 fg=DEFAULT_TXT, bg=BG, anchor="w").pack(fill="x", padx=14, pady=(12, 8))

        # ── Obsidian section ───────────────────────────────────────────
        sec = tk.Frame(f, bg=BG)
        sec.pack(fill="x", padx=14, pady=(0, 6))

        # Toggle row
        row = tk.Frame(sec, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="Obsidian 集成", font=("Helvetica Neue", 11, "bold"),
                 fg=DEFAULT_TXT, bg=BG, anchor="w").pack(side="left")

        enabled = tk.BooleanVar(value=cfg.get("obsidian_enabled", False))

        def _toggle_obsidian():
            orbit_config.set_value("obsidian_enabled", enabled.get())
            self._refresh_settings_page()

        chk = tk.Checkbutton(row, variable=enabled, command=_toggle_obsidian,
                             bg=BG, activebackground=BG, cursor="hand2",
                             relief="flat", bd=0)
        chk.pack(side="right")

        tk.Label(sec, text="启用后可在「项目进度」页查看 Obsidian 项目进度.md",
                 font=("Helvetica Neue", 9), fg="#94A3B8", bg=BG,
                 anchor="w", wraplength=310).pack(fill="x", pady=(2, 8))

        # File path row (shown only when enabled)
        if cfg.get("obsidian_enabled", False):
            path_frame = tk.Frame(sec, bg=BG)
            path_frame.pack(fill="x", pady=(0, 6))
            tk.Label(path_frame, text="项目文件", font=("Helvetica Neue", 10),
                     fg=DEFAULT_TXT, bg=BG, width=7, anchor="w").pack(side="left")

            cur_path = cfg.get("obsidian_project_file", "") or "（未设置）"
            path_lbl = tk.Label(path_frame, text=cur_path,
                                font=("Helvetica Neue", 9), fg="#64748B", bg=BG,
                                anchor="w", wraplength=220, justify="left")
            path_lbl.pack(side="left", fill="x", expand=True, padx=(6, 4))

            def _pick_file():
                p = filedialog.askopenfilename(
                    title="选择项目进度.md",
                    filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
                    initialdir=str(Path.home())
                )
                if p:
                    orbit_config.set_value("obsidian_project_file", p)
                    # Invalidate projects cache so it reloads on next visit
                    if hasattr(self, "_proj_mtime"):
                        self._proj_mtime = 0.0
                    self._refresh_settings_page()

            sel_lbl = tk.Label(path_frame, text="选择", font=("Helvetica Neue", 9),
                               fg="#5B8FD4", bg=BG, cursor="hand2")
            sel_lbl.pack(side="right")
            sel_lbl.bind("<Button-1>", lambda e: _pick_file())

        # ── Divider ────────────────────────────────────────────────────
        tk.Frame(f, bg="#E2E8F0", height=1).pack(fill="x", padx=14, pady=8)

        # ── Server section ─────────────────────────────────────────────
        sec2 = tk.Frame(f, bg=BG)
        sec2.pack(fill="x", padx=14)

        row2 = tk.Frame(sec2, bg=BG)
        row2.pack(fill="x")
        tk.Label(row2, text="HTTP 服务端口", font=("Helvetica Neue", 11, "bold"),
                 fg=DEFAULT_TXT, bg=BG, anchor="w").pack(side="left")
        port_val = tk.StringVar(value=str(cfg.get("server_port", 46461)))

        port_entry = tk.Entry(row2, textvariable=port_val, width=7,
                              font=("Helvetica Neue", 10), fg=DEFAULT_TXT,
                              relief="solid", bd=1, highlightthickness=0)
        port_entry.pack(side="right")

        def _save_port(e=None):
            try:
                p = int(port_val.get())
                if 1024 <= p <= 65535:
                    orbit_config.set_value("server_port", p)
            except ValueError:
                pass

        port_entry.bind("<FocusOut>", _save_port)
        port_entry.bind("<Return>", _save_port)

        tk.Label(sec2, text="iPhone Scriptable 小组件通过此端口连接 Mac",
                 font=("Helvetica Neue", 9), fg="#94A3B8", bg=BG,
                 anchor="w", wraplength=310).pack(fill="x", pady=(2, 0))

    def _init_projects_page(self):
        f = tk.Frame(self._content, bg=BG)
        self._projects_frame = f
        self._proj_mtime = 0.0
        self._proj_data = {}
        self._proj_scroll_canvas = None
        self._reload_projects()
        self._show_projects_list()
        self._poll_projects()

    def _parse_internal_projects(self):
        result = {}
        for p in orbit_config.load_internal_projects():
            pid = p["id"]
            todos = [(t.get("done", False), t["text"]) for t in p.get("todos", [])]
            result[pid] = {
                "now": p.get("now", ""), "next": p.get("next", ""),
                "bottleneck": p.get("bottleneck", ""), "todos": todos,
                "_label": p.get("name", "项目"), "_color": p.get("color", "#5B8FD4"),
                "_id": pid,
            }
        return result

    def _parse_projects(self):
        if not orbit_config.get("obsidian_enabled", False):
            return self._parse_internal_projects()
        pf = orbit_config.project_file()
        if pf is None:
            return {}
        try:
            text = pf.read_text(encoding="utf-8")
        except Exception:
            return {}
        _HEADING_MAP = [(p[2], p[2]) for p in PROJ_MAP]
        sections = {}
        current_key = None
        current_data = None
        in_todos = False
        for line in text.splitlines():
            if line.startswith("# "):
                if current_key and current_data is not None:
                    sections[current_key] = current_data
                current_key = None
                for kw, key in _HEADING_MAP:
                    if kw in line:
                        current_key = key
                        break
                if current_key:
                    current_data = {"now": "", "next": "", "bottleneck": "", "todos": []}
                    in_todos = False
            elif current_key is None:
                continue
            elif "📍 现在：" in line:
                current_data["now"] = line.split("📍 现在：", 1)[1].strip()
                in_todos = False
            elif "⏭️ 下一步：" in line:
                current_data["next"] = line.split("⏭️ 下一步：", 1)[1].strip()
                in_todos = False
            elif "⚠️ 瓶颈：" in line:
                current_data["bottleneck"] = line.split("⚠️ 瓶颈：", 1)[1].strip()
                in_todos = False
            elif "⭕️ 待办" in line:
                in_todos = True
            else:
                m_open = re.search(r'- \[ \]\s*(.*)', line)
                m_done = re.search(r'- \[x\]\s*(.*)', line, re.IGNORECASE)
                if m_open:
                    current_data["todos"].append((False, m_open.group(1).strip()))
                elif m_done and current_key:
                    current_data["todos"].append((True, m_done.group(1).strip()))
        if current_key and current_data is not None:
            sections[current_key] = current_data
        return sections

    def _reload_projects(self):
        if orbit_config.get("obsidian_enabled", False):
            pf = orbit_config.project_file()
            try:
                mtime = pf.stat().st_mtime if pf else 0.0
            except Exception:
                mtime = 0.0
        else:
            try:
                mtime = orbit_config.PROJECTS_FILE.stat().st_mtime
            except Exception:
                mtime = 0.0
        if mtime != self._proj_mtime:
            self._proj_mtime = mtime
            self._proj_data = self._parse_projects()

    # ── Internal project CRUD ─────────────────────────────────────────

    def _proj_reload_show_list(self):
        self._proj_mtime = 0.0
        self._reload_projects()
        self._show_projects_list()

    def _proj_reload_show_detail(self, key):
        self._proj_mtime = 0.0
        self._reload_projects()
        self._show_project_detail(key)

    def _add_project(self):
        name = simpledialog.askstring("新增项目", "项目名称：", parent=self.root)
        if not name or not name.strip():
            return
        # Pick color
        import random
        color = _PROJ_COLORS[len(orbit_config.load_internal_projects()) % len(_PROJ_COLORS)]
        projects = orbit_config.load_internal_projects()
        projects.append({
            "id": uuid.uuid4().hex[:8],
            "name": name.strip(), "color": color,
            "now": "", "next": "", "bottleneck": "", "todos": [],
        })
        orbit_config.save_internal_projects(projects)
        self._proj_reload_show_list()

    def _add_todo(self, proj_id):
        text = simpledialog.askstring("新增待办", "待办内容：", parent=self.root)
        if not text or not text.strip():
            return
        projects = orbit_config.load_internal_projects()
        for p in projects:
            if p["id"] == proj_id:
                p.setdefault("todos", []).append({
                    "id": uuid.uuid4().hex[:8],
                    "text": text.strip(), "done": False,
                })
                break
        orbit_config.save_internal_projects(projects)
        self._proj_reload_show_detail(proj_id)

    def _toggle_todo_internal(self, proj_id, todo_text):
        projects = orbit_config.load_internal_projects()
        for p in projects:
            if p["id"] == proj_id:
                for t in p.get("todos", []):
                    if t["text"] == todo_text:
                        t["done"] = not t["done"]
                        break
                break
        orbit_config.save_internal_projects(projects)
        self._proj_reload_show_detail(proj_id)

    def _delete_todo_internal(self, proj_id, todo_text):
        projects = orbit_config.load_internal_projects()
        for p in projects:
            if p["id"] == proj_id:
                p["todos"] = [t for t in p.get("todos", []) if t["text"] != todo_text]
                break
        orbit_config.save_internal_projects(projects)
        self._proj_reload_show_detail(proj_id)

    def _edit_proj_field(self, proj_id, field, current):
        labels = {"now": "现在状态", "next": "下一步", "bottleneck": "瓶颈"}
        new_val = simpledialog.askstring(
            f"编辑 {labels.get(field, field)}", f"{labels.get(field, field)}：",
            initialvalue="" if current == "—" else current, parent=self.root)
        if new_val is None:
            return
        projects = orbit_config.load_internal_projects()
        for p in projects:
            if p["id"] == proj_id:
                p[field] = new_val.strip()
                break
        orbit_config.save_internal_projects(projects)
        self._proj_reload_show_detail(proj_id)

    def _schedule_todo(self, todo_text, proj_id):
        data = self._proj_data.get(proj_id, {})
        color = data.get("_color", "#5B8FD4")
        today = date.today()
        tomorrow = today + timedelta(days=1)
        hint = (f"输入日期（YYYY-MM-DD）\n"
                f"今天 {today.isoformat()}  明天 {tomorrow.isoformat()}")
        raw = simpledialog.askstring("排入哪天？", hint,
                                     initialvalue=tomorrow.isoformat(),
                                     parent=self.root)
        if not raw:
            return
        raw = raw.strip()
        if raw in ("今天", "t"):
            raw = today.isoformat()
        elif raw in ("明天", "tm"):
            raw = tomorrow.isoformat()
        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return
        _add_schedule_block(raw, todo_text, color)

    def _poll_projects(self):
        if "projects" not in self._page_inited:
            return
        old_data = self._proj_data
        self._reload_projects()
        if self._current_page == "projects" and self._proj_data != old_data:
            self._show_projects_list()
        self._projects_frame.after(30000, self._poll_projects)

    def _show_projects_list(self):
        # Clear any scroll routing
        if self._planner_app:
            self._planner_app._dialog_scroll_v = None
            self._planner_app._dialog_scroll_h = None
        self._proj_scroll_canvas = None
        self._proj_view = "list"
        for w in self._projects_frame.winfo_children():
            w.destroy()

        is_internal = not orbit_config.get("obsidian_enabled", False)

        # Header
        hdr = tk.Frame(self._projects_frame, bg=BG)
        hdr.pack(fill="x", padx=10, pady=(8, 6))
        tk.Label(hdr, text="项目进度", font=("Helvetica Neue", 12, "bold"),
                 fg=DEFAULT_TXT, bg=BG, anchor="w").pack(side="left")
        if is_internal:
            add_lbl = tk.Label(hdr, text="＋", font=("Helvetica Neue", 16),
                               fg="#5B8FD4", bg=BG, cursor="hand2")
            add_lbl.pack(side="right", padx=(0, 2))
            add_lbl.bind("<Button-1>", lambda e: self._add_project())
        elif self._proj_mtime:
            sync_str = datetime.fromtimestamp(self._proj_mtime).strftime("%H:%M")
            tk.Label(hdr, text=f"同步 {sync_str}", font=("Helvetica Neue", 8),
                     fg="#94A3B8", bg=BG).pack(side="right")

        # Empty state
        if not self._proj_data:
            if is_internal:
                msg = "点击 ＋ 新增第一个项目"
            else:
                pf = orbit_config.project_file()
                if pf is None:
                    msg = "请在设置中启用 Obsidian 集成"
                elif not pf.exists():
                    msg = "找不到 项目进度.md"
                else:
                    msg = "暂无项目数据"
            tk.Label(self._projects_frame, text=msg,
                     font=("Helvetica Neue", 11), fg="#94A3B8", bg=BG).pack(pady=40)
            return

        # Project cards
        if is_internal:
            for pid, data in self._proj_data.items():
                self._build_proj_card(self._projects_frame,
                                      data.get("_label", pid),
                                      data.get("_color", "#5B8FD4"),
                                      pid, data)
        else:
            for label, color, obs_key in PROJ_MAP:
                data = self._proj_data.get(obs_key, {})
                self._build_proj_card(self._projects_frame, label, color, obs_key, data)

    def _bind_recursive(self, widget, event, callback):
        widget.bind(event, callback)
        for child in widget.winfo_children():
            self._bind_recursive(child, event, callback)

    def _build_proj_card(self, parent, label, color, obs_key, data):
        todos = data.get("todos", [])
        done  = sum(1 for d, _ in todos if d)
        total = len(todos)
        pct   = done / total if total > 0 else 0
        now_text = data.get("now", "—")

        card = tk.Frame(parent, bg=BG, cursor="hand2")
        card.pack(fill="x", padx=8, pady=2)

        row = tk.Frame(card, bg=BG)
        row.pack(fill="x")
        tk.Frame(row, bg=color, width=4).pack(side="left", fill="y")
        inner = tk.Frame(row, bg=BG)
        inner.pack(side="left", fill="x", expand=True, padx=(6, 4))

        title_row = tk.Frame(inner, bg=BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text=label, font=("Helvetica Neue", 10, "bold"),
                 fg=DEFAULT_TXT, bg=BG, anchor="w").pack(side="left")
        tk.Label(title_row, text=f"{done}/{total}" if total > 0 else "—",
                 font=("Helvetica Neue", 9), fg="#94A3B8", bg=BG).pack(side="right")
        tk.Label(inner, text=now_text, font=("Helvetica Neue", 9),
                 fg="#64748B", bg=BG, anchor="w", wraplength=290, justify="left").pack(fill="x")

        # Progress bar — Canvas avoids pack_propagate geometry passes
        bar_cv = tk.Canvas(inner, height=5, bg="#E2E8F0", highlightthickness=0)
        bar_cv.pack(fill="x", pady=(2, 3))
        if pct > 0:
            bar_cv.bind("<Configure>", lambda e, p=pct, c=color:
                        (bar_cv.delete("fill"),
                         bar_cv.create_rectangle(0, 0, int(e.width * p), e.height,
                                                 fill=c, outline="")))

        tk.Frame(card, bg="#F1F5F9", height=1).pack(fill="x", pady=(2, 0))

        cb = lambda e, k=obs_key: self._show_project_detail(k)
        self._bind_recursive(card, "<Button-1>", cb)

    def _show_project_detail(self, key):
        is_internal = not orbit_config.get("obsidian_enabled", False)
        data = self._proj_data.get(key, {})
        if is_internal:
            label = data.get("_label", key)
            color = data.get("_color", "#5B8FD4")
        else:
            proj_info = next(((lbl, col) for lbl, col, k in PROJ_MAP if k == key), None)
            if proj_info is None:
                return
            label, color = proj_info

        self._proj_view = "detail"
        for w in self._projects_frame.winfo_children():
            w.destroy()

        # Header
        hdr = tk.Frame(self._projects_frame, bg=BG)
        hdr.pack(fill="x", padx=6, pady=(6, 2))
        back = tk.Label(hdr, text="← 返回", font=("Helvetica Neue", 10),
                        fg="#5B8FD4", bg=BG, cursor="hand2")
        back.pack(side="left")
        back.bind("<Button-1>", lambda e: self._show_projects_list())
        tk.Label(hdr, text=label, font=("Helvetica Neue", 11, "bold"),
                 fg=DEFAULT_TXT, bg=BG).pack(side="left", padx=(8, 0))
        if is_internal:
            add_lbl = tk.Label(hdr, text="＋ 待办", font=("Helvetica Neue", 9),
                               fg="#5B8FD4", bg=BG, cursor="hand2")
            add_lbl.pack(side="right")
            add_lbl.bind("<Button-1>", lambda e, k=key: self._add_todo(k))

        tk.Frame(self._projects_frame, bg=color, height=2).pack(fill="x", padx=8, pady=(2, 6))

        # 现在 / 下一步 / 瓶颈
        for lbl_text, fkey in [("📍 现在", "now"), ("⏭ 下一步", "next"), ("⚠ 瓶颈", "bottleneck")]:
            row = tk.Frame(self._projects_frame, bg=BG)
            row.pack(fill="x", padx=10, pady=1)
            tk.Label(row, text=f"{lbl_text}：", font=("Helvetica Neue", 9, "bold"),
                     fg="#64748B", bg=BG, anchor="nw", width=7).pack(side="left")
            val = data.get(fkey, "") or "—"
            val_lbl = tk.Label(row, text=val, font=("Helvetica Neue", 9),
                               fg=DEFAULT_TXT, bg=BG, anchor="nw",
                               wraplength=250, justify="left", cursor="hand2" if is_internal else "")
            val_lbl.pack(side="left", fill="x", expand=True)
            if is_internal:
                val_lbl.bind("<Button-1>",
                             lambda e, k=key, f=fkey, v=val: self._edit_proj_field(k, f, v))

        tk.Frame(self._projects_frame, bg="#E2E8F0", height=1).pack(fill="x", padx=8, pady=(6, 4))

        todos = data.get("todos", [])
        open_todos = [(d, t) for d, t in todos if not d]
        done_todos  = [(d, t) for d, t in todos if d]

        todo_hdr = tk.Frame(self._projects_frame, bg=BG)
        todo_hdr.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(todo_hdr,
                 text=f"待办  {len(open_todos)} 未完成 / {len(done_todos)} 已完成",
                 font=("Helvetica Neue", 9), fg="#94A3B8", bg=BG).pack(side="left")

        # Scrollable todo list
        cv = tk.Canvas(self._projects_frame, bg=BG, highlightthickness=0, yscrollincrement=3)
        cv.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._proj_scroll_canvas = cv

        inner = tk.Frame(cv, bg=BG)
        cv_win = cv.create_window(0, 0, anchor="nw", window=inner)
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfig(cv_win, width=e.width))

        def _make_todo_row(parent, text, done, proj_key, row_color, text_color):
            r = tk.Frame(parent, bg=BG, cursor="hand2" if is_internal else "")
            r.pack(fill="x", pady=1)
            marker = tk.Label(r, text="✓" if done else "○",
                              font=("Helvetica Neue", 9),
                              fg="#94A3B8" if done else row_color, bg=BG, width=2)
            marker.pack(side="left")
            lbl = tk.Label(r, text=text, font=("Helvetica Neue", 9),
                           fg=text_color, bg=BG, anchor="w",
                           wraplength=280, justify="left")
            lbl.pack(side="left", fill="x", expand=True)
            if is_internal:
                def _menu(event, t=text, k=proj_key, d=done):
                    m = tk.Menu(self.root, tearoff=0, bg=BG, fg=DEFAULT_TXT,
                                activebackground="#5B8FD4", activeforeground="white",
                                font=("Helvetica Neue", 11), relief="flat", bd=1)
                    m.add_command(label="📅  排入日程",
                                  command=lambda: self._schedule_todo(t, k))
                    m.add_command(
                        label="✓  标记完成" if not d else "↩  取消完成",
                        command=lambda: self._toggle_todo_internal(k, t))
                    m.add_separator()
                    m.add_command(label="🗑  删除",
                                  foreground="#EF4444",
                                  command=lambda: self._delete_todo_internal(k, t))
                    try:
                        m.tk_popup(event.x_root, event.y_root)
                    finally:
                        m.grab_release()
                for w in (r, marker, lbl):
                    w.bind("<Button-2>", _menu)
                    w.bind("<Button-3>", _menu)

        for _, text in open_todos:
            _make_todo_row(inner, text, False, key, color, DEFAULT_TXT)

        if done_todos:
            tk.Frame(inner, bg="#F1F5F9", height=1).pack(fill="x", pady=3)
            for _, text in done_todos:
                _make_todo_row(inner, text, True, key, color, "#94A3B8")

        # Scroll routing
        def _scroll_detail(dy):
            try:
                cv.yview_scroll(-1 if dy > 0 else 1, "units")
            except Exception:
                if self._planner_app:
                    self._planner_app._dialog_scroll_v = None

        if self._planner_app:
            self._planner_app._dialog_scroll_v = _scroll_detail
        cv.bind("<MouseWheel>", lambda e: _scroll_detail(e.delta))
        inner.bind("<MouseWheel>", lambda e: _scroll_detail(e.delta))

    # ─────────────────────────────────────────────────────────────────
    # 尺寸 & 位置
    # ─────────────────────────────────────────────────────────────────
    def _primary_screen(self):
        try:
            from AppKit import NSScreen
            for screen in NSScreen.screens():
                f = screen.frame()
                if abs(f.origin.x) < 1:
                    return int(f.size.width), int(f.size.height)
            f = NSScreen.mainScreen().frame()
            return int(f.size.width), int(f.size.height)
        except Exception:
            return self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _update_geometry(self, page=None):
        self.root.update_idletasks()
        sw, sh = self._primary_screen()
        if self._focus_mode:
            self.root.geometry(
                f"{FOCUS_SQ}x{FOCUS_SQ}+{sw - FOCUS_SQ - 14}+{sh - FOCUS_SQ - 60}")
        else:
            self.root.geometry(
                f"{TOTAL_W}x{WIN_H}+{sw - TOTAL_W - 8}+{sh - WIN_H - 60}")

    # ─────────────────────────────────────────────────────────────────
    # 拖拽
    # ─────────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f"+{x}+{y}")

    # ─────────────────────────────────────────────────────────────────
    # Section header
    # ─────────────────────────────────────────────────────────────────
    def _section_header(self, label: str, section: str, parent=None):
        if parent is None:
            parent = self._home_frame
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=10, pady=(3, 1))
        tk.Label(f, text=label, font=("Helvetica Neue", 9),
                 fg="#94A3B8", bg=BG, anchor="w").pack(side="left")
        tk.Button(f, text="−", font=("Helvetica Neue", 11, "bold"),
                  fg="#94A3B8", bg=BG, relief="flat", bd=0, cursor="hand2",
                  command=lambda s=section: self._del_item_dialog(s),
                  ).pack(side="right", padx=(2, 0))
        tk.Button(f, text="+", font=("Helvetica Neue", 11, "bold"),
                  fg="#94A3B8", bg=BG, relief="flat", bd=0, cursor="hand2",
                  command=lambda s=section: self._add_item_dialog(s),
                  ).pack(side="right")

    # ─────────────────────────────────────────────────────────────────
    # 任务编辑
    # ─────────────────────────────────────────────────────────────────
    def _add_item_dialog(self, section: str):
        names = {"work": "工作任务", "life": "生活任务", "checkin": "打卡项目"}
        answer = simpledialog.askstring(
            "新增", f"新增{names.get(section, '项目')}：", parent=self.root)
        if answer and answer.strip():
            send_command("add_item", section=section, item=answer.strip())

    def _del_item_dialog(self, section: str):
        items = {
            "work":    self._work_tasks,
            "life":    self._life_tasks,
            "checkin": self._checkin_items,
        }.get(section, [])
        if not items:
            return
        items_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
        answer = simpledialog.askstring(
            "删除", f"输入要删除的序号：\n\n{items_str}", parent=self.root)
        if answer and answer.strip().isdigit():
            idx = int(answer.strip()) - 1
            if 0 <= idx < len(items):
                send_command("del_item", section=section, item=items[idx])

    # ─────────────────────────────────────────────────────────────────
    # 任务按钮 & 打卡
    # ─────────────────────────────────────────────────────────────────
    def _rebuild_buttons(self, work_tasks, life_tasks):
        for c in self.work_frame.winfo_children():
            c.destroy()
        for c in self.life_frame.winfo_children():
            c.destroy()

        def make(parent, task, cols):
            fg = TASK_FG.get(task, "#FFFFFF")
            bg = TASK_BG.get(task, "#94A3B8")
            h  = 36 if cols == 2 else 32
            return RoundedButton(parent, text=task, bg_color=bg, fg_color=fg,
                                  command=lambda t=task: send_command("start", t),
                                  radius=12, btn_h=h)

        for i, t in enumerate(work_tasks):
            make(self.work_frame, t, 2).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=3, pady=2)
        if work_tasks:
            self.work_frame.columnconfigure(0, weight=1)
            self.work_frame.columnconfigure(1, weight=1)

        for i, t in enumerate(life_tasks):
            make(self.life_frame, t, 3).grid(
                row=i // 3, column=i % 3, sticky="ew", padx=3, pady=2)
        if life_tasks:
            self.life_frame.columnconfigure(0, weight=1)
            self.life_frame.columnconfigure(1, weight=1)
            self.life_frame.columnconfigure(2, weight=1)

    def _rebuild_checkin(self, items, done):
        for c in self.checkin_frame.winfo_children():
            c.destroy()
        if not items:
            self._update_geometry()
            return
        for i, item in enumerate(items):
            checked = item in done
            bg = CHECKIN_DONE_BG if checked else CHECKIN_UNDONE_BG
            fg = CHECKIN_DONE_FG if checked else CHECKIN_UNDONE_FG
            RoundedButton(self.checkin_frame,
                          text=("✓ " if checked else "○ ") + item,
                          bg_color=bg, fg_color=fg,
                          command=lambda t=item: self._toggle_checkin(t),
                          radius=10, btn_h=28,
                          ).grid(row=i // 2, column=i % 2, sticky="ew",
                                 padx=3, pady=2)
        self.checkin_frame.columnconfigure(0, weight=1)
        self.checkin_frame.columnconfigure(1, weight=1)
        self._update_geometry()

    def _toggle_checkin(self, item: str):
        if item in self._checkin_done:
            self._checkin_done = [x for x in self._checkin_done if x != item]
        else:
            self._checkin_done = self._checkin_done + [item]
        self._rebuild_checkin(self._checkin_items, self._checkin_done)
        send_command("checkin_toggle", item=item)

    # ─────────────────────────────────────────────────────────────────
    # 定时刷新
    # ─────────────────────────────────────────────────────────────────
    def _tick(self):
        try:
            if STATE_FILE.exists():
                s             = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                task          = s.get("current_task")
                start_str     = s.get("task_start")
                work_tasks    = s.get("work_tasks", [])
                life_tasks    = s.get("life_tasks", [])
                checkin_items = s.get("checkin_items", [])
                checkin_done  = s.get("checkin_done", [])
                checkin_date  = s.get("checkin_date", "")

                if work_tasks != self._work_tasks or life_tasks != self._life_tasks:
                    self._work_tasks = work_tasks
                    self._life_tasks = life_tasks
                    self._rebuild_buttons(work_tasks, life_tasks)

                if (checkin_items != self._checkin_items
                        or checkin_done != self._checkin_done
                        or checkin_date != self._checkin_date):
                    self._checkin_items = checkin_items
                    self._checkin_done  = checkin_done
                    self._checkin_date  = checkin_date
                    self._rebuild_checkin(checkin_items, checkin_done)

                if task and start_str:
                    elapsed = (datetime.now() - datetime.fromisoformat(start_str)).total_seconds()
                    accent  = TASK_BG.get(task, "#94A3B8")
                    # 普通界面标签更新
                    self.task_var.set(task)
                    self.time_var.set(fmt(elapsed))
                    self.task_lbl.config(fg=accent)
                    self.time_lbl.config(fg=accent)
                    # 工作类任务且在 home 页 → 专注正方形
                    if task in work_tasks and self._current_page == "home":
                        self._enter_focus_mode()
                        self._sq_update(task, elapsed, accent)
                    else:
                        self._exit_focus_mode()
                else:
                    self.task_var.set("⚡ 未开始")
                    self.time_var.set("—")
                    self.task_lbl.config(fg=DEFAULT_TXT)
                    self.time_lbl.config(fg=DEFAULT_FG)
                    self._exit_focus_mode()
        except Exception:
            pass
        self.root.after(1000, self._tick)

    # ─────────────────────────────────────────────────────────────────
    # AppKit
    # ─────────────────────────────────────────────────────────────────
    def _setup_nswindow(self):
        try:
            from AppKit import NSApp
            for win in NSApp.windows():
                win.setTitlebarAppearsTransparent_(True)
                win.setTitleVisibility_(1)
                win.setCollectionBehavior_(1 | 16 | 64 | 2048)
        except Exception:
            pass


if __name__ == "__main__":
    FocusApp()
