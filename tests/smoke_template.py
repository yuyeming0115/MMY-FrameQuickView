"""模板系统冒烟测试：Template dataclass、save/load、editor 默认值。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.template import Template, load_templates, save_template


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tpl = Template(
            name="测试模板",
            folder_pattern="{id}_{part}",
            parts=["hair", "body", "shadow"],
            directions=["E", "N"],
            actions=["idle", "run"],
            hierarchy=["direction", "action"],
            layer_order=["shadow"],
            frame_pattern=r"^(\d{4})\.png$",
            extensions=[".png"],
        )
        fp = save_template(tpl, tmp_path)
        assert fp.exists(), "模板文件未写入"

        loaded = load_templates(tmp_path)
        assert len(loaded) == 1, f"应加载 1 个模板，实际 {len(loaded)}"
        assert loaded[0].name == "测试模板"
        assert loaded[0].parts == ["hair", "body", "shadow"]
        assert loaded[0].layer_order == ["shadow"]

        # parse_folder_name 验证
        assert loaded[0].parse_folder_name("50101101_hair") == ("50101101", "hair")
        assert loaded[0].parse_folder_name("50101101") == ("50101101", None)

    print("TEMPLATE SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
