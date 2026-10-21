"""把 frset-extracted/*.png 生成 base64 JS 映射，写入 icons-data.js。"""
import base64
import json
from pathlib import Path

SRC = Path("/Users/zhqi/work/workbuddy/dualswitch/design/redesign/icons/frset-extracted")
OUT = Path("/Users/zhqi/work/workbuddy/dualswitch/design/redesign/icons-data.js")

KEYMAP = {
    "clipboard_type_text": "typeText",
    "clipboard_type_url": "typeUrl",
    "clipboard_type_color": "typeColor",
    "clipboard_type_unknow": "typeUnknown",
    "clipboard_empty": "emptyArt",
    "clipboard_copy": "copyArt",
    "abc_settings": "gear",
    "abc_more": "more",
    "abc_clear": "clear",
    "abc_colorpicker": "colorpicker",
    "abc_screenshot": "screenshot",
    "abc_float": "floatIcon",
    "abc_eyedropper": "eyedropper",
    "abc_color": "colorIcon",
    "abc_fan": "fan",
    "settings_general_unselected": "navGeneral",
    "settings_window_unselected": "navWindow",
    "settings_pro_unselected": "navPro",
    "settings_about_unselected": "navAbout",
}

out = {}
total = 0
for name, key in KEYMAP.items():
    p = SRC / f"{name}.png"
    if not p.exists():
        print("MISS", name)
        continue
    b = base64.b64encode(p.read_bytes()).decode()
    out[key] = f"data:image/png;base64,{b}"
    total += len(b)

js = "/* Frset Assets.car 提取图标（设计原型参考用） */\nconst ICONS = " + json.dumps(out, indent=2) + ";\n"
OUT.write_text(js, encoding="utf-8")
print("wrote", OUT.name, "keys:", len(out), "raw b64 chars:", total)
