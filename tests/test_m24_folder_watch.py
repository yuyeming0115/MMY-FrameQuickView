"""M24 GUI 冒烟：文件夹变更自动刷新（offscreen）。

覆盖：
1. _collect_watch_dirs：全量收集 / 超上限降级为部件级
2. 端到端：外部新增帧文件 → _on_tree_changed 防抖 → _auto_rescan → 帧数更新 + 选择/方向/动作保持
3. 外部删除帧 → 自动刷新后帧数减少
4. 开关：关闭 → 监听清空；重开 → 监听恢复
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication([])

from PIL import Image

from src.app import MainWindow


def make_png(path: Path) -> None:
    img = Image.new("RGBA", (8, 8), (200, 80, 60, 255))
    img.save(path)


tmp = Path(tempfile.mkdtemp())
try:
    # 造一个部件：E/idle 2 帧
    d = tmp / "50122101_body"
    act = d / "E" / "idle"
    act.mkdir(parents=True)
    for i in (1, 2):
        make_png(act / f"{i:04d}.png")

    win = MainWindow()
    win._on_folder_dropped(tmp)
    QApplication.processEvents()
    assert win._result is not None and len(win._result.parts) == 1

    # ---- 1) 监听目录收集 ----
    dirs = win._collect_watch_dirs(tmp)
    assert str(tmp) in [str(x) for x in dirs]
    assert str(act) in [str(x) for x in dirs], "动作目录应在监听列表（帧级增删感知）"
    print(f"[1] OK watch dirs = {len(dirs)}")

    # 超上限降级：把阈值调小，只应保留 root + 部件目录及其父
    win.WATCH_LIMIT = 1
    dirs2 = win._collect_watch_dirs(tmp)
    names = {Path(x).name for x in dirs2}
    assert "E" not in names, f"降级后不应监听方向目录: {names}"
    assert "50122101_body" in names, f"部件目录应保留: {names}"
    win.WATCH_LIMIT = 2000
    print(f"[2] OK 降级 watch dirs = {sorted(names)}")

    # ---- 2) 外部新增帧 → 自动刷新 ----
    key = win.part_list.current_key()
    d0, a0 = win.matrix.current()
    make_png(act / "0003.png")

    win._on_tree_changed(str(act))          # 模拟 directoryChanged
    assert win._rescan_timer.isActive(), "防抖 timer 应已启动"
    win._rescan_timer.stop()
    win._auto_rescan()                      # 跳过 1s 等待直接执行
    QApplication.processEvents()

    assert win.part_list.current_key() == key, "选中项应保持"
    d1, a1 = win.matrix.current()
    assert (d1, a1) == (d0, a0), f"方向/动作应保持: {(d1, a1)} != {(d0, a0)}"
    # 帧数从 2 → 3（A 区网格 cells）
    from tests.smoke_gui import _wait_workers
    _wait_workers(win)
    cells = len(win.grid_view._cells)
    assert cells == 3, f"新增帧后应为 3 帧: {cells}"
    print(f"[3] OK 新增帧自动刷新: 3 帧, 选择={key}, 方向={d1}, 动作={a1}")

    # ---- 3) 外部删除帧 ----
    (act / "0003.png").unlink()
    win._auto_rescan()
    _wait_workers(win)
    cells = len(win.grid_view._cells)
    assert cells == 2, f"删除帧后应为 2 帧: {cells}"
    print("[4] OK 删除帧自动刷新: 2 帧")

    # ---- 4) 开关 ----
    win._on_auto_refresh_toggled(False)
    assert win._dir_watcher.directories() == [], "关闭后监听应清空"
    assert not win._auto_refresh
    win._on_auto_refresh_toggled(True)
    assert win._dir_watcher.directories(), "重开后监听应恢复"
    print(f"[5] OK 开关: 重开后 watch {len(win._dir_watcher.directories())} 目录")

    print("ALL M24 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
