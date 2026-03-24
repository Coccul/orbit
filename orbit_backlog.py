#!/usr/bin/env python3
"""
Orbit 积压看板 — 查看 pending.json，一键排入某天日程
支持独立窗口 & 嵌入 Widget 两种模式
"""
import tkinter as tk
from tkinter import simpledialog, messagebox
import json
import uuid
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

APP_SUPPORT    = Path.home() / "Library" / "Application Support" / "Orbit"

_HERE = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
         else Path(__file__).parent)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import orbit_config
PENDING_FILE   = APP_SUPPORT / "pending.json"
SCHEDULES_FILE = APP_SUPPORT / "schedules.json"

BG        = "#FFFFFF"
ROW_ODD   = "#F8FAFC"
ROW_EVEN  = "#FFFFFF"
ACCENT    = "#5B8FD4"
RED       = "#EF4444"
TEXT_MAIN = "#1E293B"
TEXT_SUB  = "#64748B"
BORDER    = "#E2E8F0"

DEFAULT_COL = "#5B8FD4"
TASK_BG     = orbit_config.load_task_colors()

def color_for(text: str) -> str:
    for key, col in TASK_BG.items():
        if key in text:
            return col
    return DEFAULT_COL


def load_pending() -> list:
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def save_pending(items: list):
    PENDING_FILE.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def load_schedules() -> dict:
    try:
        return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_schedules(data: dict):
    SCHEDULES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_block_to_date(target_date: str, text: str):
    schedules = load_schedules()
    day    = schedules.setdefault(target_date, {"blocks": [], "todos": []})
    blocks = day.setdefault("blocks", [])
    last_end = max((b.get("end_min", 0) for b in blocks), default=9 * 60)
    start = max(last_end, 9 * 60)
    blocks.append({
        "id":          uuid.uuid4().hex[:8],
        "text":        text,
        "start_min":   start,
        "end_min":     start + 60,
        "color":       color_for(text),
        "done":        False,
        "skip_reason": "",
    })
    save_schedules(schedules)


class BacklogApp:
    def __init__(self, parent=None, win=None):
        self._embedded = parent is not None
        if self._embedded:
            self.root = parent
            self._win  = win
        else:
            self.root = tk.Tk()
            self._win  = self.root
            self.root.title("📋 积压任务")
            self.root.configure(bg=BG)
            self.root.resizable(False, False)
            try:
                from AppKit import NSApp, NSApplicationActivationPolicyRegular
                NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            self.root.attributes("-topmost", True)

        self._build()
        self._refresh()

        if not self._embedded:
            self.root.mainloop()

    def _build(self):
        # 标题栏
        header = tk.Frame(self.root, bg=BG, pady=10)
        header.pack(fill="x", padx=14)
        tk.Label(header, text="📋 积压",
                 font=("Helvetica Neue", 13, "bold"),
                 bg=BG, fg=TEXT_MAIN).pack(side="left")
        self._count_lbl = tk.Label(header, text="",
                                   font=("Helvetica Neue", 11),
                                   bg=BG, fg=TEXT_SUB)
        self._count_lbl.pack(side="left", padx=6)
        hint = tk.Label(header, text="右键操作",
                        font=("Helvetica Neue", 9), bg=BG, fg="#CBD5E1")
        hint.pack(side="right", padx=(0, 4))
        tk.Button(header, text="↺", font=("Helvetica Neue", 13),
                  bg=BG, fg=ACCENT, relief="flat", cursor="hand2",
                  command=self._refresh).pack(side="right")

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # 滚动区域
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)

        self._frame = tk.Frame(self._canvas, bg=BG)
        self._canvas_win = self._canvas.create_window((0, 0), window=self._frame, anchor="nw")
        self._frame.bind("<Configure>", lambda _: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(
            self._canvas_win, width=e.width))

        # 双指滚动（三层保险，同 timeline 实现）
        def _scroll(event):
            if event.delta != 0:
                self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self._scroll_handler = _scroll
        self._canvas.bind("<MouseWheel>", _scroll)
        self._win.bind_all("<MouseWheel>", _scroll)

        self._scroll_monitor = None
        try:
            import AppKit
            def _ns_scroll(ns_event):
                dy = ns_event.scrollingDeltaY()
                if abs(dy) > 0.5:
                    self._canvas.after(0, lambda: self._canvas.yview_scroll(
                        -1 if dy > 0 else 1, "units"))
                return ns_event
            self._scroll_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskScrollWheel, _ns_scroll)
        except Exception:
            pass

    def _refresh(self):
        for w in self._frame.winfo_children():
            w.destroy()

        items = load_pending()
        self._count_lbl.config(text=f"{len(items)} 项" if items else "")

        if not items:
            tk.Label(self._frame, text="✅ 没有积压任务",
                     font=("Helvetica Neue", 13),
                     bg=BG, fg=TEXT_SUB, pady=40).pack()
            return

        for i, item in enumerate(items):
            row_bg = ROW_ODD if i % 2 == 0 else ROW_EVEN
            row = tk.Frame(self._frame, bg=row_bg, pady=10, padx=12, cursor="hand2")
            row.pack(fill="x")

            tk.Frame(row, bg=color_for(item["text"]),
                     width=3, height=32).pack(side="left", padx=(0, 10))

            info = tk.Frame(row, bg=row_bg)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=item["text"],
                     font=("Helvetica Neue", 12, "bold"),
                     bg=row_bg, fg=TEXT_MAIN, anchor="w").pack(fill="x")
            meta = f"原定 {item.get('original_date', '?')}"
            if item.get("skip_reason"):
                meta += f"  ·  {item['skip_reason']}"
            tk.Label(info, text=meta,
                     font=("Helvetica Neue", 10),
                     bg=row_bg, fg=TEXT_SUB, anchor="w").pack(fill="x")

            tk.Frame(self._frame, bg=BORDER, height=1).pack(fill="x")

            # 右键菜单 + 滚动绑定（绑在 row 和所有子控件上）
            it = dict(item)
            def _show_menu(event, x=it):
                menu = tk.Menu(self.root, tearoff=0, bg=BG, fg=TEXT_MAIN,
                               activebackground=ACCENT, activeforeground="white",
                               font=("Helvetica Neue", 12), relief="flat", bd=1)
                menu.add_command(label="📅  排入日程", command=lambda: self._schedule(x))
                menu.add_separator()
                menu.add_command(label="🗑  删除", foreground=RED,
                                 command=lambda: self._delete(x))
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()

            def _bind_row(widget):
                widget.bind("<Button-2>", _show_menu)
                widget.bind("<Button-3>", _show_menu)
                widget.bind("<MouseWheel>", self._scroll_handler)
                for child in widget.winfo_children():
                    _bind_row(child)

            _bind_row(row)

    def _ask_date(self) -> str | None:
        today    = date.today()
        tomorrow = today + timedelta(days=1)
        hint = (f"输入日期（YYYY-MM-DD）\n"
                f"今天 {today.isoformat()}  明天 {tomorrow.isoformat()}")
        self._win.attributes("-topmost", False)
        try:
            raw = simpledialog.askstring("排入哪天？", hint,
                                         initialvalue=tomorrow.isoformat(),
                                         parent=self._win)
        finally:
            self._win.attributes("-topmost", True)
        if raw is None:
            return None
        raw = raw.strip()
        if raw in ("今天", "today", "t"):
            return today.isoformat()
        if raw in ("明天", "tomorrow", "tm"):
            return tomorrow.isoformat()
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            messagebox.showerror("格式错误", "请输入 YYYY-MM-DD，如 2026-03-25",
                                 parent=self._win)
            return None

    def _schedule(self, item: dict):
        target = self._ask_date()
        if not target:
            return
        add_block_to_date(target, item["text"])
        items = load_pending()
        items = [x for x in items
                 if x.get("id") != item.get("id") and x.get("text") != item.get("text")]
        save_pending(items)
        self._refresh()

    def _delete(self, item: dict):
        self._win.attributes("-topmost", False)
        try:
            ok = messagebox.askyesno(
                "确认删除",
                f"从积压列表删除：\n「{item['text']}」\n\n（不会排入任何日程）",
                parent=self._win)
        finally:
            self._win.attributes("-topmost", True)
        if not ok:
            return
        items = load_pending()
        items = [x for x in items
                 if x.get("id") != item.get("id") and x.get("text") != item.get("text")]
        save_pending(items)
        self._refresh()


if __name__ == "__main__":
    BacklogApp()
