"""
py2app 打包配置
运行方式：
  cd ~/.local/share/focusguard
  python setup.py py2app
"""
from setuptools import setup

APP = ['orbit_guard.py']

OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'Orbit.icns',
    'plist': {
        'CFBundleName':             'Orbit',
        'CFBundleDisplayName':      'Orbit',
        'CFBundleIdentifier':       'com.cam.orbit',
        'CFBundleVersion':          '1.0.0',
        'CFBundleShortVersionString': '1.0',
        'LSUIElement':              True,   # 不显示 Dock 图标
        'NSHighResolutionCapable':  True,
        'NSRemindersUsageDescription': 'Orbit 需要访问提醒事项，以便创建明天上午的任务提醒。',
    },
    'packages': ['rumps', 'tkinter'],
    'includes': [
        'tkinter', 'tkinter.messagebox', 'tkinter.ttk',
        '_tkinter',
        'sqlite3', 'AppKit',
    ],
    'extra_scripts': [
        'orbit_config.py',
        'orbit_timeline.py',
        'orbit_widget.py',
        'orbit_gantt.py',
        'orbit_planner.py',
        'orbit_backlog.py',
        'orbit_heatmap.py',
        'orbit_stats.py',
        'orbit_trend.py',
        'orbit_breakdown.py',
    ],
}

setup(
    name='Orbit',
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
