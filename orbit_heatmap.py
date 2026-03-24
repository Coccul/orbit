#!/usr/bin/env python3
"""Orbit 周热力图 — 每天每小时在做什么（支持独立窗口和嵌入 widget）"""

import tkinter as tk
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Orbit"
DB_PATH     = APP_SUPPORT / "focus.db"

BG    = "#FFFFFF"
TEXT  = "#334155"
MUTED = "#94A3B8"
GRID  = "#E2E8F0"

TASK_COLORS = {
    "📈 交易学习":    "#E07068",
    "🔮 塔罗分析":    "#9880CC",
    "✍️ 写文章/卡片": "#E8956A",
    "💻 折腾/开发":   "#5B8FD4",
    "🗂️ 杂事":        "#94A3B8",
    "🗂️ 系统/杂事":   "#94A3B8",
    "📷 摄影/画廊":   "#81D8D0",
    "😴 睡眠":        "#7090C8",
    "📱 刷手机":      "#7AB0D4",
    "☕ 放松/休息":   "#C68642",
    "🏃 运动":        "#72B87A",
    "🍽️ 吃饭/家务":  "#DDB86A",
    "🧘 冥想":        "#68B868",
    "📔 日记":        "#E8A0BF",
}

HOUR_START = 6
HOUR_END   = 24
LEFT_PAD   = 36
TOP_PAD    = 44
LEGEND_ROW_H = 18
DAYS_ZH    = ["一", "二", "三", "四", "五", "六", "日"]


def _load(start_date: date, end_date: date):
    d_start = start_date.strftime("%Y-%m-%d") + " 00:00"
    d_end   = (end_date + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00"
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT start_time, end_time, duration, category FROM entries "
            "WHERE end_time > ? AND start_time < ? ORDER BY start_time",
            (d_start, d_end)
        ).fetchall()

    slot_minutes = defaultdict(lambda: defaultdict(int))
    for s, e, dur, cat in rows:
        try:
            s_dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
            e_dt = datetime.strptime(e, "%Y-%m-%d %H:%M")
        except Exception:
            continue
        s_dt = max(s_dt, datetime.combine(start_date, datetime.min.time()))
        e_dt = min(e_dt, datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
        if s_dt >= e_dt:
            continue
        cur = s_dt.replace(minute=0, second=0, microsecond=0)
        while cur < e_dt:
            slot_end = cur + timedelta(hours=1)
            overlap  = (min(e_dt, slot_end) - max(s_dt, cur)).total_seconds() / 60
            if overlap > 0:
                slot_minutes[(cur.date(), cur.hour)][cat] += overlap
            cur = slot_end

    result = {}
    for (d, h), cats in slot_minutes.items():
        dominant = max(cats, key=cats.get)
        result[(d, h)] = (dominant, cats[dominant])
    return result


def _blend(hex_color: str, bg_hex: str, frac: float) -> str:
    def parse(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r1, g1, b1 = parse(hex_color)
    r2, g2, b2 = parse(bg_hex)
    f = 0.25 + 0.75 * frac
    r = int(r1 * f + r2 * (1 - f))
    g = int(g1 * f + g2 * (1 - f))
    b = int(b1 * f + b2 * (1 - f))
    return f"#{r:02x}{g:02x}{b:02x}"


class HeatmapApp:
    def __init__(self, parent=None, win=None):
        self._embedded = parent is not None
        self.root = parent if self._embedded else tk.Tk()
        if not self._embedded:
            self.root.title("周天 · 周热力图")
            self.root.configure(bg=BG)
            self.root.resizable(False, False)

        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        self._start = week_start
        self._end   = today

        self._cell_map = {}
        self._build()
        self._draw()

    def _build(self):
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill="both", expand=True)

        # nav bar
        nav = tk.Frame(container, bg=BG)
        nav.pack(fill="x", padx=10, pady=(8, 2))

        tk.Button(nav, text="◀", command=self._prev_week,
                  bg=BG, fg=TEXT, relief="flat",
                  font=("Helvetica Neue", 12), cursor="hand2").pack(side="left")
        self._title_var = tk.StringVar()
        tk.Label(nav, textvariable=self._title_var,
                 bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 11, "bold")).pack(side="left", padx=6)
        tk.Button(nav, text="▶", command=self._next_week,
                  bg=BG, fg=TEXT, relief="flat",
                  font=("Helvetica Neue", 12), cursor="hand2").pack(side="left")

        # canvas — width computed in _draw based on container
        n_hours  = HOUR_END - HOUR_START
        n_legend = len([k for k in TASK_COLORS if k != "🗂️ 系统/杂事"])
        legend_rows = (n_legend + 1) // 2
        canvas_h = TOP_PAD + n_hours * 22 + legend_rows * LEGEND_ROW_H + 20

        self._canvas = tk.Canvas(container, bg=BG, highlightthickness=0,
                                 height=canvas_h)
        self._canvas.pack(fill="x", padx=4, pady=(0, 6))
        self._canvas.bind("<Motion>",   self._on_hover)
        self._canvas.bind("<Leave>",    lambda e: self._tip.place_forget())
        self._canvas.bind("<Configure>", lambda e: self._draw())

        self._tip = tk.Label(container, text="", bg="#334155", fg="white",
                             font=("Helvetica Neue", 10), padx=5, pady=2)

    def _draw(self):
        cv = self._canvas
        cv.delete("all")
        self._cell_map = {}

        self._title_var.set(
            f"{self._start.strftime('%m/%d')} – {self._end.strftime('%m/%d')}"
        )

        cw = cv.winfo_width()
        if cw < 100:
            cw = 340
        cell_w = (cw - LEFT_PAD - 4) // 7
        cell_h = 22
        n_hours = HOUR_END - HOUR_START

        data = _load(self._start, self._end)
        week_dates = [self._start + timedelta(days=i) for i in range(7)]

        # day headers
        for i, d in enumerate(week_dates):
            x = LEFT_PAD + i * cell_w + cell_w // 2
            dow = DAYS_ZH[d.weekday()]
            color = MUTED if d > date.today() else ("#E07068" if d.weekday() >= 5 else TEXT)
            cv.create_text(x, 10, text=dow,
                           fill=color, font=("Helvetica Neue", 9, "bold"))
            cv.create_text(x, 24, text=d.strftime("%-m/%-d"),
                           fill=color, font=("Helvetica Neue", 8))

        # hour rows
        for hi in range(n_hours):
            hour = HOUR_START + hi
            y_top = TOP_PAD + hi * cell_h
            cv.create_text(LEFT_PAD - 4, y_top + cell_h // 2,
                           text=f"{hour:02d}",
                           fill=MUTED, font=("Helvetica Neue", 8), anchor="e")
            cv.create_line(LEFT_PAD, y_top, LEFT_PAD + 7 * cell_w, y_top,
                           fill=GRID, width=1)

        # cells
        for i, d in enumerate(week_dates):
            for hi in range(n_hours):
                hour = HOUR_START + hi
                x0 = LEFT_PAD + i * cell_w + 1
                y0 = TOP_PAD  + hi * cell_h + 1
                x1 = x0 + cell_w - 2
                y1 = y0 + cell_h - 2

                key = (d, hour)
                if key in data:
                    cat, mins = data[key]
                    frac  = min(1.0, mins / 60)
                    color = _blend(TASK_COLORS.get(cat, "#94A3B8"), BG, frac)
                else:
                    cat, mins = None, 0
                    color = "#F1F5F9" if d <= date.today() else "#FAFAFA"

                tag  = f"c_{i}_{hi}"
                cv.create_rectangle(x0, y0, x1, y1,
                                    fill=color, outline="", tags=(tag,))
                self._cell_map[tag] = (d, hour, cat, mins)

        # grid borders
        for i in range(8):
            x = LEFT_PAD + i * cell_w
            cv.create_line(x, TOP_PAD, x, TOP_PAD + n_hours * cell_h,
                           fill=GRID, width=1)
        cv.create_line(LEFT_PAD, TOP_PAD + n_hours * cell_h,
                       LEFT_PAD + 7 * cell_w, TOP_PAD + n_hours * cell_h,
                       fill=GRID, width=1)

        # legend
        items = [(k, v) for k, v in TASK_COLORS.items()
                 if k != "🗂️ 系统/杂事"]
        legend_y = TOP_PAD + n_hours * cell_h + 12
        col_w = (7 * cell_w) // 2
        for idx, (cat, color) in enumerate(items):
            col = idx % 2
            row = idx // 2
            lx = LEFT_PAD + col * col_w
            ly = legend_y + row * LEGEND_ROW_H
            cv.create_rectangle(lx, ly + 2, lx + 9, ly + 11,
                                fill=color, outline="")
            label = cat.split(" ", 1)[-1] if " " in cat else cat
            cv.create_text(lx + 13, ly + 6, text=label,
                          fill=TEXT, font=("Helvetica Neue", 8), anchor="w")

        # store cell_h for hover
        self._cell_h = cell_h
        self._cell_w = cell_w

    def _on_hover(self, event):
        items = self._canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in items:
            for tag in self._canvas.gettags(item):
                if tag in self._cell_map:
                    d, hour, cat, mins = self._cell_map[tag]
                    if cat:
                        m = int(mins)
                        self._tip.config(
                            text=f"{d.strftime('%-m/%-d')} {hour:02d}:00  {cat}  {m}min")
                        self._tip.place(x=event.x + 8, y=event.y - 22)
                        return
        self._tip.place_forget()

    def _prev_week(self):
        self._start -= timedelta(days=7)
        self._end    = self._start + timedelta(days=6)
        self._draw()

    def _next_week(self):
        new_start = self._start + timedelta(days=7)
        if new_start <= date.today():
            self._start = new_start
            self._end   = min(self._start + timedelta(days=6), date.today())
            self._draw()

    def refresh(self):
        self._draw()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    HeatmapApp().run()
