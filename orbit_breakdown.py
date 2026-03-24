#!/usr/bin/env python3
"""
Orbit 断点复盘
将 schedules.json 的计划块与 focus.db 的实际记录对齐，
展示每天在哪里断掉了、当时在做什么。
"""

import tkinter as tk
import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

APP_SUPPORT    = Path.home() / "Library" / "Application Support" / "Orbit"
SCHEDULES_FILE = APP_SUPPORT / "schedules.json"
FOCUS_DB       = APP_SUPPORT / "focus.db"

BG        = "#FFFFFF"
CARD_BG   = "#FAFAFA"
MISS_BG   = "#FFF5F5"
MISS_BD   = "#FCA5A5"
DONE_BG   = "#F0FDF4"
DONE_BD   = "#86EFAC"
FG_DARK   = "#334155"
FG_MID    = "#64748B"
FG_LIGHT  = "#94A3B8"
ACCENT    = "#F97316"   # orange for "实际在做"
GREEN     = "#22C55E"
RED       = "#EF4444"
BLUE      = "#3B82F6"
BAR_RED   = "#F87171"
BAR_GREEN = "#86EFAC"
BAR_TRACK = "#E5E7EB"

MIN_OVERLAP_SECS = 5 * 60   # 5 分钟最小有效重叠


def _fmt_dur(secs: int) -> str:
    """将秒数格式化为 Xh Ym 或 Ym。"""
    if secs <= 0:
        return "0m"
    m = secs // 60
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m}m"


def _load_data(target_date: date):
    """
    在后台线程调用。
    返回 dict:
      blocks: list of block dicts (enriched)
      total / done_count / missed_count / diverted_secs
      patterns: {"上午": (断点率, 总块数), "下午": ..., "晚上": ...}
    """
    result = {
        "blocks": [],
        "total": 0, "done_count": 0, "missed_count": 0, "diverted_secs": 0,
        "patterns": {},
        "error": None,
    }
    try:
        # ── 读 schedules.json ────────────────────────────────────────
        if not SCHEDULES_FILE.exists():
            result["error"] = "未找到 schedules.json"
            return result
        raw = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
        date_key = target_date.isoformat()
        day_data = raw.get(date_key, {})
        blocks = day_data.get("blocks", [])

        # ── 读 focus.db ──────────────────────────────────────────────
        entries = []
        if FOCUS_DB.exists():
            conn = sqlite3.connect(str(FOCUS_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT start_time, end_time, duration, category
                FROM entries
                ORDER BY start_time
            """)
            entries = list(cur.fetchall())
            conn.close()

        # 把 entry 的 start/end 统一转成当天分钟（相对 00:00）
        def to_day_mins(ts_str, ref_date):
            """解析 ISO 时间戳，返回 (date, offset_in_minutes_from_midnight)。"""
            try:
                dt = datetime.fromisoformat(ts_str)
                mins = dt.hour * 60 + dt.minute + dt.second / 60
                return dt.date(), mins
            except Exception:
                return None, None

        # 只保留 target_date 当天的 entries
        def entry_range_mins(e):
            d1, s = to_day_mins(e["start_time"], target_date)
            if d1 != target_date:
                return None, None
            if e["end_time"]:
                _, end = to_day_mins(e["end_time"], target_date)
            else:
                dur = e["duration"] or 0
                end = s + dur / 60
            return s, end

        day_entries = []
        for e in entries:
            s, end = entry_range_mins(e)
            if s is not None and end is not None:
                day_entries.append({
                    "start": s,
                    "end": end,
                    "category": e["category"] or "未知",
                    "duration_secs": (end - s) * 60,
                })

        # ── 对每个 block 找重叠 entries ──────────────────────────────
        enriched = []
        for blk in blocks:
            bs = blk.get("start_min", 0)
            be = blk.get("end_min", bs + 30)
            done = bool(blk.get("done", False))

            best_cat = None
            best_overlap = 0
            total_div = 0

            for ent in day_entries:
                # 重叠区间
                overlap_start = max(bs, ent["start"])
                overlap_end   = min(be, ent["end"])
                overlap_secs  = max(0, (overlap_end - overlap_start) * 60)
                if overlap_secs < MIN_OVERLAP_SECS:
                    continue
                total_div += overlap_secs
                if overlap_secs > best_overlap:
                    best_overlap = overlap_secs
                    best_cat = ent["category"]

            enriched.append({
                "start_min": bs,
                "end_min":   be,
                "text":      blk.get("text", "（无标题）"),
                "done":      done,
                "actual_cat":  best_cat,
                "actual_secs": int(best_overlap),
                "diverted_secs": int(total_div) if not done else 0,
            })

        # ── 汇总统计 ─────────────────────────────────────────────────
        total = len(enriched)
        done_count = sum(1 for b in enriched if b["done"])
        missed_count = total - done_count
        diverted_secs = sum(b["diverted_secs"] for b in enriched)

        result["blocks"]        = enriched
        result["total"]         = total
        result["done_count"]    = done_count
        result["missed_count"]  = missed_count
        result["diverted_secs"] = diverted_secs

        # ── 7 天规律（过去 7 个完整日历日，不含 target_date）───────
        slots = {
            "上午": (6 * 60,  12 * 60),
            "下午": (12 * 60, 18 * 60),
            "晚上": (18 * 60, 24 * 60),
        }
        slot_stats = {k: {"total": 0, "missed": 0} for k in slots}

        for offset in range(1, 8):
            past_date = target_date - timedelta(days=offset)
            past_key  = past_date.isoformat()
            past_day  = raw.get(past_key, {})
            past_blks = past_day.get("blocks", [])
            for blk in past_blks:
                bs = blk.get("start_min", 0)
                for slot_name, (slot_s, slot_e) in slots.items():
                    if bs >= slot_s and bs < slot_e:
                        slot_stats[slot_name]["total"] += 1
                        if not blk.get("done", False):
                            slot_stats[slot_name]["missed"] += 1

        patterns = {}
        for slot_name, st in slot_stats.items():
            if st["total"] > 0:
                pct = round(st["missed"] / st["total"] * 100)
            else:
                pct = None
            patterns[slot_name] = (pct, st["total"])

        result["patterns"] = patterns

    except Exception as e:
        result["error"] = str(e)

    return result


class BreakdownApp:
    def __init__(self, parent: tk.Frame, win: tk.Tk):
        self._parent = parent
        self._win    = win
        self._date   = date.today()
        self._loading = False

        parent.configure(bg=BG)
        self._build_ui()
        self._setup_scroll_monitor()
        self._refresh()

    # ─────────────────────────────────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        p = self._parent

        # ── 标题行 ───────────────────────────────────────────────────
        hdr = tk.Frame(p, bg=BG)
        hdr.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(hdr, text="断点复盘",
                 font=("Helvetica Neue", 12, "bold"),
                 fg=FG_DARK, bg=BG, anchor="w").pack(side="left")

        # ── 日期导航 ─────────────────────────────────────────────────
        nav = tk.Frame(p, bg=BG)
        nav.pack(fill="x", padx=10, pady=(0, 6))

        btn_prev = tk.Label(nav, text="◀", font=("Helvetica Neue", 11),
                            fg=FG_LIGHT, bg=BG, cursor="hand2")
        btn_prev.pack(side="left")
        btn_prev.bind("<Button-1>", lambda e: self._go_day(-1))
        btn_prev.bind("<Enter>", lambda e: btn_prev.config(fg=FG_MID))
        btn_prev.bind("<Leave>", lambda e: btn_prev.config(fg=FG_LIGHT))

        self._date_var = tk.StringVar()
        tk.Label(nav, textvariable=self._date_var,
                 font=("Helvetica Neue", 11, "bold"),
                 fg=FG_DARK, bg=BG).pack(side="left", expand=True)

        btn_next = tk.Label(nav, text="▶", font=("Helvetica Neue", 11),
                            fg=FG_LIGHT, bg=BG, cursor="hand2")
        btn_next.pack(side="right")
        btn_next.bind("<Button-1>", lambda e: self._go_day(1))
        btn_next.bind("<Enter>", lambda e: btn_next.config(fg=FG_MID))
        btn_next.bind("<Leave>", lambda e: btn_next.config(fg=FG_LIGHT))

        tk.Frame(p, bg="#E2E8F0", height=1).pack(fill="x", padx=8, pady=(0, 6))

        # ── 4 格汇总 ─────────────────────────────────────────────────
        self._stats_frame = tk.Frame(p, bg=BG)
        self._stats_frame.pack(fill="x", padx=10, pady=(0, 6))

        self._stat_vars = {}
        for col, (key, label, color) in enumerate([
            ("total",   "计划块", FG_DARK),
            ("done",    "完成",   GREEN),
            ("missed",  "断掉",   RED),
            ("diverted","去哪了",  BLUE),
        ]):
            box = tk.Frame(self._stats_frame, bg=BG,
                           relief="solid", bd=1,
                           highlightbackground="#E0E0E0")
            box.grid(row=0, column=col, sticky="ew", padx=3)
            self._stats_frame.columnconfigure(col, weight=1)
            v = tk.StringVar(value="—")
            self._stat_vars[key] = v
            tk.Label(box, textvariable=v,
                     font=("Helvetica Neue", 18, "bold"),
                     fg=color, bg=BG).pack(pady=(4, 0))
            tk.Label(box, text=label,
                     font=("Helvetica Neue", 8),
                     fg=FG_LIGHT, bg=BG).pack(pady=(0, 4))

        # ── 主滚动区 ─────────────────────────────────────────────────
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True, padx=0)

        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0,
                           borderwidth=0)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._scroll_canvas = canvas
        self._scroll_inner  = tk.Frame(canvas, bg=BG)
        self._scroll_window = canvas.create_window(
            (0, 0), window=self._scroll_inner, anchor="nw")

        self._scroll_inner.bind("<Configure>", self._on_inner_configure)
        canvas.bind("<Configure>",   self._on_canvas_configure)
        canvas.bind("<MouseWheel>",  self._on_mousewheel)
        canvas.bind("<Button-4>",    self._on_mousewheel)
        canvas.bind("<Button-5>",    self._on_mousewheel)

        # 内容区
        self._list_frame    = tk.Frame(self._scroll_inner, bg=BG)
        self._list_frame.pack(fill="x", padx=10, pady=(0, 4))

        self._pattern_frame = tk.Frame(self._scroll_inner, bg=BG)
        self._pattern_frame.pack(fill="x", padx=10, pady=(0, 10))

    def _on_inner_configure(self, e):
        self._scroll_canvas.configure(
            scrollregion=self._scroll_canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._scroll_canvas.itemconfig(
            self._scroll_window, width=e.width)

    def _on_mousewheel(self, e):
        if e.num == 4:
            self._scroll_canvas.yview_scroll(-1, "units")
        elif e.num == 5:
            self._scroll_canvas.yview_scroll(1, "units")
        else:
            self._scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _setup_scroll_monitor(self):
        """NSEvent 监听器：拦截 trackpad 双指滚动，与其他页面行为一致。"""
        self._scroll_monitor = None
        try:
            import AppKit
            def _ns_scroll(ns_event):
                dy = ns_event.scrollingDeltaY()
                if abs(dy) > 0.5 and self._parent.winfo_ismapped():
                    self._scroll_canvas.after(
                        0, lambda d=dy: self._scroll_canvas.yview_scroll(
                            -1 if d > 0 else 1, "units"))
                return ns_event
            self._scroll_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskScrollWheel, _ns_scroll)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────
    # 数据刷新
    # ─────────────────────────────────────────────────────────────────
    def _refresh(self):
        """切换到断点复盘页时调用，或翻日期时调用。"""
        today = date.today()
        label = self._date.strftime("%-m月%-d日")
        if self._date == today:
            label += "（今天）"
        elif self._date == today - timedelta(days=1):
            label += "（昨天）"
        self._date_var.set(label)

        # 清空旧内容
        for w in self._list_frame.winfo_children():
            w.destroy()
        for w in self._pattern_frame.winfo_children():
            w.destroy()

        # 显示加载中
        tk.Label(self._list_frame, text="加载中…",
                 font=("Helvetica Neue", 11),
                 fg=FG_LIGHT, bg=BG).pack(pady=20)

        # 重置统计格
        for v in self._stat_vars.values():
            v.set("—")

        # 后台加载
        target = self._date
        t = threading.Thread(target=self._load_and_render,
                             args=(target,), daemon=True)
        t.start()

    def _load_and_render(self, target: date):
        data = _load_data(target)
        # 回到主线程更新 UI
        self._win.after(0, lambda: self._render(data, target))

    def _render(self, data: dict, target: date):
        """主线程渲染。"""
        # 如果用户已切换到别的日期，丢弃旧数据
        if target != self._date:
            return

        for w in self._list_frame.winfo_children():
            w.destroy()
        for w in self._pattern_frame.winfo_children():
            w.destroy()

        if data.get("error"):
            tk.Label(self._list_frame,
                     text=f"加载失败：{data['error']}",
                     font=("Helvetica Neue", 10),
                     fg=RED, bg=BG, wraplength=320).pack(pady=20)
            return

        # ── 统计格 ────────────────────────────────────────────────────
        self._stat_vars["total"].set(str(data["total"]))
        self._stat_vars["done"].set(str(data["done_count"]))
        self._stat_vars["missed"].set(str(data["missed_count"]))
        self._stat_vars["diverted"].set(_fmt_dur(data["diverted_secs"]))

        # ── 断点列表 ──────────────────────────────────────────────────
        if not data["blocks"]:
            tk.Label(self._list_frame,
                     text="当天无计划块",
                     font=("Helvetica Neue", 10),
                     fg=FG_LIGHT, bg=BG).pack(pady=12)
        else:
            # 标签行
            sec_lbl = tk.Label(self._list_frame, text="今天的断点",
                               font=("Helvetica Neue", 9, "bold"),
                               fg=FG_LIGHT, bg=BG, anchor="w")
            sec_lbl.pack(fill="x", pady=(0, 4))

            for blk in data["blocks"]:
                self._render_block(blk)

        # ── 7 天规律 ──────────────────────────────────────────────────
        self._render_patterns(data["patterns"])

    def _render_block(self, blk: dict):
        done = blk["done"]
        bg   = DONE_BG   if done else MISS_BG
        bd   = DONE_BD   if done else MISS_BD

        row = tk.Frame(self._list_frame, bg=bg,
                       relief="solid", bd=1,
                       highlightbackground=bd,
                       highlightthickness=1)
        row.pack(fill="x", pady=2)

        inner = tk.Frame(row, bg=bg)
        inner.pack(fill="x", padx=6, pady=5)

        # 时间列
        h, m = divmod(blk["start_min"], 60)
        time_str = f"{h:02d}:{m:02d}"
        tk.Label(inner, text=time_str,
                 font=("Helvetica Neue", 9),
                 fg=FG_LIGHT, bg=bg, anchor="nw",
                 width=5).pack(side="left", anchor="n")

        # 信息列
        info = tk.Frame(inner, bg=bg)
        info.pack(side="left", fill="x", expand=True)

        tk.Label(info, text=blk["text"],
                 font=("Helvetica Neue", 10, "bold"),
                 fg=FG_DARK, bg=bg, anchor="w").pack(fill="x")

        if not done:
            if blk["actual_cat"]:
                detail = f"实际在做: {blk['actual_cat']}  ({_fmt_dur(blk['actual_secs'])})"
            else:
                detail = "未记录（无 focus 数据覆盖此时段）"
            tk.Label(info, text=detail,
                     font=("Helvetica Neue", 9),
                     fg=ACCENT if blk["actual_cat"] else FG_LIGHT,
                     bg=bg, anchor="w").pack(fill="x")

        # 状态点
        dot_color = GREEN if done else RED
        dot = tk.Canvas(inner, width=8, height=8,
                        bg=bg, highlightthickness=0)
        dot.pack(side="right", anchor="n", pady=2)
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")

    def _render_patterns(self, patterns: dict):
        if not patterns:
            return

        tk.Label(self._pattern_frame, text="近7天规律",
                 font=("Helvetica Neue", 9, "bold"),
                 fg=FG_LIGHT, bg=BG, anchor="w").pack(fill="x", pady=(6, 4))

        box = tk.Frame(self._pattern_frame, bg=CARD_BG,
                       relief="solid", bd=1,
                       highlightbackground="#E0E0E0",
                       highlightthickness=1)
        box.pack(fill="x")

        inner = tk.Frame(box, bg=CARD_BG)
        inner.pack(fill="x", padx=8, pady=6)

        tk.Label(inner, text="哪个时段最容易断",
                 font=("Helvetica Neue", 9, "bold"),
                 fg=FG_MID, bg=CARD_BG, anchor="w").pack(fill="x",
                                                          pady=(0, 4))

        has_data = False
        for slot_name in ["上午", "下午", "晚上"]:
            pct, total = patterns.get(slot_name, (None, 0))

            row = tk.Frame(inner, bg=CARD_BG)
            row.pack(fill="x", pady=2)

            tk.Label(row, text=slot_name,
                     font=("Helvetica Neue", 9),
                     fg=FG_MID, bg=CARD_BG,
                     width=4, anchor="w").pack(side="left")

            track = tk.Frame(row, bg=BAR_TRACK,
                             height=8, relief="flat")
            track.pack(side="left", fill="x", expand=True,
                       padx=(4, 4))
            track.pack_propagate(False)
            track.update_idletasks()

            if pct is not None:
                has_data = True
                bar_color = BAR_GREEN if pct < 50 else BAR_RED
                # 用 Canvas 画比例条
                bar_canvas = tk.Canvas(track, height=8, bg=BAR_TRACK,
                                       highlightthickness=0)
                bar_canvas.pack(fill="both", expand=True)
                bar_canvas.update_idletasks()
                w = bar_canvas.winfo_width() or 180
                fill_w = max(2, int(w * pct / 100))
                bar_canvas.create_rectangle(0, 0, fill_w, 8,
                                            fill=bar_color, outline="")
                pct_txt = f"{pct}%断"
            else:
                pct_txt = "暂无"

            tk.Label(row, text=pct_txt,
                     font=("Helvetica Neue", 8),
                     fg=FG_LIGHT, bg=CARD_BG,
                     width=6, anchor="e").pack(side="right")

        if not has_data:
            tk.Label(inner, text="过去7天无计划块数据",
                     font=("Helvetica Neue", 9),
                     fg=FG_LIGHT, bg=CARD_BG).pack()

        tk.Label(self._pattern_frame,
                 text="断点 = 有计划块但当时在做别的事情",
                 font=("Helvetica Neue", 8),
                 fg=FG_LIGHT, bg=BG,
                 anchor="w").pack(fill="x", pady=(4, 0))

    # ─────────────────────────────────────────────────────────────────
    # 日期导航
    # ─────────────────────────────────────────────────────────────────
    def _go_day(self, delta: int):
        new_date = self._date + timedelta(days=delta)
        if new_date > date.today():
            return
        self._date = new_date
        self._refresh()
