#!/usr/bin/env python3
"""Orbit 统计 — 甜甜圈图 + 日期范围选择"""

import tkinter as tk
import sqlite3, math, calendar
from datetime import date, timedelta, datetime
from pathlib import Path
from collections import defaultdict

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Orbit"
DB_PATH     = APP_SUPPORT / "focus.db"

BG    = "#FFFFFF"
TEXT  = "#334155"
MUTED = "#94A3B8"
GRID  = "#E2E8F0"
BLUE  = "#5B8FD4"

TASK_COLORS = {
    "📈 交易学习":    "#E07068",
    "🔮 塔罗分析":    "#9880CC",
    "✍️ 写文章/卡片": "#E8956A",
    "💻 折腾/开发":   "#5B8FD4",
    "🗂️ 杂事":        "#64748B",
    "🗂️ 系统/杂事":   "#64748B",
    "📷 摄影/画廊":   "#81D8D0",
    "😴 睡眠":        "#7090C8",
    "📱 刷手机":      "#7AB0D4",
    "☕ 放松/休息":   "#8B5A38",
    "🏃 运动":        "#72B87A",
    "🍽️ 吃饭/家务":  "#DDB86A",
    "🧘 冥想":        "#68B868",
    "📔 日记":        "#E8A0BF",
}

RANGES = ["今天", "本周", "当月", "本季", "最近7天", "最近30天"]
WDAYS  = ["一", "二", "三", "四", "五", "六", "日"]


def _cat_color(cat):
    return TASK_COLORS.get(cat, "#94A3B8")


class StatsApp:
    def __init__(self, parent=None, win=None):
        self._embedded = parent is not None
        self.root = parent if self._embedded else tk.Tk()
        if not self._embedded:
            self.root.title("周天 · 统计")
            self.root.configure(bg=BG)
            self.root.resizable(True, True)

        self._range    = "今天"
        self._offset   = 0
        self._tab      = "category"   # "category" | "content"
        self._picker   = None
        self._slices   = []
        self._donut    = (0, 0, 0, 0)   # cx cy r_out r_in

        self._build()
        self._load_and_draw()

        if not self._embedded:
            self.root.mainloop()

    def refresh(self):
        self._offset = 0
        self._load_and_draw()

    # ── 日期范围计算 ──────────────────────────────────────────────────
    def _date_range(self):
        today = date.today()
        off   = self._offset
        r     = self._range

        if r == "今天":
            d = today + timedelta(days=off)
            return d, d
        if r == "本周":
            mon = today - timedelta(days=today.weekday())
            mon += timedelta(weeks=off)
            return mon, mon + timedelta(days=6)
        if r == "当月":
            m = today.month + off
            y = today.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            last = calendar.monthrange(y, m)[1]
            return date(y, m, 1), date(y, m, last)
        if r == "本季":
            q = (today.month - 1) // 3 + off
            y = today.year + q // 4
            q = q % 4
            sm = q * 3 + 1
            em = sm + 2
            last = calendar.monthrange(y, em)[1]
            return date(y, sm, 1), date(y, em, last)
        if r == "今年":
            y = today.year + off
            return date(y, 1, 1), date(y, 12, 31)
        if r == "最近7天":
            end = today + timedelta(days=off * 7)
            return end - timedelta(days=6), end
        if r == "最近30天":
            end = today + timedelta(days=off * 30)
            return end - timedelta(days=29), end
        if r == "有史以来":
            return date(2020, 1, 1), today
        return today, today

    def _date_label(self):
        start, end = self._date_range()
        r = self._range
        if r == "今天":
            suffix = "（今天）" if start == date.today() else ""
            return f"{start.month}月{start.day}日，周{WDAYS[start.weekday()]}{suffix}"
        if start == end:
            return f"{start.month}月{start.day}日"
        if start.year == end.year:
            return f"{start.year}年 {start.month}/{start.day} – {end.month}/{end.day}"
        return f"{start} – {end}"

    # ── UI 构建 ───────────────────────────────────────────────────────
    def _build(self):
        # ── 范围选择行 ────────────────────────────────────────────
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=12, pady=(8, 0))

        self._range_btn = tk.Label(
            top, text=self._range + " ▾",
            font=("Helvetica Neue", 11), fg=BLUE, bg=BG, cursor="hand2")
        self._range_btn.pack(side="left")
        self._range_btn.bind("<Button-1>", self._toggle_picker)

        # ── 日期导航行 ────────────────────────────────────────────
        nav = tk.Frame(self.root, bg=BG)
        nav.pack(fill="x", padx=12, pady=(2, 0))

        arr_l = tk.Label(nav, text="◀", font=("Helvetica Neue", 12),
                         fg=MUTED, bg=BG, cursor="hand2")
        arr_l.pack(side="left")
        arr_l.bind("<Button-1>", lambda e: self._navigate(-1))

        self._date_lbl = tk.Label(nav, text="",
                                   font=("Helvetica Neue", 12, "bold"),
                                   fg=TEXT, bg=BG)
        self._date_lbl.pack(side="left", expand=True)

        arr_r = tk.Label(nav, text="▶", font=("Helvetica Neue", 12),
                         fg=MUTED, bg=BG, cursor="hand2")
        arr_r.pack(side="right")
        arr_r.bind("<Button-1>", lambda e: self._navigate(1))

        tk.Frame(self.root, bg=GRID, height=1).pack(fill="x", padx=12, pady=(6, 0))

        # ── Canvas ────────────────────────────────────────────────
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=6)
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Motion>",    self._on_motion)
        self.canvas.bind("<Leave>",     lambda e: (self.canvas.delete("tip"), self._draw()))

        # ── 下拉覆盖层（place 在 root 上，初始隐藏） ─────────────
        self._picker = tk.Frame(self.root, bg="#F8FAFC",
                                relief="solid", bd=1)
        self._picker_rows = []
        for r in RANGES:
            lbl = tk.Label(self._picker, text=r,
                           font=("Helvetica Neue", 12),
                           bg="#F8FAFC", padx=16, pady=8, anchor="w")
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, rr=r: self._select_range(rr))
            lbl.bind("<Enter>", lambda e, l=lbl: l.config(bg="#EFF6FF"))
            lbl.bind("<Leave>", lambda e, l=lbl: l.config(bg="#F8FAFC"))
            self._picker_rows.append((r, lbl))
        self._picker_visible = False

    # ── 范围选择下拉 ──────────────────────────────────────────────────
    def _toggle_picker(self, event=None):
        if self._picker_visible:
            self._picker.place_forget()
            self._picker_visible = False
        else:
            self._refresh_picker_colors()
            self._picker.lift()
            self._picker.place(x=8, y=36, width=160)
            self._picker_visible = True

    def _refresh_picker_colors(self):
        for r, lbl in self._picker_rows:
            lbl.config(fg=BLUE if r == self._range else TEXT, bg="#F8FAFC")

    def _select_range(self, r):
        self._range = r
        self._offset = 0
        self._range_btn.config(text=r + " ▾")
        self._picker.place_forget()
        self._picker_visible = False
        self._load_and_draw()

    def _navigate(self, delta):
        self._offset += delta
        self._load_and_draw()

    def _set_tab(self, key):
        self._tab = key
        self._refresh_tab_style()
        self._draw()

    def _refresh_tab_style(self):
        for key, btn in self._tab_btns.items():
            btn.config(fg=BLUE if key == self._tab else MUTED)

    # ── 数据加载 ──────────────────────────────────────────────────────
    def _load_and_draw(self):
        start, end = self._date_range()
        self._date_lbl.config(text=self._date_label())

        end_str = (end + timedelta(days=1)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            self._rows = conn.execute(
                "SELECT start_time, end_time, duration, category FROM entries "
                "WHERE end_time > ? AND start_time < ? ORDER BY start_time",
                (start.isoformat(), end_str)
            ).fetchall()

        # 按天裁剪后用 15-min slot 去重（含跨午夜条目）
        self._cat_mins = defaultdict(float)
        cur = start
        while cur <= end:
            d_str   = cur.isoformat()
            d_start = d_str + " 00:00"
            d_end   = (cur + timedelta(days=1)).isoformat() + " 00:00"
            clipped = []
            for r in self._rows:
                s = max(r[0], d_start)
                e = min(r[1], d_end)
                if s < e:
                    clipped.append((s, e, r[2], r[3]))
            for cat, mins in _timeline_stats(clipped).items():
                self._cat_mins[cat] += mins
            cur += timedelta(days=1)

        self._draw()

    # ── 绘制分发 ─────────────────────────────────────────────────────
    def _draw(self):
        c = self.canvas
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        if W < 60 or H < 60:
            return
        if self._tab == "category":
            self._draw_donut(W, H)
        else:
            self._draw_content(W, H)

    # ── 甜甜圈 ───────────────────────────────────────────────────────
    def _draw_donut(self, W, H):
        c = self.canvas
        cat_data = sorted(
            [(cat, mins, _cat_color(cat))
             for cat, mins in self._cat_mins.items() if mins > 0],
            key=lambda x: -x[1])

        if not cat_data:
            c.create_text(W // 2, H // 2, text="暂无数据",
                          font=("Helvetica Neue", 14), fill=MUTED)
            return

        total = sum(m for _, m, _ in cat_data)

        # 图例高度（两列）
        cols = 2
        rows_n = (len(cat_data) + cols - 1) // cols
        legend_h = rows_n * 22 + 8
        chart_h  = max(H - legend_h - 16, 80)

        cx    = W // 2
        cy    = chart_h // 2 + 8
        r_out = min(W - 40, chart_h - 16) // 2
        r_in  = int(r_out * 0.52)
        self._donut = (cx, cy, r_out, r_in)

        self._slices = []
        start = 90.0
        for cat, mins, color in cat_data:
            extent = -(mins / total * 360)
            c.create_arc(cx - r_out, cy - r_out, cx + r_out, cy + r_out,
                         start=start, extent=extent,
                         fill=color, outline=BG, width=2, style="pieslice")
            mid_deg = start + extent / 2
            mid_rad = math.radians(mid_deg)
            label_r = (r_out + r_in) / 2
            lx = cx + label_r * math.cos(mid_rad)
            ly = cy - label_r * math.sin(mid_rad)
            pct = mins / total * 100
            if pct >= 7:
                c.create_text(lx, ly, text=f"{pct:.0f}%",
                              font=("Helvetica Neue", 9, "bold"),
                              fill="white", anchor="center")
            self._slices.append((start, start + extent, cat, mins, color))
            start += extent

        # 空洞
        c.create_oval(cx - r_in, cy - r_in, cx + r_in, cy + r_in,
                      fill=BG, outline="")
        # 中心文字
        total_h, total_m = divmod(int(total), 60)
        c.create_text(cx, cy - 9,
                      text=f"{total_h}h {total_m:02d}m",
                      font=("Helvetica Neue", 13, "bold"), fill=TEXT)
        c.create_text(cx, cy + 9, text="总计",
                      font=("Helvetica Neue", 9), fill=MUTED)

        # 图例
        col_w = W // cols
        for i, (cat, mins, color) in enumerate(cat_data):
            row_i, col_i = divmod(i, cols)
            x = col_i * col_w + 12
            y = chart_h + 8 + row_i * 22
            c.create_oval(x, y + 3, x + 9, y + 12, fill=color, outline="")
            h, m = divmod(int(mins), 60)
            c.create_text(x + 13, y + 7,
                          text=f"{cat}  {h}h{m:02d}m",
                          anchor="w", font=("Helvetica Neue", 10), fill=TEXT)

    # ── 事件内容列表 ──────────────────────────────────────────────────
    def _draw_content(self, W, H):
        c = self.canvas
        if not self._rows:
            c.create_text(W // 2, H // 2, text="暂无数据",
                          font=("Helvetica Neue", 14), fill=MUTED)
            return

        sorted_rows = sorted(self._rows, key=lambda r: -(r[2] or 0))
        y = 8
        for start_t, end_t, dur, cat in sorted_rows:
            if y > H:
                break
            color = _cat_color(cat)
            h, m = divmod(int(dur or 0), 60)
            time_str = f"{h}h {m:02d}m" if h else f"{m}m"
            c.create_oval(10, y + 4, 18, y + 13, fill=color, outline="")
            c.create_text(24, y + 8, text=cat, anchor="w",
                          font=("Helvetica Neue", 11), fill=TEXT)
            c.create_text(W - 10, y + 8, text=time_str, anchor="e",
                          font=("Helvetica Neue", 11), fill=MUTED)
            time_range = f"{start_t[11:16]} – {end_t[11:16]}"
            c.create_text(24, y + 22, text=time_range, anchor="w",
                          font=("Helvetica Neue", 9), fill=MUTED)
            c.create_line(10, y + 32, W - 10, y + 32, fill=GRID, width=1)
            y += 36

    # ── 悬停 ─────────────────────────────────────────────────────────
    def _on_motion(self, event):
        if self._tab != "category" or not self._slices:
            return
        cx, cy, r_out, r_in = self._donut
        dx, dy = event.x - cx, event.y - cy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < r_in or dist > r_out:
            self.canvas.delete("tip")
            return
        angle = math.degrees(math.atan2(-dy, dx)) % 360
        for s_deg, e_deg, cat, mins, color in self._slices:
            s = s_deg % 360
            e = e_deg % 360
            if s > e:
                hit = e <= angle <= s
            else:
                hit = angle >= e or angle <= s
            if hit:
                h, m = divmod(int(mins), 60)
                total = sum(sl[3] for sl in self._slices)
                pct = mins / total * 100
                self._draw_tip(event.x, event.y,
                               [f"  {cat}", f"  {h}h {m:02d}m  ({pct:.1f}%)"])
                return
        self.canvas.delete("tip")

    def _draw_tip(self, mx, my, lines):
        c = self.canvas
        c.delete("tip")
        text = "\n".join(lines)
        W = c.winfo_width()
        tmp = c.create_text(0, 0, text=text, anchor="nw",
                            font=("Helvetica Neue", 11), tags="tip_tmp")
        bb = c.bbox(tmp)
        c.delete(tmp)
        if not bb:
            return
        tw = bb[2] - bb[0] + 20
        th = bb[3] - bb[1] + 16
        tx = mx + 14
        if tx + tw > W - 4:
            tx = mx - tw - 10
        ty = max(4, my - th // 2)
        c.create_rectangle(tx, ty, tx + tw, ty + th,
                           fill="#1E293B", outline="", tags="tip")
        c.create_text(tx + 10, ty + 8, text=text, anchor="nw",
                      font=("Helvetica Neue", 11), fill="white",
                      justify="left", tags="tip")


def _timeline_stats(rows):
    """15-min slot 去重统计，避免重叠条目被重复计算。"""
    if not rows:
        return {}
    from datetime import timedelta as td
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
    scan_end   = min(max(e for s, e, c in parsed), scan_start + td(hours=24))
    cat_mins   = {}
    t = scan_start
    while t < scan_end:
        t_end = t + td(minutes=15)
        best_cat, best_ov = None, td(0)
        for s_dt, e_dt, cat in parsed:
            ov = min(e_dt, t_end) - max(s_dt, t)
            if ov > td(0) and ov >= best_ov:
                best_ov, best_cat = ov, cat
        if best_cat:
            cat_mins[best_cat] = cat_mins.get(best_cat, 0) + 15
        t = t_end
    return cat_mins


if __name__ == "__main__":
    StatsApp()
