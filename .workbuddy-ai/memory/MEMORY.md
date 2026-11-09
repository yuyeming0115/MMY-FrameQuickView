# 项目长期记忆（MMY-FrameQuickView）

## 环境
- 开发一律用项目 `.venv`：`D:/GitWork/MMY-FrameQuickView/.venv/Scripts/python.exe`（系统 Python 已损坏）。
- GUI 测试加 `QT_QPA_PLATFORM=offscreen`；`tests/` 下均为独立脚本（非 pytest），逐个运行。
- Git commit/push 需非沙箱执行；push 遇 schannel 失败可重试或 `-c http.sslBackend=openssl`。

## 约定
- 新功能/UI 优化 → 开功能分支 + 在 `开发文档/` 写需求方案文档 + 创建 PR，合并前必须问用户。
- 每个模块的优化/Bug 修复都要同步更新对应 `开发文档/Mxx-*.md`。
- **测试必须隔离 QSettings**：本机 `QSettings(org, app)` 总是落注册表（`setDefaultFormat/setPath` 无效），
  测试里直接 `S().clear()` 会清掉用户真实配置。做法：`appmod.QSettings = lambda *a, **k: QSettings(临时ini, IniFormat)`，
  且临时 ini 必须放在被扫描目录之外（否则写设置会触发 QFileSystemWatcher → 重扫风暴）。
- 深色主题配色：底 `#1E2023` / 卡 `#2A2E33` / 边 `#3A3F46` / 主色金 `#D4AF37` / 文字 `#E8E4D9`。
- 真实资源样本目录：`E:\XYJProject\美术资源\动画序列帧\角色输出图`（267 组，可用于实测回归）。

## 已知遗留失败测试（非回归）
- `tests/test_m21_e2e_realdir.py`：断言特效分类 == 1，真实目录已变 4 个。
- `tests/test_m40_atlas_and_copy_frame.py`：`test_03` 的 `f4.rotated` 断言失败。
