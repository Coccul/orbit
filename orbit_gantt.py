#!/usr/bin/env python3
"""
Orbit 项目甘特图
- 近 14 天每日投入时长可视化
- 联动 Obsidian 项目进度.md 显示待办完成率
- 里程碑管理：优先级 + 截止日期（点右侧面板编辑）
- 今日计划：按优先级自动推荐待办，点击勾选
"""

import tkinter as tk
import sqlite3
import re
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# ── 路径 ─────────────────────────────────────────────────────────────
APP_SUPPORT     = Path.home() / "Library" / "Application Support" / "Orbit"
DB_PATH         = APP_SUPPORT / "focus.db"
MILESTONES_FILE  = APP_SUPPORT / "milestones.json"
PLAN_FILE        = APP_SUPPORT / "daily_plan.json"
SCHEDULES_FILE   = APP_SUPPORT / "schedules.json"

_HERE = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
         else Path(__file__).parent)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import orbit_config

# ── 项目定义：(FG任务名, 颜色, Obsidian 章节关键词) ─────────────────
WORK_PROJECTS = [(p[0], p[1], p[2]) for p in orbit_config.load_obsidian_projects()]

# task → color 快查
TASK_COLOR = {t: c for t, c, _ in WORK_PROJECTS}

# ── 配色 ─────────────────────────────────────────────────────────────
BG     = "#FFFFFF"
PANEL  = "#F1F5F9"
GRID   = "#E2E8F0"
TEXT   = "#334155"
MUTED  = "#94A3B8"
WHITE  = "#FFFFFF"
ACCENT = "#3B82F6"
GREEN  = "#16A34A"
YELLOW = "#D97706"
RED    = "#EF4444"

PRIORITY_COLOR = {"P1": "#EF4444", "P2": "#F59E0B", "P3": "#64748B"}
PRIORITY_BG    = {"P1": "#FEF2F2", "P2": "#FFFBEB", "P3": "#F8FAFC"}

# ── 布局 ─────────────────────────────────────────────────────────────
DAYS        = 14
LABEL_W     = 175
INFO_W      = 200
CELL_W      = 42
CELL_H_W    = 52
HDR_H       = 52
SEC_H       = 28
PLAN_ITEM_H = 28
PAD         = 8
MAX_MINS    = 480

GANTT_ROW_H = 42    # 甘特进度区每项目行高
GANTT_BAR_H = 14    # 进度条高度
GANTT_BLK_S = 10    # 近7天方块尺寸


def _brighten(hex_color: str, amount: int = 40) -> str:
    r = min(255, int(hex_color[1:3], 16) + amount)
    g = min(255, int(hex_color[3:5], 16) + amount)
    b = min(255, int(hex_color[5:7], 16) + amount)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── 数据加载 ─────────────────────────────────────────────────────────
def load_db_data() -> dict:
    today  = date.today()
    start  = today - timedelta(days=DAYS - 1)
    result = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT category, substr(start_time,1,10) AS d, SUM(duration) "
            "FROM entries WHERE d >= ? GROUP BY category, d",
            (start.isoformat(),)
        ).fetchall()
        conn.close()
    except Exception:
        return result
    for cat, d, mins in rows:
        result.setdefault(cat, {})[d] = mins
    return result


def load_schedule_data() -> dict:
    """返回 {task_name: {date_str: {"total": N, "done": N}}}，按颜色匹配项目。"""
    result = {}
    if not SCHEDULES_FILE.exists():
        return result
    try:
        raw = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return result

    color_to_task = {c: t for t, c, _ in WORK_PROJECTS}
    today = date.today()
    start = today - timedelta(days=DAYS - 1)

    for date_str, day_info in raw.items():
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        if d < start or d > today:
            continue
        blocks = day_info.get("blocks", []) if isinstance(day_info, dict) else []
        for blk in blocks:
            task = color_to_task.get(blk.get("color", ""))
            if not task:
                continue
            counts = result.setdefault(task, {}).setdefault(
                date_str, {"total": 0, "done": 0})
            counts["total"] += 1
            if blk.get("done", False):
                counts["done"] += 1
    return result


def load_todos() -> dict:
    """返回 {section: {"done": N, "total": N, "pending": ["任务文字", ...]}}"""
    result = {}
    pf = orbit_config.project_file()
    if pf is None or not pf.exists():
        return result
    try:
        text = pf.read_text(encoding="utf-8")
    except Exception:
        return result

    current = None
    done = total = 0
    pending = []
    for line in text.splitlines():
        if line.startswith("# "):
            if current is not None:
                result[current] = {"done": done, "total": total, "pending": pending}
            current = line[2:].strip()
            done = total = 0
            pending = []
        elif re.match(r'\s+- \[x\]', line, re.IGNORECASE):
            done += 1
            total += 1
        elif re.match(r'\s+- \[ \]', line):
            task_text = re.sub(r'^\s+- \[ \]\s*', '', line).strip()
            if task_text:
                pending.append(task_text)
            total += 1
    if current is not None:
        result[current] = {"done": done, "total": total, "pending": pending}
    return result


def match_todos(todos: dict, keyword: str):
    """返回 (done, total) —— 兼容旧调用"""
    if not keyword:
        return (0, 0)
    for section, data in todos.items():
        if keyword in section:
            if isinstance(data, dict):
                return (data["done"], data["total"])
            return data
    return (0, 0)


def match_pending(todos: dict, keyword: str) -> list:
    """返回该项目的待办文字列表"""
    if not keyword:
        return []
    for section, data in todos.items():
        if keyword in section:
            return data.get("pending", []) if isinstance(data, dict) else []
    return []


def load_milestones() -> dict:
    try:
        return json.loads(MILESTONES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_milestones(data: dict):
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    MILESTONES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_plan() -> dict:
    """返回今日计划，过期则返回空"""
    try:
        data = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        if data.get("date") == date.today().isoformat():
            return data
    except Exception:
        pass
    return {"date": date.today().isoformat(), "items": []}


def save_plan(data: dict):
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def auto_plan(todos: dict, milestones: dict) -> list:
    """按优先级 + 截止日排序，每个项目取最多 2 条待办，合计最多 5 条"""
    today = date.today()

    def urgency(proj_tuple):
        task, _, obs_key = proj_tuple
        ms = milestones.get(task, {})
        p  = ms.get("priority", "")
        p_rank = {"P1": 0, "P2": 1, "P3": 2}.get(p, 3)
        dl = ms.get("deadline")
        if dl:
            try:
                days_left = (date.fromisoformat(dl) - today).days
            except ValueError:
                days_left = 9999
        else:
            days_left = 9999
        return (p_rank, days_left)

    sorted_projects = sorted(WORK_PROJECTS, key=urgency)
    items = []
    for task, color, obs_key in sorted_projects:
        if len(items) >= 5:
            break
        pending = match_pending(todos, obs_key)
        for t in pending[:2]:
            if len(items) >= 5:
                break
            items.append({"project": task, "task": t, "done": False})
    return items


def deadline_color(dl: date, today: date) -> str:
    days_left = (dl - today).days
    if days_left <= 7:
        return RED
    elif days_left <= 14:
        return YELLOW
    else:
        return GREEN


# ── 主窗口 ────────────────────────────────────────────────────────────
class GanttWindow:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("周天 — 项目进度")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception:
            pass

        self.db         = load_db_data()
        self.schedules  = load_schedule_data()
        self.todos      = load_todos()
        self.milestones = load_milestones()
        self.plan       = load_plan()
        self.days       = [(date.today() - timedelta(days=DAYS - 1 - i))
                           for i in range(DAYS)]

        # 若今日计划为空，自动生成
        if not self.plan["items"]:
            self.plan["items"] = auto_plan(self.todos, self.milestones)
            save_plan(self.plan)

        self._row_hitboxes  = []  # [(task, y0, y1)]
        self._plan_hitboxes = []  # [(item_index, y0, y1)]

        self._build()
        self.root.mainloop()

    # ── 尺寸 ──────────────────────────────────────────────────────────
    def _canvas_w(self):
        return LABEL_W + CELL_W * DAYS + INFO_W

    def _plan_section_h(self):
        n = len(self.plan.get("items", []))
        return SEC_H + n * PLAN_ITEM_H + PAD if n > 0 else SEC_H + PAD

    def _gantt_section_h(self):
        return SEC_H + len(WORK_PROJECTS) * GANTT_ROW_H + PAD

    def _canvas_h(self):
        return HDR_H + self._gantt_section_h() + PAD * 2

    def _build(self):
        cw = self._canvas_w()
        ch = self._canvas_h()
        self.root.geometry(f"{cw}x{ch}")
        self.canvas = tk.Canvas(self.root, width=cw, height=ch,
                                bg=BG, highlightthickness=0)
        self.canvas.pack()
        self._draw()
        self.canvas.bind("<Button-1>", self._on_click)

    def _resize_canvas(self):
        cw = self._canvas_w()
        ch = self._canvas_h()
        self.root.geometry(f"{cw}x{ch}")
        self.canvas.config(width=cw, height=ch)

    # ── 绘制入口 ──────────────────────────────────────────────────────
    def _draw(self):
        c = self.canvas
        c.delete("all")
        self._row_hitboxes  = []
        self._plan_hitboxes = []
        cw    = self._canvas_w()
        ch    = self._canvas_h()
        today = date.today()

        # 面板背景（左标签区 + 右信息区）
        c.create_rectangle(0, 0, LABEL_W, ch, fill=PANEL, outline="")
        c.create_rectangle(LABEL_W + CELL_W * DAYS, 0, cw, ch, fill=PANEL, outline="")
        c.create_line(0, HDR_H, cw, HDR_H, fill=GRID, width=1)

        # 顶部标题
        c.create_text(PAD + 4, HDR_H // 2,
                      text="📊  项目进度", fill=TEXT,
                      anchor="w", font=("Helvetica Neue", 12, "bold"))
        c.create_text(cw - PAD - 4, HDR_H // 2,
                      text=today.strftime("%m/%d"),
                      fill=MUTED, anchor="e", font=("Helvetica Neue", 9))

        # 甘特进度区
        y = HDR_H
        self._draw_gantt_section(y)

    # ── 甘特进度区 ────────────────────────────────────────────────────
    def _draw_gantt_section(self, y) -> int:
        c     = self.canvas
        cw    = self._canvas_w()
        today = date.today()

        # 区块背景
        sec_h = self._gantt_section_h()
        c.create_rectangle(0, y, cw, y + sec_h, fill="#FAFBFC", outline="")
        c.create_line(0, y + sec_h, cw, y + sec_h, fill=GRID, width=1)

        # section header
        c.create_text(PAD + 6, y + SEC_H // 2,
                      text="项目进度", fill=TEXT, anchor="w",
                      font=("Helvetica Neue", 9, "bold"))
        c.create_text(LABEL_W + PAD, y + SEC_H // 2,
                      text="← 开始", fill=MUTED, anchor="w",
                      font=("Helvetica Neue", 8))
        c.create_text(LABEL_W + CELL_W * DAYS - PAD, y + SEC_H // 2,
                      text="截止 →", fill=MUTED, anchor="e",
                      font=("Helvetica Neue", 8))
        y += SEC_H

        # 统一时间轴：收集所有项目的 start / deadline
        starts    = []
        deadlines = []
        for task, _, _ in WORK_PROJECTS:
            ms = self.milestones.get(task, {})
            cr = ms.get("created")
            dl = ms.get("deadline")
            if cr:
                try:
                    starts.append(date.fromisoformat(cr))
                except ValueError:
                    pass
            if dl:
                try:
                    deadlines.append(date.fromisoformat(dl))
                except ValueError:
                    pass

        axis_start = min(starts)    if starts    else today - timedelta(days=30)
        axis_end   = max(deadlines) if deadlines else today + timedelta(days=60)
        if axis_end <= today:
            axis_end = today + timedelta(days=14)
        total_days = max((axis_end - axis_start).days, 1)
        bar_area_w = CELL_W * DAYS  # 与热力图等宽

        def date_x(d: date) -> int:
            frac = max(0.0, min(1.0, (d - axis_start).days / total_days))
            return LABEL_W + int(frac * bar_area_w)

        today_x = date_x(today)
        gantt_top = y  # 记录顶部，最后画红线用

        for task, color, obs_key in WORK_PROJECTS:
            ms    = self.milestones.get(task, {})
            cr    = ms.get("created")
            dl    = ms.get("deadline")
            dim_c = _brighten(color, 50)  # 淡化背景色

            # 左侧标签
            label = task if len(task) <= 10 else task[:9] + "…"
            cy    = y + GANTT_ROW_H // 2
            c.create_text(PAD + 6, cy - 2,
                          text=label, fill=TEXT, anchor="w",
                          font=("Helvetica Neue", 10))

            # 时间轴端点解析
            try:
                p_start = date.fromisoformat(cr) if cr else today - timedelta(days=30)
            except ValueError:
                p_start = today - timedelta(days=30)
            try:
                p_end = date.fromisoformat(dl) if dl else None
            except ValueError:
                p_end = None

            x0     = date_x(p_start)
            x1     = date_x(p_end) if p_end else LABEL_W + bar_area_w - 2
            bar_y  = y + (GANTT_ROW_H - GANTT_BAR_H - GANTT_BLK_S - 4) // 2
            bar_y  = y + 4

            if p_end is None:
                # 无截止日：灰色虚线条
                c.create_rectangle(x0, bar_y, x1, bar_y + GANTT_BAR_H,
                                   fill=GRID, outline="")
                c.create_text((x0 + x1) // 2, bar_y + GANTT_BAR_H // 2,
                              text="无截止日", fill=MUTED,
                              font=("Helvetica Neue", 8))
            else:
                # 进度计算
                done, total = match_todos(self.todos, obs_key)
                todo_pct    = done / total if total > 0 else 0.0
                span_days   = max((p_end - p_start).days, 1)
                elapsed_pct = max(0.0, min(1.0, (today - p_start).days / span_days))
                behind      = elapsed_pct - todo_pct

                if behind <= 0.05:
                    fill_c = GREEN
                elif behind <= 0.2:
                    fill_c = YELLOW
                else:
                    fill_c = RED

                bar_w_px = max(x1 - x0, 4)
                fill_px  = max(0, min(int(todo_pct * bar_w_px), bar_w_px))

                # 底色（整个项目时间段）
                c.create_rectangle(x0, bar_y, x1, bar_y + GANTT_BAR_H,
                                   fill=dim_c, outline="")
                # 已完成填色
                if fill_px > 0:
                    c.create_rectangle(x0, bar_y, x0 + fill_px, bar_y + GANTT_BAR_H,
                                       fill=fill_c, outline="")
                # 进度百分比文字
                if total > 0:
                    pct_txt = f"{int(todo_pct * 100)}%"
                    mid_x   = x0 + fill_px // 2 if fill_px > 24 else (x0 + x1) // 2
                    c.create_text(mid_x, bar_y + GANTT_BAR_H // 2,
                                  text=pct_txt,
                                  fill=WHITE if fill_px > 24 else MUTED,
                                  font=("Helvetica Neue", 8, "bold"))

                # 截止日菱形
                dlc = deadline_color(p_end, today)
                c.create_polygon(x1, bar_y,
                                 x1 + 4, bar_y + GANTT_BAR_H // 2,
                                 x1, bar_y + GANTT_BAR_H,
                                 x1 - 4, bar_y + GANTT_BAR_H // 2,
                                 fill=dlc, outline="")

                # 右侧信息
                xi         = LABEL_W + bar_area_w + PAD + 4
                days_left  = (p_end - today).days
                if days_left < 0:
                    dl_str = f"逾期{-days_left}天"
                    dl_c   = RED
                elif days_left == 0:
                    dl_str = "今天截止"
                    dl_c   = RED
                else:
                    dl_str = f"{p_end.strftime('%m/%d')} -{days_left}天"
                    dl_c   = dlc

                if behind > 0.05:
                    gap_str = f"差{int(behind * 100)}%"
                    gap_c   = RED if behind > 0.2 else YELLOW
                else:
                    gap_str = "跟上了"
                    gap_c   = GREEN

                c.create_text(xi, bar_y + 2, text=gap_str, fill=gap_c, anchor="w",
                              font=("Helvetica Neue", 9, "bold"))
                c.create_text(xi, bar_y + GANTT_BAR_H + 1, text=dl_str, fill=dl_c,
                              anchor="w", font=("Helvetica Neue", 8))

            # 近7天 schedule 方块（固定锚定在今日红线左侧）
            blk_y   = bar_y + GANTT_BAR_H + 4
            blk_gap = 12  # 每个方块占宽
            last_7  = [today - timedelta(days=i) for i in range(6, -1, -1)]
            for i, day in enumerate(last_7):
                offset = 6 - i  # 0=今天, 6=6天前
                bx     = today_x - offset * blk_gap
                if bx < LABEL_W or bx > LABEL_W + bar_area_w:
                    continue
                sched = self.schedules.get(task, {}).get(day.isoformat())
                if sched and sched["total"] > 0:
                    ratio = sched["done"] / sched["total"]
                    sq_c  = "#4ADE80" if ratio >= 1.0 else "#FCD34D" if ratio > 0 else "#CBD5E1"
                else:
                    sq_c = "#EDEEF0"
                c.create_rectangle(bx - GANTT_BLK_S // 2, blk_y,
                                   bx + GANTT_BLK_S // 2, blk_y + GANTT_BLK_S,
                                   fill=sq_c, outline=GRID)

            c.create_line(0, y + GANTT_ROW_H, cw, y + GANTT_ROW_H, fill=GRID, width=1)
            y += GANTT_ROW_H

        # 今日红线（贯穿全部项目行）
        c.create_line(today_x, gantt_top, today_x, y,
                      fill=RED, width=2, dash=(4, 2))
        c.create_text(today_x, gantt_top - 4, text="今",
                      fill=RED, font=("Helvetica Neue", 8, "bold"))

        return y + PAD

    # ── 今日计划区 ────────────────────────────────────────────────────
    def _draw_plan_section(self, y) -> int:
        c   = self.canvas
        cw  = self._canvas_w()
        items = self.plan.get("items", [])

        # 区块背景（浅蓝色调）
        sec_h = self._plan_section_h()
        c.create_rectangle(0, y, cw, y + sec_h, fill="#F0F7FF", outline="")
        c.create_line(0, y + sec_h, cw, y + sec_h, fill=GRID, width=1)

        # section header
        c.create_text(PAD + 6, y + SEC_H // 2,
                      text="今日计划", fill=ACCENT, anchor="w",
                      font=("Helvetica Neue", 9, "bold"))
        done_count = sum(1 for it in items if it.get("done"))
        c.create_text(PAD + 76, y + SEC_H // 2,
                      text=f"{done_count}/{len(items)}", fill=MUTED, anchor="w",
                      font=("Helvetica Neue", 9))

        # "↺ 重新生成" 按钮区域
        regen_x = cw - PAD - 4
        c.create_text(regen_x, y + SEC_H // 2,
                      text="↺ 重新生成", fill=MUTED, anchor="e",
                      font=("Helvetica Neue", 9))
        self._regen_hitbox = (y, y + SEC_H)

        y += SEC_H

        for idx, item in enumerate(items):
            self._plan_hitboxes.append((idx, y, y + PLAN_ITEM_H))
            self._draw_plan_item(y, item)
            y += PLAN_ITEM_H

        return y + PAD

    def _draw_plan_item(self, y, item):
        c     = self.canvas
        cw    = self._canvas_w()
        done  = item.get("done", False)
        proj  = item.get("project", "")
        task  = item.get("task", "")
        color = TASK_COLOR.get(proj, MUTED)
        cy    = y + PLAN_ITEM_H // 2

        # 勾选圆圈
        cr = 7
        cx_circle = PAD + 10
        if done:
            c.create_oval(cx_circle - cr, cy - cr, cx_circle + cr, cy + cr,
                          fill=color, outline="")
            c.create_text(cx_circle, cy, text="✓", fill=WHITE,
                          font=("Helvetica Neue", 8, "bold"))
        else:
            c.create_oval(cx_circle - cr, cy - cr, cx_circle + cr, cy + cr,
                          fill=WHITE, outline=GRID)

        # 项目色点
        dot_x = cx_circle + cr + 10
        c.create_oval(dot_x - 4, cy - 4, dot_x + 4, cy + 4,
                      fill=color, outline="")

        # 项目名（短）
        proj_short = proj.split()[-1] if proj else ""  # 取 emoji 后第一个词
        name_x = dot_x + 12
        c.create_text(name_x, cy, text=proj_short, fill=MUTED, anchor="w",
                      font=("Helvetica Neue", 9))

        # 任务文字
        task_x = name_x + 68
        max_w  = cw - task_x - PAD - 10
        # 按像素估算截断（约 7px/字）
        max_chars = max(10, int(max_w / 7))
        task_display = task if len(task) <= max_chars else task[:max_chars - 1] + "…"
        fg = MUTED if done else TEXT
        c.create_text(task_x, cy, text=task_display, fill=fg, anchor="w",
                      font=("Helvetica Neue", 10))

        # 删除线（完成时）
        if done:
            text_w = min(len(task_display) * 7, max_w)
            c.create_line(task_x, cy, task_x + text_w, cy,
                          fill=MUTED, width=1)

    # ── 工作行 ────────────────────────────────────────────────────────
    def _draw_work_row(self, y, task, color, obs_key):
        c         = self.canvas
        data      = self.db.get(task, {})
        ms        = self.milestones.get(task, {})
        today     = date.today()
        dim_color = _brighten(color, -40)

        label = task if len(task) <= 10 else task[:9] + "…"
        c.create_text(PAD + 6, y + CELL_H_W // 2,
                      text=label, fill=TEXT, anchor="w",
                      font=("Helvetica Neue", 10))

        dl_date = None
        if ms.get("deadline"):
            try:
                dl_date = date.fromisoformat(ms["deadline"])
            except ValueError:
                pass

        total_mins = 0
        for i, d in enumerate(self.days):
            mins = data.get(d.isoformat(), 0)
            total_mins += mins
            x  = LABEL_W + i * CELL_W
            ip = 3
            if mins > 0:
                bar_h = max(4, int((mins / MAX_MINS) * (CELL_H_W - ip * 2)))
                bar_h = min(bar_h, CELL_H_W - ip * 2)
                c.create_rectangle(x + ip, y + ip,
                                   x + CELL_W - ip, y + CELL_H_W - ip,
                                   fill=dim_color, outline="")
                c.create_rectangle(x + ip, y + CELL_H_W - ip - bar_h,
                                   x + CELL_W - ip, y + CELL_H_W - ip,
                                   fill=color, outline="")
                if mins >= 60:
                    h, m = divmod(mins, 60)
                    lbl  = f"{h}h" if m == 0 else f"{h}h{m:02d}"
                    c.create_text(x + CELL_W // 2, y + CELL_H_W // 2,
                                  text=lbl, fill=WHITE,
                                  font=("Helvetica Neue", 7, "bold"))
            else:
                c.create_rectangle(x + ip, y + ip,
                                   x + CELL_W - ip, y + CELL_H_W - ip,
                                   fill=GRID, outline="")

            # 计划块完成率（schedules.json）
            sched = self.schedules.get(task, {}).get(d.isoformat())
            if sched and sched["total"] > 0:
                s_done  = sched["done"]
                s_total = sched["total"]
                if s_done == s_total:
                    sc = "#4ADE80"   # 全完成 绿
                elif s_done > 0:
                    sc = "#FCD34D"   # 部分完成 黄
                else:
                    sc = "#94A3B8"   # 全未完成 灰
                c.create_text(x + CELL_W // 2, y + ip + 7,
                              text=f"✓{s_done}/{s_total}",
                              fill=sc,
                              font=("Helvetica Neue", 7, "bold"))

        # 里程碑竖线
        if dl_date and self.days[0] <= dl_date <= self.days[-1]:
            col_i = (dl_date - self.days[0]).days
            mx    = LABEL_W + col_i * CELL_W + CELL_W
            dlc   = deadline_color(dl_date, today)
            c.create_line(mx, y + 4, mx, y + CELL_H_W - 4,
                          fill=dlc, width=2, dash=(4, 2))
            c.create_polygon(mx, y + 4, mx + 4, y + 9,
                             mx, y + 14, mx - 4, y + 9,
                             fill=dlc, outline="")

        # 右侧信息
        xi          = LABEL_W + CELL_W * DAYS + PAD + 4
        cy          = y + CELL_H_W // 2
        priority    = ms.get("priority", "")
        has_dl      = dl_date is not None
        has_todo    = bool(obs_key and match_todos(self.todos, obs_key)[1] > 0)
        qty_target  = int(ms.get("qty_target") or 0)
        qty_current = int(ms.get("qty_current") or 0)
        has_qty     = qty_target > 0

        # 动态行布局：按实际有的信息分配 y 位置
        rows  = ["time"]
        if has_dl:   rows.append("dl")
        if has_todo: rows.append("todo")
        if has_qty:  rows.append("qty")
        row_h = 12
        start = cy - (len(rows) * row_h) // 2 + row_h // 2
        row_y = {name: start + i * row_h for i, name in enumerate(rows)}

        # 时间 + 优先级徽章
        t_y      = row_y["time"]
        total_h  = total_mins / 60
        time_str = f"{total_h:.1f}h" if total_h >= 1 else f"{total_mins}m"
        c.create_text(xi, t_y, text=time_str, fill=TEXT, anchor="w",
                      font=("Helvetica Neue", 11, "bold"))
        if priority:
            pc = PRIORITY_COLOR[priority]; pbg = PRIORITY_BG[priority]
            bx = xi + 46
            c.create_rectangle(bx - 1, t_y - 8, bx + 27, t_y + 7,
                                fill=pbg, outline=pc)
            c.create_text(bx + 13, t_y, text=priority, fill=pc,
                          font=("Helvetica Neue", 8, "bold"))

        # 截止日倒计时
        if has_dl:
            dl_y      = row_y["dl"]
            days_left = (dl_date - today).days
            dlc       = deadline_color(dl_date, today)
            if days_left < 0:
                dl_text = f"已逾期 {-days_left}天"
            elif days_left == 0:
                dl_text = "今天截止！"
            else:
                dl_text = f"↓{days_left}天  {dl_date.strftime('%m/%d')}"
            c.create_text(xi, dl_y, text=dl_text, fill=dlc, anchor="w",
                          font=("Helvetica Neue", 9))

        # Obsidian 待办进度条
        if has_todo:
            bar_y  = row_y["todo"]
            done, total = match_todos(self.todos, obs_key)
            pct    = done / total
            bar_w  = INFO_W - PAD * 2 - 44
            done_w = int(pct * bar_w)
            c.create_rectangle(xi, bar_y - 4, xi + bar_w, bar_y + 4,
                                fill=GRID, outline="")
            if done_w > 0:
                pc = GREEN if pct >= 1.0 else YELLOW if pct >= 0.5 else ACCENT
                c.create_rectangle(xi, bar_y - 4, xi + done_w, bar_y + 4,
                                   fill=pc, outline="")
            c.create_text(xi + bar_w + 4, bar_y,
                          text=f"{done}/{total}", fill=MUTED, anchor="w",
                          font=("Helvetica Neue", 8))

        # 量化目标进度条
        if has_qty:
            qty_y  = row_y["qty"]
            pct    = min(qty_current / qty_target, 1.0)
            bar_w  = INFO_W - PAD * 2 - 44
            done_w = int(pct * bar_w)
            pc_bar = GREEN if pct >= 1.0 else YELLOW if pct >= 0.5 else ACCENT
            unit   = ms.get("goal", "")
            c.create_rectangle(xi, qty_y - 4, xi + bar_w, qty_y + 4,
                                fill=GRID, outline="")
            if done_w > 0:
                c.create_rectangle(xi, qty_y - 4, xi + done_w, qty_y + 4,
                                   fill=pc_bar, outline="")
            label = f"{qty_current}/{qty_target}"
            if unit:
                label += f" {unit[:4]}"
            c.create_text(xi + bar_w + 4, qty_y,
                          text=label, fill=TEXT, anchor="w",
                          font=("Helvetica Neue", 8, "bold"))

        c.create_text(self._canvas_w() - 6, cy, text="✏", fill="#CBD5E1",
                      anchor="e", font=("Helvetica Neue", 10))

    # ── 点击处理 ──────────────────────────────────────────────────────
    def _on_click(self, event):
        x, y = event.x, event.y

        # 今日计划：重新生成
        if hasattr(self, "_regen_hitbox"):
            y0, y1 = self._regen_hitbox
            if y0 <= y < y1 and x >= self._canvas_w() - 120:
                self._regen_plan()
                return

        # 今日计划：勾选 item
        for idx, y0, y1 in self._plan_hitboxes:
            if y0 <= y < y1:
                items = self.plan.get("items", [])
                if 0 <= idx < len(items):
                    items[idx]["done"] = not items[idx]["done"]
                    save_plan(self.plan)
                    self._draw()
                return

        # 里程碑编辑（点右侧面板）
        if x >= LABEL_W + CELL_W * DAYS:
            for task, y0, y1 in self._row_hitboxes:
                if y0 <= y < y1:
                    self._edit_milestone(task)
                    return

        # 其他区域 → 刷新数据
        self.db         = load_db_data()
        self.schedules  = load_schedule_data()
        self.todos      = load_todos()
        self.milestones = load_milestones()
        self._draw()

    def _regen_plan(self):
        self.todos      = load_todos()
        self.milestones = load_milestones()
        self.plan["items"] = auto_plan(self.todos, self.milestones)
        save_plan(self.plan)
        self._resize_canvas()
        self._draw()

    # ── 里程碑编辑弹窗 ────────────────────────────────────────────────
    def _edit_milestone(self, task):
        ms = self.milestones.get(task, {})
        dlg = tk.Toplevel(self.root)
        dlg.title(f"里程碑 — {task}")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.geometry("300x320")

        tk.Label(dlg, text="优先级", bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 10)).pack(anchor="w", padx=14, pady=(12, 2))
        prio_var = tk.StringVar(value=ms.get("priority", ""))
        prio_f = tk.Frame(dlg, bg=BG)
        prio_f.pack(anchor="w", padx=14)
        for p in ("P1", "P2", "P3"):
            tk.Radiobutton(prio_f, text=p, variable=prio_var, value=p,
                           bg=BG, fg=PRIORITY_COLOR[p], selectcolor=BG,
                           activebackground=BG,
                           font=("Helvetica Neue", 10, "bold")).pack(side="left", padx=6)
        tk.Radiobutton(prio_f, text="无", variable=prio_var, value="",
                       bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                       font=("Helvetica Neue", 10)).pack(side="left", padx=6)

        tk.Label(dlg, text="截止日期 (YYYY-MM-DD)", bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 10)).pack(anchor="w", padx=14, pady=(10, 2))
        date_var   = tk.StringVar(value=ms.get("deadline") or "")
        date_entry = tk.Entry(dlg, textvariable=date_var, width=18,
                              font=("Helvetica Neue", 10), bg=PANEL, fg=TEXT,
                              relief="flat", highlightthickness=1,
                              highlightbackground=GRID, highlightcolor=ACCENT)
        date_entry.pack(anchor="w", padx=14)

        tk.Label(dlg, text="数量目标", bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 10)).pack(anchor="w", padx=14, pady=(10, 2))
        qty_f = tk.Frame(dlg, bg=BG)
        qty_f.pack(anchor="w", padx=14)
        qty_target_var  = tk.StringVar(value=str(ms.get("qty_target") or ""))
        qty_current_var = tk.StringVar(value=str(ms.get("qty_current") or ""))
        tk.Label(qty_f, text="目标", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 9)).pack(side="left")
        tk.Entry(qty_f, textvariable=qty_target_var, width=6,
                 font=("Helvetica Neue", 10), bg=PANEL, fg=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRID, highlightcolor=ACCENT).pack(side="left", padx=(4, 12))
        tk.Label(qty_f, text="当前", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 9)).pack(side="left")
        tk.Entry(qty_f, textvariable=qty_current_var, width=6,
                 font=("Helvetica Neue", 10), bg=PANEL, fg=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRID, highlightcolor=ACCENT).pack(side="left", padx=(4, 0))

        tk.Label(dlg, text="单位（如：个视频、篇文章）", bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 10)).pack(anchor="w", padx=14, pady=(8, 2))
        goal_var = tk.StringVar(value=ms.get("goal", ""))
        tk.Entry(dlg, textvariable=goal_var, width=20,
                 font=("Helvetica Neue", 10), bg=PANEL, fg=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRID, highlightcolor=ACCENT).pack(anchor="w", padx=14)

        def _save():
            dl = date_var.get().strip()
            if dl:
                try:
                    date.fromisoformat(dl)
                except ValueError:
                    date_entry.config(highlightbackground=RED)
                    return
            entry = self.milestones.setdefault(task, {})
            if "created" not in entry:
                entry["created"] = date.today().isoformat()
            entry["priority"] = prio_var.get()
            entry["deadline"] = dl or None
            entry["goal"]     = goal_var.get().strip()
            try:
                entry["qty_target"]  = int(qty_target_var.get().strip() or 0)
                entry["qty_current"] = int(qty_current_var.get().strip() or 0)
            except ValueError:
                entry["qty_target"]  = 0
                entry["qty_current"] = 0
            save_milestones(self.milestones)
            dlg.destroy()
            self._draw()

        def _clear():
            self.milestones.pop(task, None)
            save_milestones(self.milestones)
            dlg.destroy()
            self._draw()

        btn_f = tk.Frame(dlg, bg=BG)
        btn_f.pack(pady=12)
        tk.Button(btn_f, text="保存", command=_save,
                  bg=ACCENT, fg=WHITE, relief="flat",
                  font=("Helvetica Neue", 10), padx=16, pady=4).pack(side="left", padx=6)
        tk.Button(btn_f, text="清除", command=_clear,
                  bg=PANEL, fg=MUTED, relief="flat",
                  font=("Helvetica Neue", 10), padx=10, pady=4).pack(side="left", padx=6)


if __name__ == "__main__":
    GanttWindow()
