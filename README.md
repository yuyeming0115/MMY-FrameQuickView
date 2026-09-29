# MMY-FrameQuickView

全序列帧极速预览桌面工具（MMY-Tools 系列）。拖入资源目录即可预览序列帧动画、
检测文件夹结构缺漏、叠层核对同 ID 多部件资源是否配套。

![技术栈](https://img.shields.io/badge/Python-3.13-blue) ![GUI](https://img.shields.io/badge/GUI-PySide6-green) ![平台](https://img.shields.io/badge/平台-Windows-lightgrey)

## 核心功能

- **查漏补缺**：按模板规则检测文件夹结构——缺方向、缺动作、帧号断档，左栏红点 + 状态栏提示
- **GIF 动画检验**：B 区动态播放帧序列，可调速（FPS 1–60）、逐帧步进、与 A 区网格逐帧联动对照
- **同 ID 部件叠层**：同 ID 多部件（shadow / body / hair / weapon…）按模板层序叠合预览，
  支持套装整装、穿戴特效/翅膀，逐层显隐开关
- **画布 3x3 显向热区**：画布内点击/拖拽切换方向；金色 = 当前方向，米白 = 鼠标指向
- **中文名匹配表**：`*匹配表*.txt` 自动发现，新 ID 自动登记，F2 内联改名热回写

## 环境搭建

> ⚠️ 本项目使用 WorkBuddy managed Python 3.13（系统 Python 已损坏，禁用）。

```bash
# 1. 用 WorkBuddy Python 建 venv（路径按本机实际调整）
C:\Users\EDY\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv .venv

# 2. 安装依赖
.venv/Scripts/python.exe -m pip install PySide6-Essentials pillow numpy

# 3. 打包另需
.venv/Scripts/python.exe -m pip install pyinstaller
```

## 运行

```bash
.venv/Scripts/python.exe src/main.py
```

启动后拖入部件文件夹 / 父级目录（或点顶栏快捷目录 chip）即可。

## 运行测试

tests/ 下均为独立脚本（非 pytest），逐个用 venv Python 运行：

```bash
# 单个测试（Git Bash）
.venv/Scripts/python.exe tests/test_m35_b_toolbar_overlay.py

# GUI 类测试建议加 offscreen，避免弹窗口
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/test_m8_layout.py

# 全量回归
for f in tests/test_*.py; do echo "== $f"; QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe "$f"; done
```

> PowerShell 设置环境变量用 `$env:QT_QPA_PLATFORM='offscreen'`。
> 个别测试依赖本机真实资源路径（如 `E:\Temp\...`），无数据时相应环节只打印不校验。

## 打包发布

```bash
# 一键打包（双击 build.bat 等价）：生成单文件便携 exe
.venv/Scripts/python.exe scripts/build.py onefile

# 或直接 pyinstaller
.venv/Scripts/python.exe -m pyinstaller --noconfirm --onefile --windowed --name MMY-FrameQuickView src/main.py
```

产物在 `dist/`，随包需带 `templates/` 目录。

## 规则模板

`templates/*.json` 定义文件夹识别规则：部位 / 方向 / 动作 / 层级顺序 / 叠层层序 /
帧命名 / 套装合并阈值等。程序启动自动扫描，顶栏可切换模板或进入编辑器。
默认模板：`templates/default.json`（层级 方向→动作，帧号同方向内跨动作连续编号）。

## 目录结构

```
MMY-FrameQuickView/
├── src/
│   ├── main.py                # 入口
│   ├── app.py                 # 主窗口
│   ├── core/                  # 扫描/模板/映射/叠层/图像处理
│   └── ui/                    # 拖拽区/部件列表/按钮矩阵/网格区/动画区
├── templates/                 # 规则模板（JSON）
├── tests/                     # 独立测试脚本（按里程碑编号）
├── 开发文档/                   # 模块化开发文档（M1–M35…）
├── build.bat / scripts/       # 一键打包
└── releases/                  # 发布包归档
```
