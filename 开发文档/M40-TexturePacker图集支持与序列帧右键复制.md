# M40 TexturePacker 图集支持与序列帧右键复制

> 模块状态：已完成 (测试全绿)  
> 分支：`feature-atlas-and-copy-frame`  
> 涉及模块：`src/core/atlas.py`, `src/core/clipboard_util.py`, `src/core/scanner.py`, `src/core/imageops.py`, `src/ui/worker.py`, `src/ui/grid_view.py`, `src/app.py`, `tests/test_m40_atlas_and_copy_frame.py`

---

## 一、 需求背景

### 1. TexturePacker 平铺图集支持（local-role）
- **业务场景**：项目在角色输出后，通过 TexturePacker 打包为平铺图集存放于 `local-role` 目录，需支持直接拖拽该目录进行全功能预览，且具备与散帧目录完全一致的叠层、配套检查与形象组合体验。
- **文件形态**：
  - 根目录平铺 13,882 个文件（6,941 对 `.plist` + `.png`），无方向/动作子目录。
  - 命名格式：`{res_id}[_{part}]_{direction}_{action}.(plist|png)`（及少量扁平特效 `{res_id}.(plist|png)`）。
  - 图集为 Cocos2d-x Format 3 规范，包含单帧裁剪矩形 `textureRect`、旋转标记 `textureRotated`、原始尺寸 `spriteSourceSize`、偏移量 `spriteOffset`。
- **核心成果**：
  - **毫秒级极速扫描**：拖入 `local-role` 目录，扫描器纯依据文件名正则聚合生成 316 个部件与 263 个组，**耗时仅约 160ms**（绝不在扫描阶段解码图片）。
  - **无损坐标逆变换**：基于 offset 与 sourceSize 精确还原至原画布位置，旋转帧逆时针 90° 还原，实测与原始未打包散帧比对**像素级 0 误差**，叠层与动画不抖动。
  - **全体系兼容**：无缝支持同 ID 叠层（`layer_order` 排序且 `shadow` 最底）、跨 ID 形象组合（M37）、穿戴搭配（M38）、变体（M36.1 worldboss）与 fills 警告级降噪。

### 2. A 区序列帧网格右键复制原图（保留透明通道并弃掉透明像素）
- **业务场景**：美术与策划在 A 区序列帧网格中预览动作时，需要快速提取某一帧画面粘贴到 Photoshop 中拼接概念图或制作立绘草稿。
- **核心成果**：
  - **右键上下文菜单**：右键点击任一帧，弹出符合 Modern Dark UI 的菜单（字号 14px）。
  - **去透明边缘（默认核心）**：`📋 复制原图 (去透明边缘 · 0001)`，自动通过 alpha 包围盒（`getbbox`）裁剪掉四周多余透明留白，只保留有效画面像素。
  - **全尺寸复制（辅助选项）**：`📋 复制原图 (完整原尺寸 · 0001)`，保留完整原始渲染画布尺寸。
  - **跨软件透明度完美兼容**：在 Windows 平台采用 Qt MIME + 原生注册剪贴板格式 `PNG` 双重写入，严谨管理 Win32 64 位 `HGLOBAL` 句柄，确保在 Photoshop、SAI、Clip Studio、微信、QQ、PPT 中 `Ctrl+V` 直接粘贴为纯净透明图层，杜绝黑底白底问题。
  - **即时视觉反馈**：复制完成后，主窗口状态栏立即显示「已复制第 0001 帧有效原图 (宽×高) 到剪贴板，可直接在 PS 中粘贴」。

---

## 二、 架构与数据设计

### 1. 图集帧对象 `AtlasFrame`（鸭子类型）
```python
@dataclass(frozen=True)
class AtlasFrame:
    plist_path: Path
    png_path: Path
    frame_key: str                            # 如 "0001.png"
    stem: str                                 # 如 "0001"
    name: str                                 # 如 "0001.png"
    rect: tuple[int, int, int, int]           # (x, y, w, h)
    rotated: bool                             # 是否顺时针打包旋转
    offset: tuple[int, int]                   # (ox, oy)
    source_size: tuple[int, int]              # (src_w, src_h)
    canvas_bbox: tuple[int, int, int, int] | None
    canvas_size: tuple[int, int]
```

### 2. 坐标还原公式
$$\text{orig\_left} = \text{round}\left(\frac{\text{src\_w} - w}{2}\right) + \text{ox}$$
$$\text{orig\_top} = \text{round}\left(\frac{\text{src\_h} - h}{2}\right) - \text{oy}$$
$$\text{orig\_right} = \text{orig\_left} + w, \quad \text{orig\_bottom} = \text{orig\_top} + h$$

---

## 三、 测试与验证

自动化测试位于 `tests/test_m40_atlas_and_copy_frame.py`：
- `test_01_parse_atlas_filename`：涵盖常规部件、纯 ID、坐骑、扁平特效、带中文与括号的部件名（如 `503026_神龙`）、排除 4 位普通帧文件名（`0001.png`）。
- `test_02_is_atlas_root`：平铺图集目录探测（>= 2 个匹配文件）。
- `test_03_plist_parsing_and_reconstruction`：纯元数据解析与原图比对绝对 0 误差。
- `test_04_action_data_lazy_numbers_and_gaps`：`ActionData` 惰性加载同步填充 `numbers` 和 `gaps`。
- `test_05_fills_warning_in_atlas`：`scan_atlas_root` 对 fills 部件转为警告级字段。
- `test_06_worker_label_stem`：worker 帧标签正确提取为帧序号（如 "0001"）。
- `test_07_clipboard_copy_transparent`：带透明通道剪贴板写入与 Win32 内存句柄安全。
- `test_08_grid_view_copied_signal`：A 区网格右键复制信号传递。

回归测试验证：
- `tests/smoke_core.py`：PASS
- `tests/test_m37_look_combine.py`：PASS
- `tests/test_m36_worldboss_blank.py`：PASS
