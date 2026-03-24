# 周天

**macOS 菜单栏专注追踪器，带浮动 Widget 窗口**

[English](README.md) | 中文版

![Platform](https://img.shields.io/badge/Platform-macOS%2012%2B-lightgrey?logo=apple)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 功能

- **菜单栏计时** — 切换任务、每 10 分钟提醒「你还在做 XX 吗？」
- **浮动 Widget** — 首页状态、每日计划、时间轴、项目看板、积压任务、甘特图、热力图、趋势分析
- **Obsidian 联动**（可选）— 从 Markdown 项目文件同步待办，直接勾选完成
- **内置项目模式** — 不用 Obsidian 也能管理项目和待办
- **Blockytime CSV 导出** — 兼容 Blockytime 时间追踪 App
- **iPhone Widget** — 本地 HTTP 服务器 + Scriptable 脚本读取

---

## 下载安装

### 方式一 — 直接下载 .app（推荐）

从 [Releases](https://github.com/Coccul/orbit/releases) 下载最新版 `.app`。

解压后将 `Orbit.app` 移动到应用程序文件夹，双击启动。

> 首次打开时 macOS 可能会拦截，前往 **系统设置 → 隐私与安全性** 点击「仍要打开」即可。

### 方式二 — 从源码运行

**环境要求：** macOS 12+，Python 3.11+

```bash
git clone https://github.com/Coccul/orbit.git
cd orbit
pip install rumps pyobjc-framework-Cocoa --break-system-packages
python focus_guard.py
```

---

## 配置

首次启动后，配置文件自动生成在：

```
~/Library/Application Support/Orbit/config.json
```

也可以在 Widget 的 ⚙️ 设置页面修改常用选项。

### 主要配置项

| 字段 | 类型 | 说明 |
|------|------|------|
| `obsidian_enabled` | bool | 是否启用 Obsidian 联动 |
| `obsidian_project_file` | string | 项目进度 Markdown 文件的绝对路径 |
| `server_port` | int | iPhone Widget HTTP 端口（默认 46461） |
| `obsidian_projects` | array | 项目列表，用于新建时间块和甘特图 |
| `task_colors` | object | 任务名 → 颜色，用于时间轴着色 |
| `category_map` | object | 任务名 → Blockytime 导出分类 |

### `obsidian_projects` 格式

每项为 4 元素数组：`[显示名, 颜色, Obsidian标题关键词, 按钮简称]`

```json
"obsidian_projects": [
  ["💼 工作", "#5B8FD4", "工作", "工作"],
  ["📚 学习", "#E07068", "学习", "学习"]
]
```

---

## 数据存储

所有数据保存在 `~/Library/Application Support/Orbit/`：

| 文件 | 内容 |
|------|------|
| `config.json` | 用户配置 |
| `focus.db` | SQLite，所有专注记录 |
| `schedules.json` | 每日时间块（计划页） |
| `pending.json` | 积压任务 |
| `projects.json` | 内置项目（不使用 Obsidian 时） |

---

## License

[MIT](LICENSE)
