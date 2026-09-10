# M30：快捷文件夹 chips（收藏 + 最近）

> 分支 `feature-quick-folder-chips` · 2026-09-07

## 需求

拖入过的常用文件夹（如 `E:\Temp\天命装`、`E:\XYJProject\...\角色输出图`）希望能一键切换，
不用每次重新拖拽或在资源管理器里找路径。

## 方案（已与用户确认）

- **入口**：拖拽区内嵌一排 chips（`点这里就能切` 的直觉），无内容时整行隐藏不占布局。
- **产生机制**：自动记录最近 + 手动星标固定。
  - 拖入成功 → 自动记入「最近」：去重、最新在前、上限 **5** 个（旧的被挤掉）；
  - chip 右键 →「★ 固定为收藏」：进收藏列表（金色星标，排最前），**不占用最近名额、不被挤掉**，
    收藏上限 **8**；「☆ 取消收藏」回到最近最前；
  - chip 右键 →「🗑 从列表移除」：从收藏+最近同时删除。
- **切换行为**：左键点击 chip = 等同重新拖入该目录（走同一条 `_on_folder_dropped` 链路，
  匹配表自动发现、目录监听、左栏重建全部复用）；目录已不存在时状态栏警告、当前扫描不变。
- **持久化**：QSettings（`folders/favs` / `folders/recents`，HKCU\Software\MMY\FrameQuickView），
  重启恢复；加载时自动剔除已不存在的目录。

## 实现

| 文件 | 改动 |
|------|------|
| `src/ui/drop_zone.py` | 布局改为两行（原内容 + chips 行）；`set_quick_folders(favs, recents)` 重建 chips；信号 `quick_folder_clicked` / `quick_folder_star_toggled` / `quick_folder_removed`；chip = QToolButton，样式区分收藏（金色边框 ★）与最近（灰边框） |
| `src/app.py` | `QUICK_RECENT_MAX=5` / `QUICK_FAV_MAX=8`；`_record_quick_folder`（拖入成功后记最近）；`_on_quick_folder_clicked` / `_on_quick_star_toggled` / `_on_quick_removed`；QSettings 读写 + `_refresh_quick_chips` |
| `tests/test_m30_quick_folders.py` | offscreen 全链路：记录/去重/上限、星标与取消、点击切换、移除、重启恢复、无效目录不崩溃 |

### 坑点记录

1. **chips 行重建**：`QHBoxLayout` 的 stretch 也是 layout item——重建时必须
   `while count(): takeAt(0)` 全清再重加（含末尾 `addStretch`），若只按"保留最后一项"
   处理会误删 stretch / 漏删 chip，导致顺序错乱（首次实现踩过，测试抓出）。
2. **QSettings 单元素列表**：`value(..., type=list)` 对只有 1 个元素的列表返回 `str`
   而非 `list`——遍历前需保证存取语义一致（本次存取都用 list 写入，读端
   `_record_quick_folder` 的去重用 `in` 判断，str/list 混入会逐字符遍历，注意）。
   实际影响：读端统一 `type=list`，Qt 对多元素返回 list、单元素返回 str；
   `_load_quick_folders` 里若出现 str 会被逐字符过滤掉（目录判定全部失败）→
   表现为"重启后最近列表只剩多个时正常、只剩 1 个时丢失"。规避：读取后统一
   `raw if isinstance(raw, list) else [raw]` 归一化（已实现）。

> 注：坑点 2 的归一化如未实现，遇到「最近只剩 1 个目录重启后消失」即此因。

## M31 追加：当前目录激活态高亮（2026-09-08）

- **需求**：用户希望一眼看出当前浏览中的目录对应哪个 chip → 激活 chip 用金色高亮
  （金边 + 金字 + 淡金底 `rgba(212,175,55,0.18)` + 加粗），比收藏态（仅金字+半透明金边）更醒目。
- **实现**：
  - `drop_zone.py`：新增 `_CHIP_ACTIVE_STYLE`；`DropZone._current_folder` 记录当前目录；
    `set_current_folder(folder)` 增量刷新（`_restyle_chips` 按 `btn._folder` 重新套样式，不重建列表）；
    `_make_chip` 重建时同样应用激活态（激活态优先于收藏态）。
  - `app.py`：`_on_folder_dropped` 中调用 `self.drop.set_current_folder(folder)`。
- **细节**：路径比较用 `str(folder) == str(current)`（同一来源记录，无需大小写归一）；
  `set_current_folder` 与 `_record_quick_folder` 的 chips 重建顺序无关紧要——重建也读 `_current_folder`。

## 后续可扩展

- chip 支持中文名显示（复用匹配表）；
- ⚙ ID 菜单加「清空最近」入口；
- 拖拽区 chips 过多时换行（当前 收藏8+最近5 上限内单行可容纳）。
