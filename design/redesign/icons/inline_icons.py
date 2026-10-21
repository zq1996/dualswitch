"""把 Frset 提取图标内联进 index-v2.html 并替换手绘 SVG。"""
import re
from pathlib import Path

HTML = Path("/Users/zhqi/work/workbuddy/dualswitch/design/redesign/index-v2.html")
ICONS = Path("/Users/zhqi/work/workbuddy/dualswitch/design/redesign/icons-data.js")

html = HTML.read_text(encoding="utf-8")
icons_js = ICONS.read_text(encoding="utf-8")

# 1) 内联 ICONS（若尚未内联）
if "const ICONS" not in html:
    marker = '<script>\n(function(){'
    if marker not in html:
        marker = '<script>(function(){'
    assert marker in html, "script marker not found"
    html = html.replace(marker, '<script>\n' + icons_js + '\n(function(){', 1)
    print("inlined ICONS")
else:
    print("ICONS already present")

# 2) thumbInner：类型图标换 Frset 的
old_thumb = '''    return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--ink-3)" stroke-width="1.6" stroke-linecap="round"><rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M8.5 8h7M8.5 12h7M8.5 16h4.5"/></svg>`;'''
new_thumb = '''    return `<img src="${ICONS.typeText}" width="30" height="30" alt="">`;'''
if old_thumb in html:
    html = html.replace(old_thumb, new_thumb)
    print("thumb text icon swapped")

# link 类型
old_link = '''      return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-linecap="round"><path d="M10 14a4.5 4.5 0 006.4 0l2.6-2.6a4.5 4.5 0 10-6.4-6.4L11.2 6.4"/><path d="M14 10a4.5 4.5 0 00-6.4 0L5 12.6a4.5 4.5 0 106.4 6.4l1.4-1.4"/></svg>`;'''
new_link = '''      return `<img src="${ICONS.typeUrl}" width="30" height="30" alt="">`;'''
if old_link in html:
    html = html.replace(old_link, new_link)
    print("thumb link icon swapped")

# 3) 空态插画：换成 <img> 占位，JS 启动时从 ICONS 填 src
svgs = list(re.finditer(r'<svg class="empty-art".*?</svg>', html, re.S))
for i, m in enumerate(svgs):
    html = html.replace(m.group(0), f'<img class="empty-art" id="emptyArt{i}" alt="">', 1)
    print(f"empty art {i} -> img placeholder")

# 4) JS 启动时填充空态插画 src
fill = '''
  // 空态插画（Frset clipboard_empty 提取图）
  if(ICONS.emptyArt){
    const ea0=document.getElementById("emptyArt0"), ea1=document.getElementById("emptyArt1");
    if(ea0) ea0.src=ICONS.emptyArt;
    if(ea1) ea1.src=ICONS.emptyArt;
  }
'''
anchor = '  /* ── toast ── */'
if anchor in html and 'emptyArt0' in html and 'ICONS.emptyArt' not in html.split('/* ── toast')[0]:
    html = html.replace(anchor, fill + anchor, 1)
    print("JS fill inserted")

HTML.write_text(html, encoding="utf-8")
print("saved", HTML.stat().st_size, "bytes")
