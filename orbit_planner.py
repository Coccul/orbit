#!/usr/bin/env python3
"""
Orbit Planner — 多日日程视图
· 左右翻页浏览历史/未来日期
· 拖拽建块，右键删除
· 左键点击已有块：勾选完成（再次点击取消，提示填写未完成原因）
· 顶部待办栏一键放入当前时间
"""

import tkinter as tk
from tkinter import simpledialog
import json
import re
import uuid
import threading
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

APP_SUPPORT    = Path.home() / "Library" / "Application Support" / "Orbit"
SCHEDULES_FILE = APP_SUPPORT / "schedules.json"
TODO_FILE      = APP_SUPPORT / "daily_todos.json"
PENDING_FILE   = APP_SUPPORT / "pending.json"
STATE_FILE     = APP_SUPPORT / "state.json"

_HERE = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
         else Path(__file__).parent)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import orbit_config

# 工作项目：(显示名, 颜色, Obsidian 章节关键词)
_obs_projects       = orbit_config.load_obsidian_projects()
WORK_PROJECTS_MAP   = [(p[0], p[1], p[2]) for p in _obs_projects]
# 弹窗按钮简称：取配置第4元素，若无则使用 obs_key 本身
WORK_PROJECTS_SHORT = {p[2]: (p[3] if len(p) > 3 else p[2]) for p in _obs_projects}

# ── 布局常量 ────────────────────────────────────────────────────────
W          = 360
TIME_COL_W = 44
CONTENT_X1 = TIME_COL_W + 2
CONTENT_X2 = W - 4
HOUR_H     = 64
HALF_H     = HOUR_H // 2
START_HOUR = 6
END_HOUR   = 24
CANVAS_H   = (END_HOUR - START_HOUR) * HOUR_H

BG           = "#FFFFFF"
GRID_ALT     = "#FAFBFC"
GRID_HOUR    = "#E2E8F0"
GRID_HALF    = "#F1F5F9"
TIME_FG      = "#94A3B8"
NOW_COLOR    = "#EF4444"
DEFAULT_COL  = "#5B8FD4"

TASK_BG = orbit_config.load_task_colors()


def y_to_min(y: float) -> int:
    return START_HOUR * 60 + int(y / HOUR_H * 60)


def min_to_y(m: int) -> float:
    return (m - START_HOUR * 60) / 60 * HOUR_H


def snap15(m: int) -> int:
    return round(m / 15) * 15


def color_for(text: str) -> str:
    tl = text.lower()
    for key, col in TASK_BG.items():
        k = key.lower().strip()
        if k in tl or tl in k:
            return col
    return DEFAULT_COL


class FocusPlanner:
    def __init__(self, parent=None, win=None):
        self._embedded = parent is not None
        if self._embedded:
            # 嵌入模式：root 是外部 Frame，_win 是真实 Tk 窗口
            self.root = parent
            self._win  = win
        else:
            self.root = tk.Tk()
            self._win  = self.root
            self.root.title("📅 日程规划")
            try:
                from AppKit import NSApp, NSApplicationActivationPolicyRegular
                NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            self.root.attributes("-topmost", True)
            self.root.configure(bg=BG)
            self.root.resizable(False, False)
            try:
                from AppKit import NSApp
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass

        self._view_date  = date.today()
        self._all        = {}   # { "YYYY-MM-DD": [block, ...] }
        self._drag_y0    = None
        self._preview    = None
        self._move_bid      = None   # 正在移动的块 id
        self._move_offset_y = 0      # 鼠标点击位置距块顶部的偏移
        self._move_duration = 0      # 被移动块的时长（分钟）
        self._resize_bid    = None   # 正在缩放的块 id

        self._reminded_ids   = set()   # 已提醒过的 block id
        self._reminder_stop  = threading.Event()

        self._load_all()
        self._build()
        self._refresh()
        self._win.after(400, self._maybe_show_compression)
        self._start_reminder_thread()
        if not self._embedded:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
            self.root.mainloop()

    def _on_close(self):
        self._reminder_stop.set()
        try:
            import AppKit
            if self._scroll_monitor:
                AppKit.NSEvent.removeMonitor_(self._scroll_monitor)
        except Exception:
            pass
        if not self._embedded:
            self._win.destroy()

    # ── 数据 ─────────────────────────────────────────────────────────
    def _load_all(self):
        try:
            if SCHEDULES_FILE.exists():
                self._all = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            self._all = {}

    def _save(self):
        try:
            APP_SUPPORT.mkdir(parents=True, exist_ok=True)
            SCHEDULES_FILE.write_text(
                json.dumps(self._all, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def _date_key(self, d: date = None) -> str:
        return (d or self._view_date).isoformat()

    def _blocks(self, d: date = None) -> list:
        return self._all.setdefault(self._date_key(d), {}).setdefault("blocks", [])

    # ── 每日重复块 ────────────────────────────────────────────────────
    _DEFAULT_RECURRING = [
        {"text": "记账", "start_min": 1290, "end_min": 1305, "color": "#DDB86A"},
    ]

    def _inject_recurring(self):
        """首次查看某天时，自动插入每日重复块（跳过已存在同名块）。"""
        key = self._date_key()
        injected = self._all.setdefault("_recurring_injected", [])
        if key in injected:
            return
        recurring = self._all.get("_recurring", self._DEFAULT_RECURRING)
        blocks = self._blocks()
        existing_texts = {b["text"] for b in blocks}
        added = False
        for tmpl in recurring:
            if tmpl["text"] in existing_texts:
                continue
            blocks.append({
                "id": uuid.uuid4().hex[:8],
                "start_min": tmpl["start_min"],
                "end_min":   tmpl["end_min"],
                "text":      tmpl["text"],
                "color":     tmpl["color"],
                "done":      False,
                "skip_reason": "",
            })
            added = True
        injected.append(key)
        if added:
            self._save()

    # ── Apple Reminders 同步 ──────────────────────────────────────────

    @staticmethod
    def _reminders_run(script: str) -> str:
        """执行 AppleScript，返回 stdout，失败静默。"""
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=8)
            return r.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _reminders_due_expr(start_min: int, target_date: date) -> str:
        """生成指定日期 + 时间的 AppleScript date 表达式。"""
        h, m = start_min // 60, start_min % 60
        ds = f"{target_date.month}/{target_date.day}/{target_date.year} {h:02d}:{m:02d}:00"
        return f'(date "{ds}")'

    def _reminders_create(self, todo: dict, start_min: int | None = None,
                          target_date: date | None = None) -> str:
        """在 Orbit 列表里新建提醒（幂等：同名未完成提醒存在则复用并更新时间）。"""
        td = target_date or date.today()
        text = todo["text"].replace('"', '\\"')
        if start_min is not None:
            due_expr = self._reminders_due_expr(start_min, td)
        else:
            due_expr = f'(date "{td.month}/{td.day}/{td.year} 23:00:00")'
        script = f'''
tell application "Reminders"
    if not (exists list "Orbit") then
        make new list with properties {{name:"Orbit"}}
    end if
    set theList to list "Orbit"
    set matches to (reminders of theList whose name is "{text}" and completed is false)
    if (count of matches) > 0 then
        set theR to item 1 of matches
        set due date of theR to {due_expr}
        set remind me date of theR to {due_expr}
        return id of theR
    end if
    set newR to make new reminder at theList with properties {{name:"{text}", due date:{due_expr}, remind me date:{due_expr}}}
    return id of newR
end tell'''
        return self._reminders_run(script)

    def _reminders_dedup(self):
        """只删除 Orbit 列表里多余的重复提醒，保留每个名字最早一条。"""
        def _run():
            # 1. 用 Python 处理去重逻辑（AppleScript contains 遇 emoji 会失败）
            raw = self._reminders_run('''
tell application "Reminders"
    if not (exists list "Orbit") then return ""
    set theList to list "Orbit"
    set allR to reminders of theList whose completed is false
    set out to ""
    repeat with r in allR
        set out to out & (id of r) & "|" & (name of r) & linefeed
    end repeat
    return out
end tell''')
            if not raw.strip():
                return
            seen, to_delete = set(), []
            for line in raw.strip().splitlines():
                if "|" not in line:
                    continue
                rid, name = line.split("|", 1)
                if name in seen:
                    to_delete.append(rid.strip())
                else:
                    seen.add(name)
            # 2. 逐个删除多余的
            for rid in to_delete:
                rid_esc = rid.replace('"', '\\"')
                self._reminders_run(f'tell application "Reminders" to delete (reminder id "{rid_esc}")')
            n = len(to_delete)
            self._reminders_run(
                f'display notification "已删除 {n} 条重复提醒" with title "周天 ✅" sound name "Glass"')
        threading.Thread(target=_run, daemon=True).start()

    def _reminders_update_time(self, reminder_id: str, start_min: int,
                               target_date: date | None = None):
        """更新提醒的 due date。"""
        if not reminder_id:
            return
        rid = reminder_id.replace('"', '\\"')
        due_expr = self._reminders_due_expr(start_min, target_date or date.today())
        script = f'''
tell application "Reminders"
    set theR to reminder id "{rid}"
    set due date of theR to {due_expr}
    set remind me date of theR to {due_expr}
end tell'''
        self._reminders_run(script)

    def _reminders_complete(self, reminder_id: str):
        """将 Apple Reminders 里的提醒标记为已完成。"""
        if not reminder_id:
            return
        rid = reminder_id.replace('"', '\\"')
        script = f'''
tell application "Reminders"
    set theR to reminder id "{rid}"
    set completed of theR to true
end tell'''
        self._reminders_run(script)

    def _sync_all_to_reminders(self):
        """把所有日期里没有 reminder_id 的未完成时间块都推到 Apple Reminders。"""
        def _run():
            changed = False
            today = date.today()
            for key, day_data in self._all.items():
                # 只同步今天及未来
                try:
                    target = date.fromisoformat(key)
                except ValueError:
                    continue
                if target < today:
                    continue
                # 时间块
                for b in day_data.get("blocks", []):
                    if b.get("done") or b.get("reminder_id"):
                        continue
                    rid = self._reminders_create(
                        {"text": b["text"]}, b["start_min"], target)
                    if rid:
                        b["reminder_id"] = rid
                        changed = True
                # 待安排（只处理今天的，未来日期的 todos 通常还没创建）
                if target == today:
                    for t in day_data.get("todos", []):
                        if isinstance(t, dict) and not t.get("reminder_id"):
                            rid = self._reminders_create(t, None, target)
                            if rid:
                                t["reminder_id"] = rid
                                changed = True
            if changed:
                self.root.after(0, self._save)
            total = sum(
                1 for d in self._all.values()
                for b in d.get("blocks", [])
                if b.get("reminder_id")
            )
            self._reminders_run(
                f'display notification "已同步 {total} 条提醒到提醒事项" with title "周天 ✅" sound name "Glass"')
        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def _norm_todo(t) -> dict:
        """将旧格式字符串 todo 统一为 dict。"""
        if isinstance(t, str):
            return {"text": t, "color": color_for(t), "duration": 60, "energy": ""}
        return dict(t)

    def _todos(self, d: date = None) -> list:
        """返回当前日期的待办列表（dict 列表）。今天额外合并 daily_todos.json。"""
        target = d or self._view_date
        day = self._all.setdefault(self._date_key(d), {})
        todos = [self._norm_todo(t) for t in day.setdefault("todos", [])]
        if target == date.today():
            try:
                if TODO_FILE.exists():
                    td = json.loads(TODO_FILE.read_text(encoding="utf-8"))
                    if td.get("date") == date.today().isoformat():
                        existing = {t["text"] for t in todos}
                        dismissed = set(day.get("dismissed_todos", []))
                        for t in td.get("todos", []):
                            if not t.get("done") and t["text"] not in existing and t["text"] not in dismissed:
                                todos.append(self._norm_todo(t["text"]))
            except Exception:
                pass
        return todos

    def _add_todo(self):
        win = tk.Toplevel(self.root)
        win.title("新建待安排")
        win.configure(bg="#FFFFFF")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        win.geometry(f"+{rx+20}+{ry+40}")
        win.after(10, win.lift)
        win.after(20, win.focus_force)

        PALETTE = [
            "#E07068", "#9880CC", "#E8956A", "#5B8FD4", "#4AABB0", "#72B87A",
            "#DDB86A", "#68B868", "#E8A0BF", "#7090C8", "#8B5A38", "#94A3B8",
        ]
        color_var  = tk.StringVar(value="#5B8FD4")
        dur_var    = tk.IntVar(value=60)
        energy_var = tk.StringVar(value="高能耗")

        # ── 事项名称 ──────────────────────────────────────────────────
        tk.Label(win, text="事项", font=("Helvetica Neue", 10, "bold"),
                 bg="#FFFFFF", fg="#334155").pack(anchor="w", padx=16, pady=(14, 2))
        name_var = tk.StringVar()
        entry = tk.Entry(win, textvariable=name_var, font=("Helvetica Neue", 12),
                         relief="flat", bg="#F8FAFC", fg="#1E293B",
                         insertbackground="#5B8FD4", width=22)
        entry.pack(padx=16, pady=(0, 10), fill="x")
        entry.focus_set()

        # ── 颜色（用 Canvas 色块，macOS Button 不渲染 bg）────────────
        tk.Label(win, text="颜色", font=("Helvetica Neue", 10, "bold"),
                 bg="#FFFFFF", fg="#334155").pack(anchor="w", padx=16, pady=(0, 4))
        color_canvas = tk.Canvas(win, bg="#FFFFFF", highlightthickness=0,
                                 width=len(PALETTE)//2 * 26, height=52)
        color_canvas.pack(padx=16, anchor="w", pady=(0, 10))

        SWATCH_W, SWATCH_H = 22, 22
        swatch_ids = {}   # color -> rect_id

        def _draw_swatches(selected):
            color_canvas.delete("all")
            swatch_ids.clear()
            for i, c in enumerate(PALETTE):
                col = i % 6
                row = i // 6
                x1 = col * 26 + 1
                y1 = row * 26 + 1
                x2 = x1 + SWATCH_W
                y2 = y1 + SWATCH_H
                outline = "#1E293B" if c == selected else c
                width   = 3 if c == selected else 1
                rid = color_canvas.create_rectangle(x1, y1, x2, y2,
                                                    fill=c, outline=outline,
                                                    width=width)
                swatch_ids[c] = rid
                color_canvas.tag_bind(rid, "<Button-1>", lambda e, cv=c: _pick_color(cv, manual=True))

        user_picked = [False]

        def _pick_color(c, manual=False):
            if manual:
                user_picked[0] = True
            color_var.set(c)
            _draw_swatches(c)

        def _auto_color(*_):
            if user_picked[0]:
                return
            txt = name_var.get().strip()
            if txt:
                _pick_color(color_for(txt))

        name_var.trace_add("write", _auto_color)
        _draw_swatches(color_var.get())

        # ── 时长 ─────────────────────────────────────────────────────
        tk.Label(win, text="时长", font=("Helvetica Neue", 10, "bold"),
                 bg="#FFFFFF", fg="#334155").pack(anchor="w", padx=16, pady=(0, 4))
        dur_row = tk.Frame(win, bg="#FFFFFF")
        dur_row.pack(padx=16, pady=(0, 10), anchor="w")
        dur_btn_map = {}

        def _pick_dur(d):
            dur_var.set(d)
            for dv, b in dur_btn_map.items():
                if dv == d:
                    b.configure(bg="#E0EAFF", fg="#2563EB",
                                font=("Helvetica Neue", 9, "bold"))
                else:
                    b.configure(bg="#F1F5F9", fg="#64748B",
                                font=("Helvetica Neue", 9))

        for i, d in enumerate([15, 30, 45, 60, 90, 120]):
            lbl = f"{d}m" if d < 60 else (f"{d//60}h" if d % 60 == 0 else f"{d//60}h{d%60}m")
            is_sel = (d == 60)
            b = tk.Label(dur_row, text=lbl, font=("Helvetica Neue", 9, "bold" if is_sel else ""),
                         bg="#E0EAFF" if is_sel else "#F1F5F9",
                         fg="#2563EB" if is_sel else "#64748B",
                         padx=8, pady=5, cursor="hand2")
            b.grid(row=0, column=i, padx=(0, 4))
            b.bind("<Button-1>", lambda e, dv=d: _pick_dur(dv))
            dur_btn_map[d] = b

        # ── 能耗 ─────────────────────────────────────────────────────
        tk.Label(win, text="能耗", font=("Helvetica Neue", 10, "bold"),
                 bg="#FFFFFF", fg="#334155").pack(anchor="w", padx=16, pady=(0, 4))
        energy_row = tk.Frame(win, bg="#FFFFFF")
        energy_row.pack(padx=16, pady=(0, 14), anchor="w")

        # (label, value, active_bg, active_fg, idle_bg, idle_fg)
        ENERGY_OPTS = [
            ("⚡ 高能耗", "高能耗", "#FEE2E2", "#B91C1C"),
            ("🍃 低能耗", "低能耗", "#DCFCE7", "#15803D"),
            ("⏱ 碎片",  "碎片时间", "#F1F5F9", "#475569"),
        ]
        energy_btn_map = {}

        def _pick_energy(val):
            energy_var.set(val)
            for v, b in energy_btn_map.items():
                cfg = next(o for o in ENERGY_OPTS if o[1] == v)
                if v == val:
                    b.configure(bg=cfg[2], fg=cfg[3],
                                font=("Helvetica Neue", 9, "bold"))
                else:
                    b.configure(bg="#F8FAFC", fg="#94A3B8",
                                font=("Helvetica Neue", 9))

        for lbl, val, abg, afg in ENERGY_OPTS:
            is_sel = (val == "高能耗")
            b = tk.Label(energy_row, text=lbl, font=("Helvetica Neue", 9, "bold" if is_sel else ""),
                         bg=abg if is_sel else "#F8FAFC",
                         fg=afg if is_sel else "#94A3B8",
                         padx=10, pady=5, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            b.bind("<Button-1>", lambda e, v=val: _pick_energy(v))
            energy_btn_map[val] = b

        # ── 保存 ─────────────────────────────────────────────────────
        def _commit():
            text = name_var.get().strip()
            if not text:
                return
            todo = {
                "text":     text,
                "color":    color_var.get(),
                "duration": dur_var.get(),
                "energy":   energy_var.get(),
            }
            day = self._all.setdefault(self._date_key(), {})
            lst = day.setdefault("todos", [])
            existing = [(t if isinstance(t, str) else t.get("text")) for t in lst]
            if text not in existing:
                # 同步到 Apple Reminders（后台线程，不阻塞 UI）
                def _sync_create(t=todo):
                    rid = self._reminders_create(t)
                    if rid:
                        t["reminder_id"] = rid
                        self._save()
                threading.Thread(target=_sync_create, daemon=True).start()
                lst.append(todo)
                self._save()
                self._build_todo_strip()
            win.destroy()

        entry.bind("<Return>", lambda e: _commit())
        tk.Button(win, text="  添加  ", command=_commit,
                  bg="#5B8FD4", fg="#FFFFFF", relief="flat",
                  font=("Helvetica Neue", 11, "bold"), padx=10, pady=7,
                  cursor="hand2").pack(pady=(0, 14))

    def _remove_todo(self, text: str):
        day = self._all.get(self._date_key(), {})
        lst = day.get("todos", [])
        new_lst = [t for t in lst if (t if isinstance(t, str) else t.get("text")) != text]
        if len(new_lst) < len(lst):
            day["todos"] = new_lst
            self._save()
            self._build_todo_strip()

    # ── 界面构建 ─────────────────────────────────────────────────────
    def _build(self):
        # ── 顶部导航栏 ──
        nav = tk.Frame(self.root, bg=BG)
        nav.pack(fill="x", padx=12, pady=(10, 2))

        tk.Button(nav, text="←", font=("Helvetica Neue", 13),
                  fg="#64748B", bg=BG, relief="flat", bd=0,
                  command=self._prev_day).pack(side="left")

        self._date_lbl = tk.Label(nav, text="",
                                  font=("Helvetica Neue", 13, "bold"),
                                  fg="#334155", bg=BG)
        self._date_lbl.pack(side="left", expand=True)

        tk.Button(nav, text="→", font=("Helvetica Neue", 13),
                  fg="#64748B", bg=BG, relief="flat", bd=0,
                  command=self._next_day).pack(side="right")

        _b = dict(font=("Helvetica Neue", 12), bg=BG, relief="flat",
                  bd=0, padx=6, pady=2, cursor="hand2")
        tk.Button(nav, text="复盘", fg="#5B8FD4",
                  command=self._show_review,   **_b).pack(side="right", padx=2)
        tk.Button(nav, text="🔄",   fg="#94A3B8",
                  command=self._sync_all_to_reminders, **_b).pack(side="right", padx=2)
        tk.Button(nav, text="🗑",   fg="#94A3B8",
                  command=self._reminders_dedup, **_b).pack(side="right", padx=2)

        tk.Frame(self.root, bg="#E2E8F0", height=1).pack(fill="x", padx=8)

        # ── 晨间压缩提示（按需显示，每天一次） ──
        self._compression_frame = tk.Frame(self.root, bg="#FFF7ED")
        # NOT packed here — shown conditionally by _maybe_show_compression()

        inner_c = tk.Frame(self._compression_frame, bg="#FFF7ED")
        inner_c.pack(fill="x", padx=10, pady=6)

        self._compression_msg = tk.Label(
            inner_c, text="", font=("Helvetica Neue", 10, "bold"),
            fg="#7C2D12", bg="#FFF7ED", anchor="w", justify="left")
        self._compression_msg.pack(side="left", fill="x", expand=True)

        self._compression_var = tk.StringVar(value="3")
        spin = tk.Spinbox(
            inner_c, from_=1, to=20, width=3,
            textvariable=self._compression_var,
            font=("Helvetica Neue", 11, "bold"),
            fg="#92400E", bg="#FEF3C7",
            relief="flat", bd=1, justify="center",
            buttonbackground="#FEF3C7")
        spin.pack(side="left", padx=(6, 4))

        tk.Label(inner_c, text="块", font=("Helvetica Neue", 10),
                 fg="#92400E", bg="#FFF7ED").pack(side="left", padx=(0, 8))

        tk.Button(inner_c, text="确定",
                  font=("Helvetica Neue", 10, "bold"),
                  fg="#FFFFFF", bg="#F97316",
                  relief="flat", bd=0, padx=8, pady=2,
                  cursor="hand2",
                  command=self._confirm_compression).pack(side="left")

        tk.Frame(self._compression_frame, bg="#FED7AA", height=1).pack(
            fill="x")

        # ── 待办栏容器（动态重建） ──
        self._todo_container = tk.Frame(self.root, bg=BG)
        self._todo_container.pack(fill="x")
        self._pills_canvas = None   # 横向 pills canvas 引用，由 _build_todo_strip 更新

        # ── Canvas + 滚动条 ──
        cf = tk.Frame(self.root, bg=BG)
        cf.pack(fill="both", expand=True, padx=8, pady=6)

        self.canvas = tk.Canvas(
            cf, width=W, height=420,
            bg=BG, highlightthickness=0,
            scrollregion=(0, 0, W, CANVAS_H),
        )
        sb = tk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.canvas.bind("<Button-1>",        self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-2>",        self._on_right)
        self.canvas.bind("<Button-3>",        self._on_right)

        self.canvas.bind("<MouseWheel>", self._on_vscroll)
        self.root.bind_all("<MouseWheel>", self._on_vscroll)

        if not self._embedded:
            self._win.geometry(f"{W + 24}x580")

        self._active_col_canvas = None   # 鼠标悬停的待安排列 canvas

        # NSEvent 本地监听器：拦截 trackpad scroll
        self._scroll_monitor = None
        try:
            import AppKit
            def _ns_scroll(ns_event):
                dy = ns_event.scrollingDeltaY()
                dx = ns_event.scrollingDeltaX()
                sv = getattr(self, '_dialog_scroll_v', None)
                sh = getattr(self, '_dialog_scroll_h', None)
                if sv and abs(dy) > 0.5:
                    self.canvas.after(0, lambda d=dy: sv(d))
                elif sh and abs(dx) > 0.5:
                    self.canvas.after(0, lambda d=dx: sh(d))
                elif abs(dy) > 0.5:
                    col_cv = self._active_col_canvas
                    if col_cv:
                        self.canvas.after(0, lambda: col_cv.yview_scroll(
                            -1 if dy > 0 else 1, "units"))
                    else:
                        self.canvas.after(0, lambda: self.canvas.yview_scroll(
                            -1 if dy > 0 else 1, "units"))
                return ns_event
            self._scroll_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskScrollWheel, _ns_scroll)
        except Exception:
            pass

    def _on_vscroll(self, event):
        if getattr(self, '_dialog_scroll_v', None):
            return
        d = event.delta
        if d == 0:
            return
        self.canvas.yview_scroll(-1 if d > 0 else 1, "units")

    def _build_todo_strip(self):
        for w in self._todo_container.winfo_children():
            w.destroy()
        self._pills_canvas = None
        self._active_col_canvas = None

        todos = self._todos()

        # ── 顶部标题行（始终显示） ───────────────────────────────────
        hdr = tk.Frame(self._todo_container, bg="#F8FAFC")
        hdr.pack(fill="x", padx=8, pady=(4, 0))

        toggle_lbl = tk.Label(hdr, text="▼ 待安排",
                              font=("Helvetica Neue", 8, "bold"),
                              fg="#64748B", bg="#F8FAFC", cursor="hand2")
        toggle_lbl.pack(side="left", padx=(6, 4))

        tk.Button(hdr, text="+", font=("Helvetica Neue", 11, "bold"),
                  fg="#5B8FD4", bg="#F8FAFC", relief="flat", bd=0,
                  cursor="hand2", command=self._add_todo).pack(side="right", padx=(0, 6))

        # ── 三列并排（可折叠） ───────────────────────────────────────
        body = tk.Frame(self._todo_container, bg="#F8FAFC")

        def _toggle(event=None):
            if body.winfo_ismapped():
                body.pack_forget()
                toggle_lbl.config(text="▶ 待安排")
            else:
                body.pack(fill="x", padx=6, pady=(2, 4))
                toggle_lbl.config(text="▼ 待安排")

        toggle_lbl.bind("<Button-1>", _toggle)

        GROUPS = [
            ("⚡ 高能耗", "高能耗",  "#FEE2E2", "#B91C1C"),
            ("🍃 低能耗", "低能耗",  "#DCFCE7", "#15803D"),
            ("⏱ 碎片",  "碎片时间", "#F1F5F9", "#475569"),
        ]
        grouped: dict = {"高能耗": [], "低能耗": [], "碎片时间": []}
        for td in todos:
            eng = td.get("energy", "")
            grouped[eng if eng in grouped else "碎片时间"].append(td)

        cols_frame = tk.Frame(body, bg="#F8FAFC")
        cols_frame.pack(fill="x", pady=(2, 2))

        COL_H = 110   # 列内容区高度（px）

        for i, (grp_lbl, key, hdr_bg, hdr_fg) in enumerate(GROUPS):
            # 列外框
            col_outer = tk.Frame(cols_frame, bg="#EBEBEB", bd=0)
            col_outer.grid(row=0, column=i, padx=(0 if i == 0 else 3, 0), sticky="nsew")
            cols_frame.columnconfigure(i, weight=1)

            # 列标题
            tk.Label(col_outer, text=grp_lbl, font=("Helvetica Neue", 8, "bold"),
                     fg=hdr_fg, bg=hdr_bg, pady=4, anchor="center"
                     ).pack(fill="x")

            # 列内容 canvas（纵向滚动）
            cv = tk.Canvas(col_outer, bg="#FFFFFF", height=COL_H,
                           highlightthickness=0, bd=0)
            cv.pack(fill="x")
            inner = tk.Frame(cv, bg="#FFFFFF")
            cv.create_window(0, 0, window=inner, anchor="nw")

            group_todos = grouped[key]
            for todo in group_todos:
                txt = todo["text"]
                col_color = todo.get("color") or color_for(txt)
                dur = todo.get("duration", 60)
                dur_str = (f"{dur//60}h" if dur % 60 == 0 else f"{dur}m") if dur != 60 else ""

                row_f = tk.Frame(inner, bg="#FFFFFF")
                row_f.pack(fill="x", pady=1, padx=2)

                # 左侧彩色条
                tk.Frame(row_f, bg=col_color, width=4).pack(side="left", fill="y")

                lbl_text = txt if not dur_str else f"{txt}  {dur_str}"
                item = tk.Label(row_f, text=lbl_text,
                                font=("Helvetica Neue", 9), fg="#1E293B", bg="#FFFFFF",
                                anchor="w", padx=5, pady=4, cursor="hand2",
                                wraplength=90, justify="left")
                item.pack(side="left", fill="x", expand=True)
                item.bind("<Button-1>", lambda e, td=todo: self._pick_time_for_todo(td))
                item.bind("<Button-3>", lambda e, t=txt: self._remove_todo(t))
                row_f.bind("<Button-1>", lambda e, td=todo: self._pick_time_for_todo(td))
                row_f.bind("<Button-3>", lambda e, t=txt: self._remove_todo(t))

            if not group_todos:
                tk.Label(inner, text="空", font=("Helvetica Neue", 9),
                         fg="#CBD5E1", bg="#FFFFFF", pady=8).pack()

            def _update_sr(event=None, c=cv, f=inner):
                c.configure(scrollregion=c.bbox("all") or (0, 0, 0, 0))
            inner.bind("<Configure>", _update_sr)

            def _vscroll(event, c=cv):
                c.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"
            cv.bind("<MouseWheel>", _vscroll)
            inner.bind("<MouseWheel>", _vscroll)
            for child in inner.winfo_children():
                child.bind("<MouseWheel>", _vscroll)

            # 悬停时 NSEvent 纵向滚动目标锁定为本列
            cv.bind("<Enter>", lambda e, c=cv: setattr(self, "_active_col_canvas", c))
            cv.bind("<Leave>", lambda e: setattr(self, "_active_col_canvas", None))

        if todos:
            body.pack(fill="x", padx=6, pady=(2, 4))

    # ── 刷新 ─────────────────────────────────────────────────────────
    def _refresh(self):
        # 更新日期标签
        today = date.today()
        if self._view_date == today:
            label = f"今天  {self._view_date.strftime('%-m月%-d日')}"
        elif self._view_date == today - timedelta(days=1):
            label = f"昨天  {self._view_date.strftime('%-m月%-d日')}"
        elif self._view_date == today + timedelta(days=1):
            label = f"明天  {self._view_date.strftime('%-m月%-d日')}"
        else:
            label = self._view_date.strftime("%-m月%-d日")
        self._date_lbl.config(text=label)

        self._inject_recurring()
        self._build_todo_strip()
        self._draw_all()
        if self._view_date == today:
            self._scroll_to_now()
            self._tick()

    def _prev_day(self):
        self._view_date -= timedelta(days=1)
        self._refresh()

    def _next_day(self):
        self._view_date += timedelta(days=1)
        self._refresh()

    # ── 绘制 ─────────────────────────────────────────────────────────
    def _draw_all(self):
        self.canvas.delete("all")
        self._draw_grid()
        self._draw_blocks()
        if self._view_date == date.today():
            self._draw_now_line()

    def _draw_grid(self):
        for h in range(START_HOUR, END_HOUR + 1):
            y = (h - START_HOUR) * HOUR_H
            if h < END_HOUR and h % 2 == 0:
                self.canvas.create_rectangle(
                    TIME_COL_W, y, W, y + HOUR_H,
                    fill=GRID_ALT, outline="", tags="grid")
            if h < END_HOUR:
                yh = y + HALF_H
                self.canvas.create_line(
                    TIME_COL_W, yh, W, yh,
                    fill=GRID_HALF, tags="grid")
            self.canvas.create_line(
                0, y, W, y,
                fill=GRID_HOUR, tags="grid")
            if h < END_HOUR:
                self.canvas.create_text(
                    TIME_COL_W - 6, y + 4,
                    text=f"{h:02d}:00",
                    font=("Helvetica Neue", 8),
                    fill=TIME_FG, anchor="ne", tags="grid")

    def _draw_blocks(self):
        self.canvas.delete("block")
        for b in self._blocks():
            self._draw_one(b)

    def _draw_one(self, b):
        y1 = min_to_y(b["start_min"])
        y2 = min_to_y(b["end_min"])
        done   = b.get("done", False)
        skipped = bool(b.get("skip_reason", ""))
        bid  = b["id"]
        h_px = y2 - y1
        # 优先用手动选的颜色，没有时才按文字自动匹配
        col  = b.get("color") or color_for(b["text"]) or DEFAULT_COL
        fill_col = "#B0B8C1" if (done or skipped) else col

        self.canvas.create_rectangle(
            CONTENT_X1, y1, CONTENT_X2, y2,
            fill=fill_col, outline="", tags=("block", f"b_{bid}"))

        # 顶部亮条
        self.canvas.create_rectangle(
            CONTENT_X1, y1, CONTENT_X2, y1 + 3,
            fill="#FFFFFF", stipple="gray25", outline="",
            tags=("block", f"b_{bid}"))

        ts = f"{b['start_min']//60:02d}:{b['start_min']%60:02d}"
        te = f"{b['end_min']//60:02d}:{b['end_min']%60:02d}"
        time_str = f"{ts}–{te}"
        reason   = b.get("skip_reason", "")
        text_fg  = "#FFFFFF" if not done else "#6B7280"

        # ── 按高度分四档 ──────────────────────────────────────────
        if h_px < 20:
            # 极短（15min）：小 checkbox + 名称
            cb_size   = 8
            name_font = ("Helvetica Neue", 8, "bold")
            label     = b["text"]
        elif h_px < 38:
            # 短（30min）：名称 + 时间同一行，小 checkbox
            cb_size   = 10
            name_font = ("Helvetica Neue", 9, "bold")
            label     = f"{b['text']}  {time_str}"
        elif h_px < 72:
            # 中（45–60min）：名称 + 时间分两行
            cb_size   = 12
            name_font = ("Helvetica Neue", 10, "bold")
            label     = f"{b['text']}\n{time_str}"
        else:
            # 长（75min+）：大字名称 + 时间
            cb_size   = 14
            name_font = ("Helvetica Neue", 13, "bold")
            label     = f"{b['text']}\n{time_str}"

        if reason:
            label += f"\n⚠ {reason}"

        # ── checkbox ──────────────────────────────────────────────
        if cb_size > 0:
            cb_x1 = CONTENT_X1 + 4
            cb_y1 = y1 + h_px / 2 - cb_size / 2
            cb_x2 = cb_x1 + cb_size
            cb_y2 = cb_y1 + cb_size
            cb_fill = "#FFFFFF" if not done else fill_col
            self.canvas.create_rectangle(
                cb_x1, cb_y1, cb_x2, cb_y2,
                fill=cb_fill, outline="#FFFFFF", width=1.2,
                tags=("block", f"b_{bid}", f"cb_{bid}"))
            if done:
                self.canvas.create_text(
                    cb_x1 + cb_size / 2, cb_y1 + cb_size / 2,
                    text="✓", font=("Helvetica Neue", cb_size - 2, "bold"),
                    fill="#FFFFFF", tags=("block", f"b_{bid}", f"cb_{bid}"))
            text_x = CONTENT_X1 + cb_size + 8
        else:
            text_x = CONTENT_X1 + 5

        # ── 文字（垂直居中）────────────────────────────────────────
        self.canvas.create_text(
            text_x, y1 + h_px / 2,
            text=label,
            font=name_font,
            fill=text_fg,
            anchor="w",
            tags=("block", f"b_{bid}"))

        # ── 底部缩放拖柄（小横条）────────────────────────────────
        mid_x = (CONTENT_X1 + CONTENT_X2) / 2
        self.canvas.create_rectangle(
            mid_x - 16, y2 - 4, mid_x + 16, y2 - 1,
            fill="#FFFFFF", outline="", stipple="gray50",
            tags=("block", f"b_{bid}", f"rsz_{bid}"))

    def _draw_now_line(self):
        self.canvas.delete("now")
        now = datetime.now()
        m = now.hour * 60 + now.minute
        if START_HOUR * 60 <= m <= END_HOUR * 60:
            y = min_to_y(m)
            self.canvas.create_oval(
                TIME_COL_W - 5, y - 5, TIME_COL_W + 5, y + 5,
                fill=NOW_COLOR, outline="", tags="now")
            self.canvas.create_line(
                TIME_COL_W, y, W, y,
                fill=NOW_COLOR, width=2, tags="now")
            self.canvas.create_text(
                TIME_COL_W - 8, y,
                text=now.strftime("%H:%M"),
                font=("Helvetica Neue", 7, "bold"),
                fill=NOW_COLOR, anchor="e", tags="now")

    def _scroll_to_now(self):
        now = datetime.now()
        m = now.hour * 60 + now.minute
        y = min_to_y(m)
        frac = max(0.0, (y - 100) / CANVAS_H)
        self.canvas.yview_moveto(frac)

    def _tick(self):
        if self._view_date == date.today():
            self._draw_now_line()
            self.root.after(30000, self._tick)

    # ── 交互 ─────────────────────────────────────────────────────────
    def _cy(self, e) -> float:
        return self.canvas.canvasy(e.y)

    def _hit_block(self, ex, cy):
        items = self.canvas.find_overlapping(ex - 2, cy - 2, ex + 2, cy + 2)
        for item in items:
            for tag in self.canvas.gettags(item):
                if tag.startswith("b_") and not tag.startswith("b_"):
                    pass
                if tag.startswith("b_") and len(tag) > 2:
                    bid = tag[2:]
                    if not bid.startswith("_"):  # 排除 cb_ 等
                        return bid
        return None

    def _hit_checkbox(self, ex, cy):
        items = self.canvas.find_overlapping(ex - 2, cy - 2, ex + 2, cy + 2)
        for item in items:
            for tag in self.canvas.gettags(item):
                if tag.startswith("cb_"):
                    return tag[3:]
        return None

    def _find_block(self, bid):
        for b in self._blocks():
            if b["id"] == bid:
                return b
        return None

    def _hit_bottom_edge(self, ex, cy):
        """检测 cy 是否落在某个块的底边 ±8px 内，返回 bid 或 None。"""
        for b in self._blocks():
            y_bottom = min_to_y(b["end_min"])
            if abs(cy - y_bottom) <= 8:
                bx1, bx2 = CONTENT_X1, CONTENT_X2
                if bx1 <= ex <= bx2:
                    return b["id"]
        return None

    def _on_press(self, e):
        cy = self._cy(e)
        # 先检查是否点击了 checkbox
        cbid = self._hit_checkbox(e.x, cy)
        if cbid:
            self._toggle_done(cbid)
            return
        # 检查是否点击了块的底边（缩放）
        rsz_bid = self._hit_bottom_edge(e.x, cy)
        if rsz_bid:
            self._resize_bid = rsz_bid
            self._drag_y0    = None
            return
        bid = self._hit_block(e.x, cy)
        if bid:
            b = self._find_block(bid)
            if b:
                self._move_bid      = bid
                self._move_offset_y = cy - min_to_y(b["start_min"])
                self._move_duration = b["end_min"] - b["start_min"]
            self._drag_y0 = None
            return
        self._drag_y0 = cy

    def _on_drag(self, e):
        cy = self._cy(e)
        # 缩放块（拖底边）
        if self._resize_bid is not None:
            b = self._find_block(self._resize_bid)
            if b:
                new_em = snap15(y_to_min(cy))
                new_em = max(b["start_min"] + 15,
                             min(new_em, END_HOUR * 60))
                b["end_min"] = new_em
                self._draw_all()
            return
        # 移动已有块
        if self._move_bid is not None:
            b = self._find_block(self._move_bid)
            if b:
                new_sm = snap15(y_to_min(cy - self._move_offset_y))
                new_sm = max(START_HOUR * 60,
                             min(new_sm, END_HOUR * 60 - self._move_duration))
                b["start_min"] = new_sm
                b["end_min"]   = new_sm + self._move_duration
                self._draw_all()
            return
        if self._drag_y0 is None:
            return
        y1, y2 = min(self._drag_y0, cy), max(self._drag_y0, cy)
        if self._preview:
            self.canvas.delete(self._preview)
        self._preview = self.canvas.create_rectangle(
            CONTENT_X1, y1, CONTENT_X2, y2,
            fill="#BFDBFE", outline="#3B82F6", width=1,
            tags="preview")

    def _on_release(self, e):
        if self._preview:
            self.canvas.delete(self._preview)
            self._preview = None
        # 缩放块松手 → 保存
        if self._resize_bid is not None:
            bid = self._resize_bid
            self._resize_bid = None
            self._save()
            self._draw_all()
            return
        # 移动块松手 → 保存，并同步提醒时间
        if self._move_bid is not None:
            bid = self._move_bid
            self._move_bid = None
            self._save()
            self._draw_all()
            b = self._find_block(bid)
            if b and b.get("reminder_id"):
                threading.Thread(
                    target=self._reminders_update_time,
                    args=(b["reminder_id"], b["start_min"], self._view_date),
                    daemon=True).start()
            return
        if self._drag_y0 is None:
            return
        cy = self._cy(e)
        sm = snap15(y_to_min(min(self._drag_y0, cy)))
        em = snap15(y_to_min(max(self._drag_y0, cy)))
        self._drag_y0 = None
        if em - sm < 15:
            return
        self._new_block_dialog(sm, em)

    def _on_right(self, e):
        cy = self._cy(e)
        bid = self._hit_block(e.x, cy) or self._hit_checkbox(e.x, cy)
        if not bid:
            return
        b = self._find_block(bid)
        if not b:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="✅ 标记完成" if not b.get("done") else "↩ 取消完成",
            command=lambda: self._toggle_done(bid))
        menu.add_command(
            label="⚠ 记录未完成原因",
            command=lambda: self._record_skip_reason(bid))
        linked = b.get("todo_ref")
        link_label = f"🔗 已链接：{linked[:16]}…" if linked and len(linked) > 16 else (f"🔗 已链接：{linked}" if linked else "🔗 链接待办")
        menu.add_command(label=link_label, command=lambda: self._link_todo_dialog(bid))
        menu.add_separator()
        menu.add_command(
            label="🗑 删除",
            command=lambda: self._delete_block(bid))
        menu.tk_popup(e.x_root, e.y_root)

    def _delete_block(self, bid):
        blocks = self._blocks()
        self._all[self._date_key()]["blocks"] = [b for b in blocks if b["id"] != bid]
        self._save()
        self._draw_all()

    def _link_todo_dialog(self, bid):
        """为已有 block 后补绑定项目待办。左右滑动切项目，上下滑动翻待办（canvas 丝滑）。"""
        b = self._find_block(bid)
        if not b:
            return

        win = tk.Toplevel(self.root)
        win.title("链接待办")
        win.configure(bg="#FFFFFF")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        win.geometry(f"300x360+{rx+30}+{ry+80}")
        win.after(10, win.lift)
        win.after(20, win.focus_force)

        cur_ref = b.get("todo_ref")
        tk.Label(win, text=b.get("text", ""), font=("Helvetica Neue", 11, "bold"),
                 bg="#FFFFFF", fg="#1E293B").pack(pady=(12, 2), padx=14)
        status = f"当前链接：{cur_ref}" if cur_ref else "尚未链接任何待办"
        tk.Label(win, text=status, font=("Helvetica Neue", 9),
                 bg="#FFFFFF", fg="#94A3B8").pack(pady=(0, 6))
        tk.Frame(win, bg="#E2E8F0", height=1).pack(fill="x", padx=12)

        todos_cache = self._load_project_todos()
        proj_idx  = [0]
        proj_btns = []
        dx_accum  = [0.0]   # 横向累积，防误触

        proj_frame = tk.Frame(win, bg="#FFFFFF")
        proj_frame.pack(fill="x", padx=12, pady=8)

        # ── 待办 Canvas（丝滑滚动，和主 canvas 机制一样）──────────────
        todo_cv = tk.Canvas(win, bg="#FFFFFF", highlightthickness=0,
                            height=200, yscrollincrement=3)
        todo_cv.pack(fill="both", expand=True, padx=12)
        todo_inner = tk.Frame(todo_cv, bg="#FFFFFF")
        _win_id = todo_cv.create_window((0, 0), window=todo_inner, anchor="nw")

        def _on_inner_cfg(e):
            todo_cv.configure(scrollregion=todo_cv.bbox("all"))
        def _on_cv_cfg(e):
            todo_cv.itemconfig(_win_id, width=e.width)
        todo_inner.bind("<Configure>", _on_inner_cfg)
        todo_cv.bind("<Configure>",    _on_cv_cfg)

        def _apply(todo_ref, color):
            b["todo_ref"] = todo_ref
            b["color"]    = color
            self._save()
            self._draw_all()
            win.destroy()

        def _render_todos():
            for w in todo_inner.winfo_children():
                w.destroy()
            todo_cv.yview_moveto(0)
            label, color, _ = WORK_PROJECTS_MAP[proj_idx[0]]
            todos = todos_cache.get(WORK_PROJECTS_MAP[proj_idx[0]][2], [])
            if not todos:
                tk.Label(todo_inner, text="（无待办）", font=("Helvetica Neue", 9),
                         bg="#FFFFFF", fg="#94A3B8").pack(anchor="w", pady=4)
                return
            for todo_text in todos:
                disp = todo_text if len(todo_text) <= 32 else todo_text[:31] + "…"
                is_current = (todo_text == cur_ref)
                bg_c = "#EFF6FF" if is_current else "#F8FAFC"
                row = tk.Label(todo_inner, text=f"  {'✓ ' if is_current else ''}{disp}",
                               font=("Helvetica Neue", 10),
                               bg=bg_c, fg="#334155", anchor="w",
                               cursor="hand2", relief="flat", pady=5, padx=6)
                row.pack(fill="x", pady=1)
                row.bind("<Button-1>", lambda e, t=todo_text, c=color: _apply(t, c))
                row.bind("<Enter>",    lambda e, rw=row: rw.config(bg="#EFF6FF"))
                row.bind("<Leave>",    lambda e, rw=row, bc=bg_c: rw.config(bg=bc))

        def _select_proj(idx):
            proj_idx[0] = idx % len(WORK_PROJECTS_MAP)
            dx_accum[0] = 0.0
            for i, (pb, (_, pc, _)) in enumerate(zip(proj_btns, WORK_PROJECTS_MAP)):
                pb.config(relief="solid" if i == proj_idx[0] else "flat",
                          bd=2    if i == proj_idx[0] else 0)
            _render_todos()

        # NSEvent 路由：垂直 = canvas yview（与主 canvas 完全一样）
        self._dialog_scroll_v = lambda dy: todo_cv.yview_scroll(
            -1 if dy > 0 else 1, "units")

        # 水平：累积到阈值才切换项目，防误触
        def _h_scroll(dx):
            dx_accum[0] += dx
            if dx_accum[0] > 35:
                dx_accum[0] = 0.0
                _select_proj(proj_idx[0] - 1)
            elif dx_accum[0] < -35:
                dx_accum[0] = 0.0
                _select_proj(proj_idx[0] + 1)
        self._dialog_scroll_h = _h_scroll

        def _cleanup(e=None):
            if e and e.widget is not win:
                return
            self._dialog_scroll_v = None
            self._dialog_scroll_h = None
        win.bind("<Destroy>", _cleanup)

        for i, (label, color, obs_key) in enumerate(WORK_PROJECTS_MAP):
            short = WORK_PROJECTS_SHORT.get(obs_key, label.split()[-1])
            pbtn = tk.Label(proj_frame, text=short, font=("Helvetica Neue", 10, "bold"),
                            bg=color, fg="#FFFFFF", padx=8, pady=5,
                            cursor="hand2", relief="flat", bd=0)
            pbtn.pack(side="left", padx=3)
            pbtn.bind("<Button-1>", lambda e, idx=i: _select_proj(idx))
            proj_btns.append(pbtn)

        _select_proj(0)

        # 取消链接按钮
        tk.Frame(win, bg="#E2E8F0", height=1).pack(fill="x", padx=12, pady=(4, 0))
        if cur_ref:
            def _unlink():
                b.pop("todo_ref", None)
                self._save()
                win.destroy()
            tk.Button(win, text="取消链接", command=_unlink,
                      bg="#FEF2F2", fg="#EF4444", relief="flat",
                      font=("Helvetica Neue", 9), pady=4).pack(pady=6)

    def _ask(self, title, prompt, initialvalue=""):
        """暂时关闭 topmost 再弹对话框，避免 macOS 下闪退。"""
        self._win.attributes("-topmost", False)
        try:
            result = simpledialog.askstring(
                title, prompt, initialvalue=initialvalue, parent=self._win)
        finally:
            self._win.attributes("-topmost", True)
        return result

    # ── 晨间压缩对话 ────────────────────────────────────────────────────
    def _maybe_show_compression(self):
        """如果昨天完成率低且今天还没问过，显示压缩提示 banner。"""
        try:
            today_str = date.today().isoformat()
            state = {}
            if STATE_FILE.exists():
                state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

            # 今天已经问过，只需显示上限指示器
            if state.get("compression_asked_date") == today_str:
                limit = state.get("today_limit")
                if limit:
                    self._show_limit_in_nav(limit)
                return

            # 读昨天的数据
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            raw = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8")) if SCHEDULES_FILE.exists() else {}
            blocks = raw.get(yesterday, {}).get("blocks", [])

            total = len(blocks)
            if total < 3:
                return   # 块数太少，不触发

            done = sum(1 for b in blocks if b.get("done", False))
            rate = done / total

            if rate >= 0.6:
                return   # 完成率够高，不触发

            # 建议上限 = 昨天完成数 + 1，至少 1
            suggested = max(1, done + 1)
            self._compression_var.set(str(suggested))
            msg = f"昨天排了 {total} 块，完成了 {done} 块。\n今天最多完成几块？"
            self._compression_msg.config(text=msg)
            # 插到 divider 下方（todo_container 上方）
            self._compression_frame.pack(fill="x",
                                         before=self._todo_container)

        except Exception:
            pass

    def _confirm_compression(self):
        """用户确认今日上限，检查今天是否超出，按需进入第二阶段。"""
        try:
            limit = int(self._compression_var.get())
        except ValueError:
            limit = 3

        today_str = date.today().isoformat()
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            state["compression_asked_date"] = today_str
            state["today_limit"] = limit
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        # 检查今天有多少未完成块
        today_blocks = self._all.get(today_str, {}).get("blocks", [])
        undone = [b for b in today_blocks if not b.get("done", False)]

        if len(undone) > limit:
            self._show_excess_phase(len(undone), limit)
        else:
            self._compression_frame.pack_forget()
            self._show_limit_in_nav(limit)

    def _show_excess_phase(self, undone_count: int, limit: int):
        """第二阶段：问用户怎么处理多余的块。两行布局避免按钮被挤掉。"""
        # 清空整个 compression_frame 内容，重建
        for w in self._compression_frame.winfo_children():
            w.destroy()

        excess = undone_count - limit

        # 第一行：说明文字
        tk.Label(self._compression_frame,
                 text=f"今天排了 {undone_count} 块，超出 {excess} 块。多余的怎么处理？",
                 font=("Helvetica Neue", 10, "bold"),
                 fg="#7C2D12", bg="#FFF7ED",
                 anchor="w").pack(fill="x", padx=10, pady=(8, 4))

        # 第二行：三个按钮
        btn_row = tk.Frame(self._compression_frame, bg="#FFF7ED")
        btn_row.pack(fill="x", padx=10, pady=(0, 8))

        tk.Button(btn_row, text="推到明天",
                  font=("Helvetica Neue", 10, "bold"),
                  fg="#FFFFFF", bg="#F97316",
                  relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                  command=lambda: self._handle_excess("tomorrow")).pack(
                      side="left", padx=(0, 6))
        tk.Button(btn_row, text="移入积压",
                  font=("Helvetica Neue", 10, "bold"),
                  fg="#FFFFFF", bg="#64748B",
                  relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                  command=lambda: self._handle_excess("backlog")).pack(
                      side="left", padx=(0, 6))
        tk.Button(btn_row, text="不管了",
                  font=("Helvetica Neue", 10),
                  fg="#7C2D12", bg="#FED7AA",
                  relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                  command=lambda: self._handle_excess("ignore")).pack(
                      side="left")

        tk.Frame(self._compression_frame, bg="#FED7AA", height=1).pack(fill="x")

    def _handle_excess(self, action: str):
        """处理多余的未完成块。"""
        today_str  = date.today().isoformat()
        tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

        today_data = self._all.get(today_str, {})
        today_blocks = today_data.get("blocks", [])
        undone = [b for b in today_blocks if not b.get("done", False)]
        done   = [b for b in today_blocks if b.get("done", False)]

        if action == "tomorrow":
            # 保留今天已完成的，未完成的全部移到明天
            today_data["blocks"] = done
            self._all[today_str] = today_data
            tomorrow_data = self._all.get(tomorrow_str, {"blocks": []})
            if not isinstance(tomorrow_data, dict):
                tomorrow_data = {"blocks": []}
            tomorrow_data["blocks"] = undone + tomorrow_data["blocks"]
            self._all[tomorrow_str] = tomorrow_data
            self._save()
            self._refresh()

        elif action == "backlog":
            # 写入 pending，从今天移除
            for blk in undone:
                self._write_to_pending(blk)
            today_data["blocks"] = done
            self._all[today_str] = today_data
            self._save()
            self._refresh()

        # ignore: 什么都不做

        self._compression_frame.pack_forget()
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            self._show_limit_in_nav(state.get("today_limit", 3))
        except Exception:
            pass

    def _show_limit_in_nav(self, limit: int):
        """在日期标签旁显示一个小的今日上限提示。"""
        try:
            if hasattr(self, "_limit_lbl") and self._limit_lbl.winfo_exists():
                self._limit_lbl.config(text=f"上限 {limit} 块")
                return
            self._limit_lbl = tk.Label(
                self._date_lbl.master,
                text=f"上限 {limit} 块",
                font=("Helvetica Neue", 9),
                fg="#F97316", bg=BG)
            self._limit_lbl.pack(side="left", padx=(0, 4))
        except Exception:
            pass

    # ── Pending（未完成积压） ───────────────────────────────────────────
    def _write_to_pending(self, block: dict):
        """把跳过的块写入 pending.json，避免重复。"""
        try:
            data = json.loads(PENDING_FILE.read_text(encoding="utf-8")) if PENDING_FILE.exists() else {"items": []}
            items = data.setdefault("items", [])
            # 同名未完成项已存在则更新，否则追加
            existing = next((x for x in items if x.get("text") == block["text"]), None)
            entry = {
                "id":            block.get("id", str(uuid.uuid4())),
                "text":          block["text"],
                "original_date": self._date_key(),
                "skip_reason":   block.get("skip_reason", ""),
                "added_at":      datetime.now().isoformat(timespec="seconds"),
            }
            if existing:
                existing.update(entry)
            else:
                items.append(entry)
            PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _remove_from_pending(self, block: dict):
        """完成某块时，从 pending.json 里移除同名条目。"""
        try:
            if not PENDING_FILE.exists():
                return
            data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            data["items"] = [x for x in data.get("items", []) if x.get("text") != block["text"]]
            PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _record_skip_reason(self, bid):
        b = self._find_block(bid)
        if not b:
            return
        reason = self._ask(
            "未完成原因",
            f"「{b['text']}」\n\n没完成的原因：",
            initialvalue=b.get("skip_reason", ""))
        if reason is None:
            return
        b["done"] = False
        b["skip_reason"] = reason.strip()
        self._save()
        self._draw_all()
        # 写入 pending 积压（不论原因是否为空）
        self._write_to_pending(b)
        # skip 了也在 Reminders 标完成
        if reason.strip():
            rid = b.get("reminder_id")
            if rid:
                threading.Thread(
                    target=self._reminders_complete,
                    args=(rid,), daemon=True).start()

    def _toggle_done(self, bid):
        b = self._find_block(bid)
        if not b:
            return
        b["done"] = not b.get("done", False)
        if b["done"]:
            b["skip_reason"] = ""
            self._remove_from_pending(b)
            # 同步勾掉 Apple Reminders
            rid = b.get("reminder_id")
            if rid:
                threading.Thread(
                    target=self._reminders_complete,
                    args=(rid,), daemon=True).start()
        # 同步本周计划 md
        threading.Thread(target=self._update_weekly_plan,
                         args=(b,), daemon=True).start()
        # 同步项目进度.md（有 todo_ref 时）
        threading.Thread(target=self._update_project_progress,
                         args=(b,), daemon=True).start()
        self._save()
        self._draw_all()

    def _update_weekly_plan(self, block: dict):
        """勾选/取消时同步更新本周计划 md 里对应的 checkbox。"""
        worklog = Path.home() / "Desktop" / "ob" / "个人版" / "03 工作log"
        candidates = sorted(worklog.glob("本周计划*.md"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return
        plan_file = candidates[0]

        # 提取关键词：去掉 emoji 和多余空格
        raw = block["text"]
        clean_key = re.sub(r'[\U0001F300-\U0001FFFF\u2600-\u27BF]', '', raw)
        clean_key = re.sub(r'\s+', '', clean_key).lower().strip()
        if len(clean_key) < 2:
            return

        done = block.get("done", False)
        text = plan_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        new_lines = []
        changed = False

        for line in lines:
            stripped = line.strip()
            is_unchecked = stripped.startswith("- [ ]")
            is_checked   = stripped.startswith("- [x]")
            if is_unchecked or is_checked:
                clean_line = re.sub(r'\s+', '', line).lower()
                if clean_key in clean_line:
                    if done and is_unchecked:
                        line = line.replace("- [ ]", "- [x]", 1)
                        changed = True
                    elif not done and is_checked:
                        line = line.replace("- [x]", "- [ ]", 1)
                        changed = True
            new_lines.append(line)

        if changed:
            plan_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def _load_project_todos(self) -> dict:
        """返回 {obs_key: ["todo text", ...]}，只含未完成 todo。"""
        result = {}
        pf = orbit_config.project_file()
        if pf is None or not pf.exists():
            return result
        current_key = None
        for line in pf.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                heading = line[2:].strip()
                current_key = None
                for _, _, obs_key in WORK_PROJECTS_MAP:
                    if obs_key in heading:
                        current_key = obs_key
                        break
            elif current_key and re.match(r'\s+- \[ \]', line):
                task_text = re.sub(r'^\s+- \[ \]\s*', '', line).strip()
                if task_text:
                    result.setdefault(current_key, []).append(task_text)
        return result

    def _update_project_progress(self, block: dict):
        """block 完成/取消时，同步写回 项目进度.md 里对应的 todo。"""
        todo_ref = block.get("todo_ref")
        pf = orbit_config.project_file()
        if not todo_ref or pf is None or not pf.exists():
            return
        done = block.get("done", False)
        lines = pf.read_text(encoding="utf-8").splitlines()
        new_lines = []
        changed = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
                todo_text = re.sub(r'^- \[.\]\s*', '', stripped).strip()
                if todo_text == todo_ref:
                    if done and "- [ ]" in line:
                        line = line.replace("- [ ]", "- [x]", 1)
                        changed = True
                    elif not done and "- [x]" in line:
                        line = line.replace("- [x]", "- [ ]", 1)
                        changed = True
            new_lines.append(line)
        if changed:
            pf.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def _new_block_dialog(self, sm: int, em: int):
        ts = f"{sm//60:02d}:{sm%60:02d}"
        te = f"{em//60:02d}:{em%60:02d}"

        win = tk.Toplevel(self.root)
        win.title("新建日程")
        win.configure(bg="#FFFFFF")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        win.geometry(f"300x380+{rx+30}+{ry+80}")
        win.after(10, win.lift)
        win.after(20, win.focus_force)

        tk.Label(win, text=f"{ts} → {te}", font=("Helvetica Neue", 12, "bold"),
                 bg="#FFFFFF", fg="#1E293B").pack(pady=(14, 4))
        tk.Label(win, text="选择项目或自由输入", font=("Helvetica Neue", 10),
                 bg="#FFFFFF", fg="#94A3B8").pack(pady=(0, 8))
        tk.Frame(win, bg="#E2E8F0", height=1).pack(fill="x", padx=12)

        todos_cache = self._load_project_todos()

        # ── 项目按钮区（横向可滚动）──────────────────────────────────
        proj_cv = tk.Canvas(win, bg="#FFFFFF", highlightthickness=0, height=36)
        proj_cv.pack(fill="x", padx=12, pady=(8, 4))
        proj_frame = tk.Frame(proj_cv, bg="#FFFFFF")
        proj_cv_win = proj_cv.create_window(0, 0, anchor="nw", window=proj_frame)
        proj_frame.bind("<Configure>", lambda e: proj_cv.configure(
            scrollregion=proj_cv.bbox("all")))

        # 可滚动 todo 区（纵向）
        todo_outer = tk.Frame(win, bg="#FFFFFF")
        todo_outer.pack(fill="both", expand=True, padx=12)
        todo_cv = tk.Canvas(todo_outer, bg="#FFFFFF", highlightthickness=0)
        todo_cv.pack(fill="both", expand=True)
        todo_inner = tk.Frame(todo_cv, bg="#FFFFFF")
        todo_cv_win = todo_cv.create_window(0, 0, anchor="nw", window=todo_inner)
        todo_inner.bind("<Configure>", lambda e: todo_cv.configure(
            scrollregion=todo_cv.bbox("all")))
        todo_cv.bind("<Configure>", lambda e: todo_cv.itemconfig(
            todo_cv_win, width=e.width))

        # 打开时：上下→todo 列表，左右→项目按钮；关闭时还原
        self._dialog_scroll_v = lambda dy: todo_cv.yview_scroll(
            -1 if dy > 0 else 1, "units")
        self._dialog_scroll_h = lambda dx: proj_cv.xview_scroll(
            -1 if dx > 0 else 1, "units")

        def _clear_scroll():
            self._dialog_scroll_v = None
            self._dialog_scroll_h = None

        def _on_win_close():
            _clear_scroll()
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_win_close)

        todo_frame = todo_inner   # alias so rest of code unchanged

        def _show_todos(label, color, obs_key):
            for w in todo_frame.winfo_children():
                w.destroy()
            todos = todos_cache.get(obs_key, [])
            tk.Label(todo_frame, text=label, font=("Helvetica Neue", 10, "bold"),
                     bg="#FFFFFF", fg=color).pack(anchor="w", pady=(4, 2))
            if not todos:
                tk.Label(todo_frame, text="（无待办）", font=("Helvetica Neue", 9),
                         bg="#FFFFFF", fg="#94A3B8").pack(anchor="w")
                return
            for todo_text in todos:
                disp = todo_text if len(todo_text) <= 32 else todo_text[:31] + "…"
                btn = tk.Label(todo_frame, text=f"  {disp}", font=("Helvetica Neue", 10),
                               bg="#F8FAFC", fg="#334155", anchor="w", cursor="hand2",
                               relief="flat", pady=4, padx=6)
                btn.pack(fill="x", pady=1)
                def _pick(t=todo_text, c=color):
                    _clear_scroll()
                    win.destroy()
                    _make_block(t, c, t)
                btn.bind("<Button-1>", lambda e, fn=_pick: fn())
                btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#EFF6FF"))
                btn.bind("<Leave>", lambda e, b=btn: b.config(bg="#F8FAFC"))

        for label, color, obs_key in WORK_PROJECTS_MAP:
            short = WORK_PROJECTS_SHORT.get(obs_key, label.split()[-1])
            btn = tk.Label(proj_frame, text=short, font=("Helvetica Neue", 10, "bold"),
                           bg=color, fg="#FFFFFF", padx=8, pady=5,
                           cursor="hand2", relief="flat")
            btn.pack(side="left", padx=3)
            btn.bind("<Button-1>",
                     lambda e, lb=label, c=color, k=obs_key: _show_todos(lb, c, k))

        tk.Frame(win, bg="#E2E8F0", height=1).pack(fill="x", padx=12, pady=(4, 0))

        # ── 自由输入区 ──────────────────────────────────────────────
        free_frame = tk.Frame(win, bg="#FFFFFF")
        free_frame.pack(fill="x", padx=12, pady=6)
        tk.Label(free_frame, text="或自由输入：", font=("Helvetica Neue", 9),
                 bg="#FFFFFF", fg="#94A3B8").pack(anchor="w")
        entry_var = tk.StringVar()
        entry = tk.Entry(free_frame, textvariable=entry_var,
                         font=("Helvetica Neue", 11), bg="#F8FAFC", fg="#1E293B",
                         relief="flat", highlightthickness=1,
                         highlightbackground="#E2E8F0", highlightcolor="#3B82F6")
        entry.pack(fill="x", pady=(2, 0))

        def _make_block(text, color, todo_ref):
            block = {
                "id":          str(uuid.uuid4())[:8],
                "start_min":   sm,
                "end_min":     em,
                "text":        text,
                "color":       color,
                "done":        False,
                "skip_reason": "",
            }
            if todo_ref:
                block["todo_ref"] = todo_ref
            self._blocks().append(block)
            self._save()
            self._draw_all()

        def _confirm_free(e=None):
            t = entry_var.get().strip()
            if t:
                _clear_scroll()
                win.destroy()
                _make_block(t, color_for(t), None)

        entry.bind("<Return>", _confirm_free)
        tk.Button(free_frame, text="确认", command=_confirm_free,
                  bg="#3B82F6", fg="#FFFFFF", relief="flat",
                  font=("Helvetica Neue", 10), padx=10, pady=3).pack(anchor="e", pady=4)

    def _pick_time_for_todo(self, todo):
        """弹出时间选择窗，确认后放入日程。"""
        if isinstance(todo, str):
            todo = self._norm_todo(todo)
        text = todo["text"]
        dur  = todo.get("duration", 60)
        col  = todo.get("color") or color_for(text)

        now = datetime.now()
        sm_var = [snap15(now.hour * 60 + now.minute)]

        win = tk.Toplevel(self.root)
        win.title("放入日程")
        win.configure(bg="#FFFFFF")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        win.geometry(f"+{rx+40}+{ry+100}")
        win.after(10, win.lift)
        win.after(20, win.focus_force)

        # 任务颜色条 + 标题
        tk.Frame(win, bg=col, height=5).pack(fill="x")
        tk.Label(win, text=text, font=("Helvetica Neue", 13, "bold"),
                 bg="#FFFFFF", fg="#1E293B").pack(pady=(10, 2), padx=20)

        dur_h, dur_m = divmod(dur, 60)
        dur_str = (f"{dur_h}h{dur_m}m" if dur_m else f"{dur_h}h") if dur_h else f"{dur_m}m"
        tk.Label(win, text=f"时长  {dur_str}", font=("Helvetica Neue", 10),
                 bg="#FFFFFF", fg="#94A3B8").pack(pady=(0, 6))

        tk.Frame(win, bg="#E2E8F0", height=1).pack(fill="x", padx=12)

        # 时间选择行
        trow = tk.Frame(win, bg="#FFFFFF")
        trow.pack(pady=14, padx=20)

        tk.Label(trow, text="开始", font=("Helvetica Neue", 10),
                 bg="#FFFFFF", fg="#64748B").pack(side="left", padx=(0, 8))

        def _refresh():
            sm = sm_var[0]
            em = sm + dur
            time_lbl.config(text=f"{sm//60:02d}:{sm%60:02d}")
            end_lbl.config(text=f"→  {em//60:02d}:{em%60:02d}")

        def _step(delta):
            sm_var[0] = max(0, min(sm_var[0] + delta * 15, 23 * 60 + 45))
            _refresh()

        btn_l = tk.Label(trow, text="◀", font=("Helvetica Neue", 14), fg="#94A3B8",
                         bg="#FFFFFF", cursor="hand2")
        btn_l.pack(side="left")
        btn_l.bind("<Button-1>", lambda e: _step(-1))

        time_lbl = tk.Label(trow, text="", font=("Helvetica Neue", 18, "bold"),
                            bg="#F8FAFC", fg="#1E293B", width=5, pady=4, padx=6)
        time_lbl.pack(side="left", padx=4)

        btn_r = tk.Label(trow, text="▶", font=("Helvetica Neue", 14), fg="#94A3B8",
                         bg="#FFFFFF", cursor="hand2")
        btn_r.pack(side="left")
        btn_r.bind("<Button-1>", lambda e: _step(1))

        end_lbl = tk.Label(trow, text="", font=("Helvetica Neue", 11),
                           bg="#FFFFFF", fg="#94A3B8")
        end_lbl.pack(side="left", padx=(10, 0))

        _refresh()

        def _commit():
            self._place_todo(todo, sm_var[0])
            win.destroy()

        tk.Button(win, text="  放入日程  ", command=_commit,
                  bg=col, fg="#FFFFFF", relief="flat",
                  font=("Helvetica Neue", 11, "bold"), pady=8,
                  cursor="hand2").pack(pady=(4, 16), padx=20, fill="x")

    def _place_todo(self, todo, start_min: int):
        """将待办按指定开始时间放入时间块，并从待安排移除。"""
        if isinstance(todo, str):
            todo = self._norm_todo(todo)
        text = todo["text"]
        dur  = todo.get("duration", 60)
        col  = todo.get("color") or color_for(text)
        sm   = start_min
        em   = sm + dur
        # 找到 reminder_id（如有）
        day = self._all.setdefault(self._date_key(), {})
        lst = day.get("todos", [])
        rid = next((
            (t.get("reminder_id") if isinstance(t, dict) else None)
            for t in lst
            if (t if isinstance(t, str) else t.get("text")) == text
        ), None) or todo.get("reminder_id")

        bid = str(uuid.uuid4())[:8]
        self._blocks().append({
            "id":          bid,
            "start_min":   sm,
            "end_min":     em,
            "text":        text,
            "color":       col,
            "done":        False,
            "skip_reason": "",
            "reminder_id": rid or "",
        })
        day["todos"] = [t for t in lst
                        if (t if isinstance(t, str) else t.get("text")) != text]
        # 记录已放入的 todo，防止 daily_todos.json 合并时重新出现
        dismissed = day.setdefault("dismissed_todos", [])
        if text not in dismissed:
            dismissed.append(text)
        self._save()

        # 同步到 Apple Reminders：更新提醒时间
        if rid:
            threading.Thread(
                target=self._reminders_update_time,
                args=(rid, sm), daemon=True).start()
        self._build_todo_strip()
        self._draw_all()
        self.canvas.yview_moveto(max(0.0, (min_to_y(sm) - 80) / CANVAS_H))

    def _place_todo_now(self, todo):
        """快捷：放入当前时间（保留供旧调用）。"""
        if isinstance(todo, str):
            todo = self._norm_todo(todo)
        now = datetime.now()
        sm = snap15(now.hour * 60 + now.minute)
        self._place_todo(todo, sm)


    # ── 提醒系统 ──────────────────────────────────────────────────────

    def _start_reminder_thread(self):
        def _loop():
            while not self._reminder_stop.wait(20):   # 每 20 秒检查一次
                self.root.after(0, self._check_reminders)
        threading.Thread(target=_loop, daemon=True).start()

    def _check_reminders(self):
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        for block in self._blocks(date.today()):
            if block.get("done") or block.get("skip_reason"):
                continue
            bid = block["id"]
            if block["start_min"] == now_min and bid not in self._reminded_ids:
                self._reminded_ids.add(bid)
                self._show_reminder_popup(block)

    def _show_reminder_popup(self, block: dict):
        text = block["text"]
        sm, em = block["start_min"], block["end_min"]
        col  = block.get("color") or color_for(text)
        time_str = f"{sm//60:02d}:{sm%60:02d} – {em//60:02d}:{em%60:02d}"

        # macOS 系统通知（即使窗口在后台也能收到）
        try:
            body = f"{time_str}  {text}"
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{body}" with title "⏰ 时间到" sound name "Ping"'],
                check=False, timeout=5)
        except Exception:
            pass

        # tkinter 弹窗
        pop = tk.Toplevel(self.root)
        pop.title("")
        pop.configure(bg="#FFFFFF")
        pop.resizable(False, False)
        pop.attributes("-topmost", True)

        # 放在屏幕右下角附近
        sw = pop.winfo_screenwidth()
        sh = pop.winfo_screenheight()
        pop.geometry(f"280x130+{sw-310}+{sh-180}")

        tk.Frame(pop, bg=col, height=5).pack(fill="x")

        tk.Label(pop, text="⏰ 时间到", font=("Helvetica Neue", 10),
                 bg="#FFFFFF", fg="#94A3B8").pack(pady=(10, 0))

        tk.Label(pop, text=text, font=("Helvetica Neue", 14, "bold"),
                 bg="#FFFFFF", fg="#1E293B").pack(pady=(2, 0))

        tk.Label(pop, text=time_str, font=("Helvetica Neue", 10),
                 bg="#FFFFFF", fg="#94A3B8").pack(pady=(2, 8))

        tk.Button(pop, text="知道了", command=pop.destroy,
                  bg=col, fg="#FFFFFF", relief="flat",
                  font=("Helvetica Neue", 10, "bold"),
                  padx=20, pady=5, cursor="hand2").pack(pady=(0, 12))

        # 30 秒后自动关闭
        pop.after(30000, lambda: pop.destroy() if pop.winfo_exists() else None)

    def _show_review(self):
        blocks = self._blocks()
        if not blocks:
            return

        d = self._view_date
        done_blocks   = [b for b in blocks if b.get("done")]
        undone_blocks = [b for b in blocks if not b.get("done")]

        # ── 计算总规划时长 ──
        total_planned = sum(b["end_min"] - b["start_min"] for b in blocks)
        total_done    = sum(b["end_min"] - b["start_min"] for b in done_blocks)

        def fmt_mins(m):
            h, mn = divmod(m, 60)
            return f"{h}h{mn}m" if h else f"{mn}m"

        # ── 构建复盘文本 ──
        lines = []
        lines.append(f"## 日程复盘 {d.strftime('%-m月%-d日')}\n")
        lines.append(f"规划 {fmt_mins(total_planned)}  ·  完成 {fmt_mins(total_done)}  ·  完成率 {int(total_done/total_planned*100) if total_planned else 0}%\n")

        if done_blocks:
            lines.append("\n**✅ 已完成**")
            for b in sorted(done_blocks, key=lambda x: x["start_min"]):
                ts = f"{b['start_min']//60:02d}:{b['start_min']%60:02d}"
                te = f"{b['end_min']//60:02d}:{b['end_min']%60:02d}"
                lines.append(f"- {ts}–{te}  {b['text']}")

        if undone_blocks:
            lines.append("\n**❌ 未完成**")
            for b in sorted(undone_blocks, key=lambda x: x["start_min"]):
                ts = f"{b['start_min']//60:02d}:{b['start_min']//60:02d}"
                ts = f"{b['start_min']//60:02d}:{b['start_min']%60:02d}"
                te = f"{b['end_min']//60:02d}:{b['end_min']%60:02d}"
                reason = b.get("skip_reason", "")
                reason_str = f"  →  {reason}" if reason else ""
                lines.append(f"- {ts}–{te}  {b['text']}{reason_str}")

        review_text = "\n".join(lines)

        # ── 弹出复盘窗口 ──
        self._win.attributes("-topmost", False)
        win = tk.Toplevel(self._win)
        win.title("复盘")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.geometry("340x420")

        tk.Label(win, text=f"{d.strftime('%-m月%-d日')} 复盘",
                 font=("Helvetica Neue", 13, "bold"),
                 fg="#334155", bg=BG).pack(pady=(14, 6))

        txt = tk.Text(win, font=("Helvetica Neue", 10),
                      fg="#334155", bg="#F8FAFC",
                      relief="flat", bd=0,
                      padx=12, pady=8,
                      wrap="word", height=16, width=36)
        txt.pack(fill="both", expand=True, padx=12)
        txt.insert("1.0", review_text)
        txt.config(state="disabled")

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=10)

        def save_to_log():
            log_path = (Path.home() / "Desktop" / "ob" / "个人版" /
                        "03 工作log" / f"{d.strftime('%m-%d-%a')}.md")
            section = f"---\n\n{review_text}\n"
            try:
                if log_path.exists():
                    existing = log_path.read_text(encoding="utf-8")
                    # 替换已有的日程复盘块（从 --- 到下一个 --- 或文件末尾）
                    new_content = re.sub(
                        r'---\s*\n\s*## 日程复盘.*?(?=\n---|\Z)',
                        section.rstrip(),
                        existing,
                        flags=re.DOTALL,
                    )
                    if new_content == existing:
                        # 没有旧复盘块，插到 **数据速览** 之前
                        if "**数据速览**" in existing:
                            new_content = existing.replace(
                                "**数据速览**",
                                section + "\n**数据速览**",
                                1,
                            )
                        else:
                            new_content = existing.rstrip() + "\n\n" + section
                    log_path.write_text(new_content, encoding="utf-8")
                else:
                    log_path.write_text(f"# {d.isoformat()} 工作日志\n\n{section}",
                                        encoding="utf-8")
                save_btn.config(text="✅ 已存入", state="disabled",
                               bg="#DCFCE7", fg="#16A34A",
                               disabledforeground="#16A34A")
            except Exception as ex:
                save_btn.config(text=f"失败: {ex}")

        save_btn = tk.Button(btn_row, text="存入工作 log",
                             font=("Helvetica Neue", 11, "bold"),
                             fg="#FFFFFF", bg="#E07068",
                             relief="flat", bd=0, padx=14, pady=6,
                             command=save_to_log)
        save_btn.pack(side="left")

        tk.Button(btn_row, text="关闭",
                  font=("Helvetica Neue", 10),
                  fg="#64748B", bg="#E2E8F0",
                  relief="flat", bd=0, padx=12, pady=5,
                  command=lambda: [win.destroy(),
                                   self._win.attributes("-topmost", True)]
                  ).pack(side="right")

        win.protocol("WM_DELETE_WINDOW",
                     lambda: [win.destroy(), self._win.attributes("-topmost", True)])


if __name__ == "__main__":
    import traceback
    LOG = Path.home() / "Library" / "Application Support" / "Orbit" / "planner_crash.log"
    try:
        FocusPlanner()
    except Exception:
        LOG.write_text(traceback.format_exc(), encoding="utf-8")
        raise
