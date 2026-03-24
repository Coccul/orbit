#!/usr/bin/env python3
"""Orbit 时间块视图 — 拖选补录模式"""
import tkinter as tk
import sqlite3, json, calendar, re
from datetime import datetime, date, timedelta
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Orbit"
DB_PATH     = APP_SUPPORT / "focus.db"
TASKS_FILE  = APP_SUPPORT / "custom_tasks.json"

TASK_COLORS = {
    "📈 交易学习":    "#E07068",
    "🔮 塔罗分析":    "#9880CC",
    "✍️ 写文章/卡片": "#E8956A",
    "💻 折腾/开发":   "#5B8FD4",
    "🗂️ 杂事":       "#64748B",
    "🗂️ 系统/杂事":  "#64748B",
    "📷 摄影/画廊":  "#81D8D0",
    "😴 睡眠":        "#7090C8",
    "📱 刷手机":      "#7AB0D4",
    "☕ 放松/休息":   "#8B5A38",
    "🏃 运动":        "#72B87A",
    "🍽️ 吃饭/家务":  "#DDB86A",
    "🧘 冥想":        "#68B868",
    "📔 日记":        "#E8A0BF",
}

OBSIDIAN_LOG_DIR = Path.home() / "Desktop" / "ob" / "个人版" / "03 工作log"
STUDY_TASKS = {"📈 交易学习", "🔮 塔罗分析", "✍️ 写文章/卡片"}
WORK_TASKS  = {"💻 折腾/开发", "📷 摄影/画廊"}

ROW_H    = 40           # px per hour row
LABEL_W  = 46           # time label column width
BAR_W    = 192          # fill-bar width (dominant area)
RIGHT_W  = 92           # right task panel (emoji grid)
SLOTS    = 96           # 24 × 4 quarter-slots
CELL_W   = BAR_W // 4  # 48px per 15-min column (horizontal)
TOTAL_H  = ROW_H * 24 + 20
GAP_CLR  = "#EBEBEB"    # empty cell gray
DIV_CLR  = "#D8D8D8"    # 15-min column divider
SEL_CLR  = "#BFDBFE"    # light-blue selection fill
SEL_BDR  = "#3B82F6"    # blue selection border
NOW_CLR  = "#EF4444"
WIN_W    = LABEL_W + BAR_W + 16 + 8 + RIGHT_W + 20
WIN_H    = 700
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ── 工作 log 更新（补录后同步） ───────────────────────────────────────────────
def _update_log_for_date(date_str: str):
    """读取 date_str（YYYY-MM-DD）当天全部记录，刷新 Obsidian 工作 log 的数据速览。"""
    _d = date.fromisoformat(date_str)
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

    # 15-min cell 'most overlap wins'
    parsed = []
    for s_str, e_str, _dur, cat in all_rows:
        try:
            s = datetime.strptime(s_str, "%Y-%m-%d %H:%M")
            e = datetime.strptime(e_str, "%Y-%m-%d %H:%M")
            if e > s:
                parsed.append((s, e, cat))
        except Exception:
            pass
    cat_mins: dict = {}
    if parsed:
        scan_start = day_start
        scan_end   = day_end
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

    # 找最长连续块（Study/工作）
    def _main_period(task_set):
        sessions = [(s, e) for s, e, c in parsed if c in task_set]
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

    lines = ["**数据速览**（周天）", "", "| 类别 | 时长 |", "|------|------|"]
    for label, task_set in [("Study", STUDY_TASKS), ("工作", WORK_TASKS)]:
        total = sum(cat_mins.get(t, 0) for t in task_set)
        if total > 0:
            h, m = divmod(total, 60)
            time_str = f"{h}h {m}m" if h else f"{m} min"
            ms, me = _main_period(task_set)
            if ms and me:
                time_str += f"（主力：{ms.strftime('%H:%M')}–{me.strftime('%H:%M')}）"
            lines.append(f"| {label} | {time_str} |")
    life_rows = [(c, m) for c, m in cat_mins.items()
                 if c not in STUDY_TASKS and c not in WORK_TASKS]
    for cat, mins in sorted(life_rows, key=lambda x: -x[1]):
        h, m = divmod(mins, 60)
        lines.append(f"| {cat} | {f'{h}h {m}m' if h else f'{m} min'} |")
    total_all = sum(cat_mins.values())
    th, tm = divmod(total_all, 60)
    lines.append(f"| **合计** | **{th}h {tm}m** |")
    block = "\n".join(lines)

    fname = _d.strftime("%m-%d-%a")
    log_path = OBSIDIAN_LOG_DIR / f"{fname}.md"
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
        new_content = (f"# {fname}\n\n---\n\n**今天完成的事**\n\n\n\n---\n\n{block}\n")
    log_path.write_text(new_content, encoding="utf-8")


# ── 日历选择弹窗 ─────────────────────────────────────────────────────────────
class CalendarPicker:
    def __init__(self, parent, current_date, callback):
        self.callback    = callback
        self.today       = date.today()
        self._month_date = current_date.replace(day=1)
        self.win = tk.Toplevel(parent)
        self.win.title("选择日期")
        self.win.configure(bg="#FFFFFF")
        self.win.resizable(False, False)
        self.win.grab_set()
        self.win.geometry("+%d+%d" % (parent.winfo_x() + 60, parent.winfo_y() + 60))
        self._build()

    def _build(self):
        for w in self.win.winfo_children():
            w.destroy()
        hdr = tk.Frame(self.win, bg="#FFFFFF")
        hdr.pack(fill="x", padx=16, pady=(12, 6))
        tk.Button(hdr, text="◀", command=self._prev_month,
                  bg="#FFFFFF", relief="flat", font=("Helvetica", 12),
                  cursor="hand2", fg="#1E293B").pack(side="left")
        tk.Label(hdr, text=self._month_date.strftime("%Y年%m月"),
                 font=("Helvetica", 13, "bold"), bg="#FFFFFF",
                 fg="#1E293B").pack(side="left", expand=True)
        nxt_first = (self._month_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        nxt_state = "normal" if nxt_first <= self.today.replace(day=1) else "disabled"
        tk.Button(hdr, text="▶", command=self._next_month,
                  bg="#FFFFFF", relief="flat", font=("Helvetica", 12),
                  cursor="hand2", fg="#1E293B", state=nxt_state).pack(side="right")
        wk = tk.Frame(self.win, bg="#FFFFFF")
        wk.pack(padx=16, pady=(0, 4))
        for i, d in enumerate(["日","一","二","三","四","五","六"]):
            fg = "#EF4444" if i == 0 else "#94A3B8"
            tk.Label(wk, text=d, width=4, font=("Helvetica", 9),
                     bg="#FFFFFF", fg=fg).grid(row=0, column=i)
        grid = tk.Frame(self.win, bg="#FFFFFF")
        grid.pack(padx=16, pady=(0, 14))
        first_col = (self._month_date.weekday() + 1) % 7
        days_in_month = calendar.monthrange(self._month_date.year, self._month_date.month)[1]
        row_i, col_i = 0, first_col
        for day in range(1, days_in_month + 1):
            d = self._month_date.replace(day=day)
            is_today  = d == self.today
            is_future = d > self.today
            if is_future:
                bg, fg, cur, state = "#FFFFFF", "#CBD5E1", "arrow", "disabled"
            elif is_today:
                bg, fg, cur, state = "#1E293B", "#FFFFFF", "hand2", "normal"
            else:
                bg, fg, cur, state = "#FFFFFF", "#334155", "hand2", "normal"
            btn = tk.Button(grid, text=str(day), width=3,
                            bg=bg, fg=fg, relief="flat",
                            font=("Helvetica", 10), cursor=cur, state=state,
                            command=lambda _d=d: self._pick(_d))
            btn.grid(row=row_i, column=col_i, padx=1, pady=2)
            col_i += 1
            if col_i == 7:
                col_i = 0
                row_i += 1

    def _prev_month(self):
        y, m = self._month_date.year, self._month_date.month
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
        self._month_date = self._month_date.replace(year=y, month=m, day=1)
        self._build()

    def _next_month(self):
        y, m = self._month_date.year, self._month_date.month
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        self._month_date = self._month_date.replace(year=y, month=m, day=1)
        self._build()

    def _pick(self, d):
        self.win.destroy()
        self.callback(d)


# ── 主应用 ───────────────────────────────────────────────────────────────────
class TimelineApp:
    def __init__(self, root, win=None):
        self._embedded = win is not None
        self.root = root
        self._win  = win if win else root
        if not self._embedded:
            self.root.title("时间块")
            self.root.configure(bg="#FFFFFF")
            self.root.resizable(False, False)

        self._view_date  = date.today()
        self._scroll_monitor = None

        # drag-select state
        self._drag_active  = False
        self._drag_anchor  = 0     # slot index where drag started
        self._press_y      = 0     # screen y at press (detect click vs drag)
        self._sel_start    = None  # datetime (inclusive)
        self._sel_end      = None  # datetime (exclusive)

        self._load_tasks()
        self._build_ui()
        self._draw()
        self.root.after(80, self._scroll_to_now)
        self._setup_scroll()
        if not self._embedded:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick()

    # ── 数据 ────────────────────────────────────────────────────────────────
    def _load_tasks(self):
        if TASKS_FILE.exists():
            d = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            self.work_tasks = d.get("work", list(TASK_COLORS)[:4])
            self.life_tasks = d.get("life", list(TASK_COLORS)[4:])
        else:
            all_t = list(TASK_COLORS)
            self.work_tasks = all_t[:4]
            self.life_tasks = all_t[4:]
        self.all_tasks = self.work_tasks + self.life_tasks

    def _get_entries(self):
        d = self._view_date.strftime("%Y-%m-%d")
        d_end = (self._view_date + timedelta(days=1)).strftime("%Y-%m-%d")
        with sqlite3.connect(DB_PATH) as c:
            return c.execute(
                "SELECT rowid, start_time, end_time, duration, category FROM entries "
                "WHERE end_time > ? AND start_time < ? ORDER BY start_time, rowid",
                (f"{d} 00:00", f"{d_end} 00:00")
            ).fetchall()

    def _parse(self, entries):
        day_start = datetime.combine(self._view_date, datetime.min.time())
        day_end   = day_start + timedelta(days=1)
        out = []
        for rowid, s, e, dur, cat in entries:
            try:
                s_dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
                e_dt = datetime.strptime(e, "%Y-%m-%d %H:%M")
                # 裁剪到当天边界（处理跨午夜条目）
                s_dt = max(s_dt, day_start)
                e_dt = min(e_dt, day_end)
                if s_dt < e_dt:
                    out.append((s_dt, e_dt, cat, rowid))
            except Exception:
                pass
        return sorted(out)

    def _task_at(self, parsed, t_start):
        t_end = t_start + timedelta(minutes=15)
        best_cat, best_overlap = None, timedelta(0)
        for s_dt, e_dt, cat, _ in parsed:
            overlap = min(e_dt, t_end) - max(s_dt, t_start)
            if overlap > timedelta(minutes=1) and overlap >= best_overlap:
                best_overlap = overlap
                best_cat = cat
        return best_cat

    # ── 坐标转换 ─────────────────────────────────────────────────────────────
    def _xy_to_slot(self, cx, cy):
        """Canvas (x, y) → 15-min slot index 0–95 (row=hour, col=quarter)."""
        y = cy - 10
        h = max(0, min(23, int(y / ROW_H))) if y >= 0 else 0
        x = cx - LABEL_W
        q = max(0, min(3, int(x / CELL_W))) if x >= 0 else 0
        return h * 4 + q

    def _slot_to_dt(self, slot):
        h, m = slot // 4, (slot % 4) * 15
        return datetime.combine(self._view_date, datetime.min.time()) + timedelta(hours=h, minutes=m)

    def _slot_bounds(self, slot):
        """Return (x1, y1, x2, y2) pixel bounds for a slot."""
        h, q = slot // 4, slot % 4
        x1 = LABEL_W + q * CELL_W
        y1 = h * ROW_H + 10
        return x1, y1, x1 + CELL_W, y1 + ROW_H

    # ── UI 构建 ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._content_frame = tk.Frame(self.root, bg="#FFFFFF")
        self._content_frame.pack(fill="both", expand=True)

        # ── 顶部日期导航 ────────────────────────────────────────────────────
        hdr = tk.Frame(self._content_frame, bg="#FFFFFF")
        hdr.pack(fill="x", padx=10, pady=(10, 4))

        tk.Button(hdr, text="◀", command=self._go_prev,
                  bg="#FFFFFF", relief="flat", font=("Helvetica", 13),
                  fg="#94A3B8", cursor="hand2",
                  activeforeground="#1E293B", activebackground="#FFFFFF").pack(side="left")

        self._date_lbl = tk.Label(
            hdr, text=self._fmt_date(self._view_date),
            font=("Helvetica", 14, "bold"), bg="#FFFFFF", fg="#1E293B", cursor="hand2"
        )
        self._date_lbl.pack(side="left", padx=4)
        self._date_lbl.bind("<Button-1>", lambda e: self._open_calendar())

        self._next_btn = tk.Button(
            hdr, text="▶", command=self._go_next,
            bg="#FFFFFF", relief="flat", font=("Helvetica", 13),
            fg="#94A3B8", cursor="hand2",
            activeforeground="#1E293B", activebackground="#FFFFFF"
        )
        self._next_btn.pack(side="left")

        tk.Button(hdr, text="📅", command=self._open_calendar,
                  bg="#FFFFFF", relief="flat", font=("Helvetica", 12),
                  cursor="hand2", activebackground="#FFFFFF").pack(side="left", padx=(6, 0))

        # ── 主体：时间轴 + 任务面板 ─────────────────────────────────────────
        body = tk.Frame(self._content_frame, bg="#FFFFFF")
        body.pack(fill="both", expand=True)

        # 左：可滚动时间轴 canvas
        left = tk.Frame(body, bg="#FFFFFF")
        left.pack(side="left", padx=(10, 0))

        canvas_h = WIN_H - 120
        self.canvas = tk.Canvas(
            left, width=LABEL_W + BAR_W + 2,
            height=canvas_h, bg="#FFFFFF",
            highlightthickness=0, bd=0
        )
        sb = tk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(
            yscrollcommand=sb.set,
            scrollregion=(0, 0, LABEL_W + BAR_W + 2, TOTAL_H)
        )
        self.canvas.pack(side="left")
        sb.pack(side="left", fill="y")

        self.canvas.bind("<ButtonPress-1>",   self._drag_start)
        self.canvas.bind("<B1-Motion>",        self._drag_move)
        self.canvas.bind("<ButtonRelease-1>",  self._drag_end)

        # 右：任务按钮面板（2列 emoji grid）
        right = tk.Frame(body, bg="#FFFFFF", width=RIGHT_W)
        right.pack(side="left", padx=(5, 4), fill="y")
        right.pack_propagate(False)

        grid = tk.Frame(right, bg="#FFFFFF")
        grid.pack(anchor="n", pady=(8, 0))
        for i, t in enumerate(self.all_tasks):
            self._task_btn(grid, t, row=i // 2, col=i % 2)

        # ── 底部状态栏 ────────────────────────────────────────────────────
        bar = tk.Frame(self._content_frame, bg="#F8FAFC", height=36)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self._btn_desel = tk.Button(
            bar, text="不选", command=self._clear_sel,
            bg="#F8FAFC", fg="#64748B", relief="flat",
            font=("Helvetica", 10), cursor="hand2",
            activebackground="#F1F5F9", state="disabled"
        )
        self._btn_desel.pack(side="right", padx=(0, 6), pady=4)

        self._btn_erase = tk.Button(
            bar, text="擦除", command=self._erase_range,
            bg="#F8FAFC", fg="#EF4444", relief="flat",
            font=("Helvetica", 10), cursor="hand2",
            activebackground="#FEF2F2", state="disabled"
        )
        self._btn_erase.pack(side="right", padx=(0, 2), pady=4)

        self._status_lbl = tk.Label(
            bar, text="拖选时间范围",
            font=("Helvetica", 10), bg="#F8FAFC", fg="#CBD5E1"
        )
        self._status_lbl.pack(side="left", padx=10)

    def _fmt_date(self, d):
        return d.strftime(f"%m月%d日  {WEEKDAYS[d.weekday()]}")

    def _task_btn(self, parent, task, row=0, col=0):
        color = TASK_COLORS.get(task, "#94A3B8")
        emoji = task.split(" ")[0]   # first token is always the emoji
        f = tk.Frame(parent, bg=color, cursor="hand2", width=40, height=32)
        f.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
        f.pack_propagate(False)
        lbl = tk.Label(f, text=emoji, bg=color, fg="#FFFFFF",
                       font=("Helvetica", 14), cursor="hand2")
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        for w in (f, lbl):
            w.bind("<Button-1>", lambda e, t=task: self._on_task_click(t))

    # ── 拖选逻辑 ─────────────────────────────────────────────────────────────
    def _drag_start(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        self._drag_anchor = self._xy_to_slot(cx, cy)
        self._drag_active = True
        self._press_x = event.x
        self._press_y = event.y

    def _drag_move(self, event):
        if not self._drag_active:
            return
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        cur = self._xy_to_slot(cx, cy)
        lo = min(self._drag_anchor, cur)
        hi = max(self._drag_anchor, cur)
        self._sel_start = self._slot_to_dt(lo)
        self._sel_end   = self._slot_to_dt(hi + 1)
        self._draw()
        self._update_status()

    def _drag_end(self, event):
        self._drag_active = False
        # Single tap (no drag) → select that one 15-min cell
        if abs(event.y - self._press_y) < 5 and abs(event.x - self._press_x) < 5:
            slot = self._drag_anchor
            self._sel_start = self._slot_to_dt(slot)
            self._sel_end   = self._slot_to_dt(slot + 1)
            self._draw()
            self._update_status()

    def _clear_sel(self):
        self._sel_start = None
        self._sel_end   = None
        self._draw()
        self._update_status()

    def _update_status(self):
        if self._sel_start and self._sel_end and self._sel_end > self._sel_start:
            mins = int((self._sel_end - self._sel_start).total_seconds() / 60)
            if mins >= 60 and mins % 60 == 0:
                text = f"选择了 {mins // 60} 小时"
            elif mins >= 60:
                text = f"选择了 {mins // 60}h {mins % 60}min"
            else:
                text = f"选择了 {mins} 分钟"
            self._status_lbl.configure(text=text, fg="#1E293B")
            self._btn_desel.configure(state="normal")
            self._btn_erase.configure(state="normal")
        else:
            self._status_lbl.configure(text="拖选时间范围", fg="#CBD5E1")
            self._btn_desel.configure(state="disabled")
            self._btn_erase.configure(state="disabled")

    def _on_task_click(self, task):
        if not (self._sel_start and self._sel_end and self._sel_end > self._sel_start):
            self._status_lbl.configure(text="先拖选时间范围", fg="#F59E0B")
            self.root.after(1500, self._update_status)
            return
        self._write_range(self._sel_start, self._sel_end, task)
        self._clear_sel()

    # ── 数据写入 ─────────────────────────────────────────────────────────────
    def _write_range(self, start_dt, end_dt, task):
        """覆写 [start_dt, end_dt) 区间为指定任务，切割已有记录的边缘。"""
        d = self._view_date.strftime("%Y-%m-%d")
        s_str = start_dt.strftime("%Y-%m-%d %H:%M")
        e_str = end_dt.strftime("%Y-%m-%d %H:%M")
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT rowid, start_time, end_time, category FROM entries "
                "WHERE start_time < ? AND end_time > ? AND start_time LIKE ?",
                (e_str, s_str, f"{d}%")
            ).fetchall()
            for rowid, rs, re, cat in rows:
                rs_dt = datetime.strptime(rs, "%Y-%m-%d %H:%M")
                re_dt = datetime.strptime(re, "%Y-%m-%d %H:%M")
                conn.execute("DELETE FROM entries WHERE rowid=?", (rowid,))
                if rs_dt < start_dt:
                    ld = max(1, int((start_dt - rs_dt).total_seconds() / 60))
                    conn.execute(
                        "INSERT INTO entries (start_time,end_time,duration,category,note,status) VALUES(?,?,?,?,?,?)",
                        (rs, s_str, ld, cat, "补录", "已完成")
                    )
                if re_dt > end_dt:
                    rd = max(1, int((re_dt - end_dt).total_seconds() / 60))
                    conn.execute(
                        "INSERT INTO entries (start_time,end_time,duration,category,note,status) VALUES(?,?,?,?,?,?)",
                        (e_str, re, rd, cat, "补录", "已完成")
                    )
            dur = max(1, int((end_dt - start_dt).total_seconds() / 60))
            conn.execute(
                "INSERT INTO entries (start_time,end_time,duration,category,note,status) VALUES(?,?,?,?,?,?)",
                (s_str, e_str, dur, task, "补录", "已完成")
            )
        self._draw()
        _update_log_for_date(d)

    def _erase_range(self):
        if not (self._sel_start and self._sel_end and self._sel_end > self._sel_start):
            return
        start_dt, end_dt = self._sel_start, self._sel_end
        d = self._view_date.strftime("%Y-%m-%d")
        s_str = start_dt.strftime("%Y-%m-%d %H:%M")
        e_str = end_dt.strftime("%Y-%m-%d %H:%M")
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT rowid, start_time, end_time, category FROM entries "
                "WHERE start_time < ? AND end_time > ? AND start_time LIKE ?",
                (e_str, s_str, f"{d}%")
            ).fetchall()
            for rowid, rs, re, cat in rows:
                rs_dt = datetime.strptime(rs, "%Y-%m-%d %H:%M")
                re_dt = datetime.strptime(re, "%Y-%m-%d %H:%M")
                conn.execute("DELETE FROM entries WHERE rowid=?", (rowid,))
                if rs_dt < start_dt:
                    ld = max(1, int((start_dt - rs_dt).total_seconds() / 60))
                    conn.execute(
                        "INSERT INTO entries (start_time,end_time,duration,category,note,status) VALUES(?,?,?,?,?,?)",
                        (rs, s_str, ld, cat, "补录", "已完成")
                    )
                if re_dt > end_dt:
                    rd = max(1, int((re_dt - end_dt).total_seconds() / 60))
                    conn.execute(
                        "INSERT INTO entries (start_time,end_time,duration,category,note,status) VALUES(?,?,?,?,?,?)",
                        (e_str, re, rd, cat, "补录", "已完成")
                    )
        self._clear_sel()
        _update_log_for_date(d)

    # ── 绘图 ─────────────────────────────────────────────────────────────────
    def _draw(self):
        c = self.canvas
        c.delete("all")
        parsed  = self._parse(self._get_entries())
        day0    = datetime.combine(self._view_date, datetime.min.time())
        now     = datetime.now()
        y_end   = 24 * ROW_H + 10

        # Compute selected slot range
        sel_lo = sel_hi = None
        if self._sel_start and self._sel_end and self._sel_end > self._sel_start:
            def _dt_to_slot(dt):
                delta = dt - day0
                total_min = int(delta.total_seconds() / 60)
                return max(0, min(SLOTS, total_min // 15))
            sel_lo = _dt_to_slot(self._sel_start)
            sel_hi = _dt_to_slot(self._sel_end)

        # ── 1. 每个 15 分钟格子（h 行 × q 列）──────────────────────────
        for slot in range(SLOTS):
            h, q = slot // 4, slot % 4
            t_start = day0 + timedelta(hours=h, minutes=q * 15)
            task = self._task_at(parsed, t_start)

            x1 = LABEL_W + q * CELL_W
            y1 = h * ROW_H + 10
            x2 = x1 + CELL_W
            y2 = y1 + ROW_H

            # 选中格子显示蓝色，否则显示任务色 / 空格色
            if sel_lo is not None and sel_lo <= slot < sel_hi:
                fill = SEL_CLR
            else:
                fill = TASK_COLORS.get(task, GAP_CLR) if task else GAP_CLR

            c.create_rectangle(x1, y1, x2 - 1, y2 - 1, fill=fill, outline="")

        # ── 2. 小时横线（加重）+ 时间标签 ───────────────────────────────
        for h in range(24):
            y = h * ROW_H + 10
            c.create_line(LABEL_W, y, LABEL_W + BAR_W, y,
                         fill="#B0B0B0", width=1)
            c.create_text(LABEL_W - 4, y + ROW_H / 2,
                         text=f"{h:02d}:00", anchor="e",
                         font=("Helvetica", 9), fill="#94A3B8")
        c.create_line(LABEL_W, y_end, LABEL_W + BAR_W, y_end,
                     fill="#B0B0B0", width=1)

        # ── 3. 15分钟纵向分隔线（轻）───────────────────────────────────
        for q in range(1, 4):
            xq = LABEL_W + q * CELL_W
            c.create_line(xq, 10, xq, y_end, fill=DIV_CLR, width=1)

        # ── 4. 选中格子边框高亮 ─────────────────────────────────────────
        if sel_lo is not None:
            for slot in range(sel_lo, sel_hi):
                h, q = slot // 4, slot % 4
                x1 = LABEL_W + q * CELL_W
                y1 = h * ROW_H + 10
                c.create_rectangle(x1, y1, x1 + CELL_W - 1, y1 + ROW_H - 1,
                                   fill="", outline=SEL_BDR, width=1)

        # ── 5. 当前时间红线 ──────────────────────────────────────────────
        if self._view_date == date.today():
            nf = (now - day0).total_seconds() / 3600
            if 0 <= nf < 24:
                ny = nf * ROW_H + 10
                c.create_line(LABEL_W - 8, ny, LABEL_W + BAR_W, ny,
                             fill=NOW_CLR, width=2, dash=(5, 3))
                c.create_oval(LABEL_W - 9, ny - 4, LABEL_W - 1, ny + 4,
                             fill=NOW_CLR, outline="")

    # ── 日期导航 ─────────────────────────────────────────────────────────────
    def _go_prev(self):
        self._view_date -= timedelta(days=1)
        self._refresh_date()

    def _go_next(self):
        nxt = self._view_date + timedelta(days=1)
        if nxt <= date.today():
            self._view_date = nxt
            self._refresh_date()

    def _open_calendar(self):
        CalendarPicker(self.root, self._view_date, self._set_date)

    def _set_date(self, d):
        self._view_date = d
        self._clear_sel()
        self._refresh_date()
        if d == date.today():
            self.root.after(50, self._scroll_to_now)

    def _refresh_date(self):
        self._date_lbl.configure(text=self._fmt_date(self._view_date))
        is_today = self._view_date >= date.today()
        self._next_btn.configure(
            state="disabled" if is_today else "normal",
            fg="#CBD5E1" if is_today else "#94A3B8"
        )
        self._draw()

    # ── 滚动 ─────────────────────────────────────────────────────────────────
    def _setup_scroll(self):
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.root.bind_all("<MouseWheel>", self._on_scroll)
        try:
            import AppKit
            def _ns_scroll(ns_event):
                dy = ns_event.scrollingDeltaY()
                if abs(dy) > 0.5:
                    self.canvas.after(0, lambda: self.canvas.yview_scroll(
                        -1 if dy > 0 else 1, "units"))
                return ns_event
            self._scroll_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskScrollWheel, _ns_scroll)
        except Exception:
            pass

    def _on_scroll(self, event):
        if event.delta != 0:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _scroll_to_now(self):
        if self._view_date != date.today():
            return
        canvas_h = self.canvas.winfo_height() or (WIN_H - 120)
        now_y    = datetime.now().hour * ROW_H + 10
        frac     = max(0, (now_y - canvas_h // 2) / TOTAL_H)
        self.canvas.yview_moveto(frac)

    def _on_close(self):
        try:
            import AppKit
            if self._scroll_monitor:
                AppKit.NSEvent.removeMonitor_(self._scroll_monitor)
        except Exception:
            pass
        if not self._embedded:
            self._win.destroy()

    # ── 定时刷新 ─────────────────────────────────────────────────────────────
    def _tick(self):
        self._draw()
        self.root.after(60_000, self._tick)


def main():
    root = tk.Tk()
    root.geometry(f"{WIN_W}x{WIN_H}+200+80")
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass
    TimelineApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
