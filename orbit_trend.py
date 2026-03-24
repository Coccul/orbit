#!/usr/bin/env python3
"""
Orbit 趋势图
- 近 7 / 14 / 30 天各分类每日时长折线图
- 鼠标悬停显示当天数据
"""

import tkinter as tk
import sqlite3
from datetime import date, timedelta, datetime
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Orbit"
DB_PATH     = APP_SUPPORT / "focus.db"

# ── 配色 ─────────────────────────────────────────────────────────────
BG    = "#FFFFFF"
GRID  = "#E2E8F0"
TEXT  = "#334155"
MUTED = "#94A3B8"
PANEL = "#F8FAFC"

# 关注的分类及颜色
LINES = [
    ("Study",       ["📈 交易学习", "🔮 塔罗分析", "✍️ 写文章/卡片"],                 "#E07068"),
    ("工作",        ["💻 折腾/开发", "📷 摄影/画廊"],                                  "#5B8FD4"),
    ("😴 睡眠",      ["😴 睡眠"],                                                     "#6C5CE7"),
    ("☕ 放松",      ["☕ 放松/休息"],                                                 "#8B5A38"),
    ("📱 刷手机",    ["📱 刷手机"],                                                    "#7AB0D4"),
    ("🍽️ 吃饭/家务", ["🍽️ 吃饭/家务"],                                               "#DDB86A"),
]

PAD_L, PAD_R, PAD_T, PAD_B = 70, 30, 40, 60
DOT_R = 4


class TrendApp:
    def __init__(self, parent=None, win=None):
        self._embedded = parent is not None
        if self._embedded:
            self.root = parent
        else:
            self.root = tk.Tk()
            self.root.title("周天 · 时间趋势")
            self.root.configure(bg=BG)
            self.root.resizable(True, True)

        self.days    = tk.IntVar(value=14)
        self.hover_x = None
        self.visible = {li: True for li in range(len(LINES))}

        self._build_ui()
        self._load_and_draw()

        if not self._embedded:
            self.root.mainloop()

    def refresh(self):
        """切换到此页时刷新数据。"""
        self._load_and_draw()

    def _build_ui(self):
        # ── 顶部工具栏 ─────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=BG, pady=8)
        bar.pack(fill="x", padx=16)

        tk.Label(bar, text="时间趋势", font=("Helvetica Neue", 14, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")

        for label, val in [("7天", 7), ("14天", 14), ("30天", 30)]:
            tk.Radiobutton(bar, text=label, variable=self.days, value=val,
                           bg=BG, fg=TEXT, selectcolor=BG, activebackground=BG,
                           font=("Helvetica Neue", 11),
                           command=self._load_and_draw).pack(side="right", padx=4)

        # ── 图例（横向滚动，可点击切换） ────────────────────────────
        legend_wrap = tk.Frame(self.root, bg=BG)
        legend_wrap.pack(fill="x", padx=16, pady=(0, 2))

        lc = legend_canvas = tk.Canvas(legend_wrap, bg=BG, height=22,
                                       highlightthickness=0)

        def _scroll_left(e):  lc.xview_scroll(-3, "units")
        def _scroll_right(e): lc.xview_scroll(3,  "units")
        def _h_scroll(e):     lc.xview_scroll(int(-e.delta / 20), "units")

        # 先 pack 两端箭头，再让 canvas 填充中间
        arr_l = tk.Label(legend_wrap, text="◀", font=("Helvetica Neue", 11),
                         fg=MUTED, bg=BG, cursor="hand2")
        arr_l.pack(side="left", padx=(0, 2))
        arr_l.bind("<Button-1>", _scroll_left)

        arr_r = tk.Label(legend_wrap, text="▶", font=("Helvetica Neue", 11),
                         fg=MUTED, bg=BG, cursor="hand2")
        arr_r.pack(side="right", padx=(2, 0))
        arr_r.bind("<Button-1>", _scroll_right)

        legend_canvas.pack(side="left", fill="x", expand=True)
        # macOS trackpad 双指横划
        legend_canvas.bind("<Shift-MouseWheel>", _h_scroll)

        legend_inner = tk.Frame(legend_canvas, bg=BG)
        legend_canvas.create_window((0, 0), window=legend_inner, anchor="nw")
        legend_inner.bind("<Configure>",
                          lambda e: legend_canvas.configure(
                              scrollregion=legend_canvas.bbox("all")))

        self._legend_dots   = []
        self._legend_labels = []

        for li, (name, _, color) in enumerate(LINES):
            item = tk.Frame(legend_inner, bg=BG, cursor="hand2")
            item.pack(side="left", padx=(4, 10))

            dot = tk.Canvas(item, width=12, height=12, bg=BG, highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, fill=color, outline="", tags="dot")
            dot.pack(side="left", padx=(0, 4))

            lbl = tk.Label(item, text=name, font=("Helvetica Neue", 11),
                           bg=BG, fg=TEXT)
            lbl.pack(side="left")

            self._legend_dots.append((dot, color))
            self._legend_labels.append(lbl)

            for w in (item, dot, lbl):
                w.bind("<Button-1>", lambda e, i=li: self._toggle_line(i))

        # ── 主图 Canvas ──────────────────────────────────────────────
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        self.canvas.bind("<Motion>",    self._on_motion)
        self.canvas.bind("<Leave>",     self._on_leave)
        self.canvas.bind("<Configure>", lambda e: self._draw())

    # ── 切换折线显示 ─────────────────────────────────────────────────
    def _toggle_line(self, li):
        self.visible[li] = not self.visible[li]
        dot_canvas, color = self._legend_dots[li]
        lbl = self._legend_labels[li]
        if self.visible[li]:
            dot_canvas.itemconfig("dot", fill=color)
            lbl.config(fg=TEXT, font=("Helvetica Neue", 11))
        else:
            dot_canvas.itemconfig("dot", fill="#CBD5E1")
            lbl.config(fg=MUTED, font=("Helvetica Neue", 11, "overstrike"))
        self._draw()

    # ── 数据加载 ─────────────────────────────────────────────────────
    def _load_and_draw(self):
        n = self.days.get()
        today = date.today()
        self.dates = [today - timedelta(days=n - 1 - i) for i in range(n)]

        # 用 timeline 15-min cell 逻辑计算每天各分类时长
        start_str = self.dates[0].isoformat()
        end_str   = (self.dates[-1] + timedelta(days=1)).isoformat()
        with sqlite3.connect(DB_PATH) as c:
            rows = c.execute(
                "SELECT start_time, end_time, duration, category FROM entries "
                "WHERE end_time > ? AND start_time < ? ORDER BY start_time, id",
                (start_str, end_str)
            ).fetchall()

        # 按天分组：含跨午夜条目，裁剪到当天边界后再做 15-min slot 统计
        self.data: dict[str, dict[str, float]] = {}
        for d in self.dates:
            d_str   = d.isoformat()
            d_start = d_str + " 00:00"
            d_end   = (d + timedelta(days=1)).isoformat() + " 00:00"
            clipped = []
            for r in rows:
                s = max(r[0], d_start)
                e = min(r[1], d_end)
                if s < e:
                    clipped.append((s, e, r[2], r[3]))
            self.data[d_str] = _timeline_stats(clipped)

        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")

        W = c.winfo_width()
        H = c.winfo_height()
        if W < 100 or H < 100:
            return

        n     = len(self.dates)
        cw    = (W - PAD_L - PAD_R) / max(n - 1, 1)   # 列宽
        ch    = H - PAD_T - PAD_B                       # 图区高

        # 最大值只考虑可见的线
        all_vals = []
        for li, (_, cats, _) in enumerate(LINES):
            if not self.visible[li]:
                continue
            for d in self.dates:
                day_data = self.data.get(d.isoformat(), {})
                all_vals.append(sum(day_data.get(cat, 0) for cat in cats) / 60)
        max_h = max(max(all_vals) if all_vals else 10, 1)
        max_h = (int(max_h) + 1)

        def gy(hours):
            return PAD_T + ch * (1 - hours / max_h)

        def gx(i):
            return PAD_L + i * cw

        # ── 网格 & Y 轴 ─────────────────────────────────────────
        steps = 6
        for i in range(steps + 1):
            h = max_h * i / steps
            y = gy(h)
            c.create_line(PAD_L, y, W - PAD_R, y, fill=GRID, width=1)
            c.create_text(PAD_L - 8, y, text=f"{h:.0f}h",
                          anchor="e", font=("SF Pro Display", 10), fill=MUTED)

        # ── X 轴日期 ────────────────────────────────────────────
        step = max(1, n // 10)
        for i, d in enumerate(self.dates):
            if i % step == 0 or i == n - 1:
                x = gx(i)
                label = f"{d.month}/{d.day}"
                c.create_text(x, H - PAD_B + 14, text=label,
                              font=("SF Pro Display", 10), fill=MUTED)
                c.create_line(x, PAD_T, x, H - PAD_B, fill=GRID, width=1, dash=(2, 4))

        # ── 折线 ────────────────────────────────────────────────
        self._dots = {}   # (line_idx, day_idx) → (x, y, hours)
        for li, (name, cats, color) in enumerate(LINES):
            if not self.visible[li]:
                continue
            pts = []
            for i, d in enumerate(self.dates):
                day_data = self.data.get(d.isoformat(), {})
                mins = sum(day_data.get(cat, 0) for cat in cats)
                hrs  = mins / 60
                x, y = gx(i), gy(hrs)
                pts.append((x, y))
                self._dots[(li, i)] = (x, y, hrs)

            # 线段
            for i in range(len(pts) - 1):
                c.create_line(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1],
                              fill=color, width=2, smooth=True)
            # 点
            for x, y in pts:
                c.create_oval(x - DOT_R, y - DOT_R, x + DOT_R, y + DOT_R,
                              fill=color, outline=BG, width=2)

        # ── 悬停竖线 ────────────────────────────────────────────
        if self.hover_x is not None and 0 <= self.hover_x < n:
            x = gx(self.hover_x)
            c.create_line(x, PAD_T, x, H - PAD_B, fill="#CBD5E1", width=1)
            # 高亮各点
            for li, (_, _, color) in enumerate(LINES):
                key = (li, self.hover_x)
                if key in self._dots:
                    px, py, _ = self._dots[key]
                    c.create_oval(px - DOT_R - 2, py - DOT_R - 2,
                                  px + DOT_R + 2, py + DOT_R + 2,
                                  fill=color, outline=BG, width=2)

    # ── 交互 ─────────────────────────────────────────────────────────
    def _on_motion(self, event):
        n  = len(self.dates)
        W  = self.canvas.winfo_width()
        cw = (W - PAD_L - PAD_R) / max(n - 1, 1)
        xi = round((event.x - PAD_L) / cw) if cw > 0 else -1
        xi = max(0, min(xi, n - 1))
        if event.x < PAD_L - 10 or event.x > W - PAD_R + 10:
            self._on_leave(None)
            return
        if xi != self.hover_x:
            self.hover_x = xi
            self._draw()
        d = self.dates[xi]
        day_data = self.data.get(d.isoformat(), {})
        lines = [f"  {d.month}/{d.day}（{['一','二','三','四','五','六','日'][d.weekday()]}）"]
        for li, (name, cats, _) in enumerate(LINES):
            if not self.visible[li]:
                continue
            mins = sum(day_data.get(cat, 0) for cat in cats)
            if mins > 0:
                h, m = divmod(int(mins), 60)
                lines.append(f"  {name}  {h}h {m}m")
        self._draw_tip(event.x, event.y, lines)

    def _draw_tip(self, mx, my, lines):
        """在 canvas 上直接绘制 tooltip，避免 Toplevel 闪烁。"""
        c = self.canvas
        c.delete("tip")
        if not lines:
            return
        text = "\n".join(lines)
        W = c.winfo_width()
        # 先量文字宽高
        tmp = c.create_text(0, 0, text=text, anchor="nw",
                            font=("Helvetica Neue", 11), tags="tip_tmp")
        bb = c.bbox(tmp)
        c.delete(tmp)
        if not bb:
            return
        tw, th = bb[2] - bb[0] + 20, bb[3] - bb[1] + 16
        tx = mx + 14
        if tx + tw > W - 4:
            tx = mx - tw - 10
        ty = my - th // 2
        ty = max(4, ty)
        c.create_rectangle(tx, ty, tx + tw, ty + th,
                           fill="#1E293B", outline="", tags="tip")
        c.create_text(tx + 10, ty + 8, text=text, anchor="nw",
                      font=("Helvetica Neue", 11), fill="white",
                      justify="left", tags="tip")

    def _on_leave(self, _):
        self.hover_x = None
        self.canvas.delete("tip")
        self._draw()


# ── Timeline stats（同 orbit_guard.py 逻辑） ──────────────────────────
def _timeline_stats(rows):
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
    # 只统计当天 00:00–24:00，避免跨午夜条目被隔天重复计算
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
    TrendApp()
