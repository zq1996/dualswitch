"""从 Frset 的 Assets.car 渲染导出指定资产 → PNG（CoreUI 私有框架）。

输出到 design/redesign/icons/frset-extracted/：
  clipboard_type_text.png / clipboard_type_url.png / clipboard_type_color.png
  clipboard_type_unknow.png / clipboard_empty.png / clipboard_copy.png
  abc_settings.png / abc_more.png / abc_clear.png / abc_colorpicker.png
  abc_screenshot.png / abc_float.png / abc_eyedropper.png / abc_color.png / abc_fan.png
"""
import objc  # noqa: F401  (pyobjc shim)
from pathlib import Path

OUT = Path("/Users/zhqi/work/workbuddy/dualswitch/design/redesign/icons/frset-extracted")
OUT.mkdir(parents=True, exist_ok=True)

CAR = "/Applications/Frset.app/Contents/Resources/Assets.car"

import Foundation  # noqa: E402
import Quartz  # noqa: E402

# 加载 CoreUI
CoreUI = objc.loadBundle(
    "CoreUI", globals(),
    "/System/Library/PrivateFrameworks/CoreUI.framework",
)
objc.loadBundleFunctions(CoreUI, globals(), [])

WANT = [
    "clipboard_type_text", "clipboard_type_url", "clipboard_type_color",
    "clipboard_type_unknow", "clipboard_empty", "clipboard_copy",
    "abc_settings", "abc_more", "abc_clear", "abc_colorpicker",
    "abc_screenshot", "abc_float", "abc_eyedropper", "abc_color", "abc_fan",
    "settings_general_unselected", "settings_general_selected_light",
    "settings_window_unselected", "settings_pro_unselected", "settings_about_unselected",
]

url = Foundation.NSURL.fileURLWithPath_(CAR)
err = None
cat = None
try:
    cat = objc.lookUpClass("CUICatalog").alloc().initWithURL_error_(url, None)
except Exception as e:
    print("catalog init via initWithURL_error_ failed:", e)

if cat is None:
    try:
        cat = objc.lookUpClass("CUICatalog").alloc().initWithURL_(url)
    except Exception as e:
        print("catalog init failed:", e)

print("catalog:", cat)
if cat is None:
    raise SystemExit(1)

sizes = [(44, 44), (24, 24), (20, 20), (18, 18), (72, 55), (40, 43)]
for name in WANT:
    done = False
    try:
        img = None
        try:
            img = cat.imageWithName_scaleFactor_(name, 2.0)
        except Exception:
            pass
        if img is None:
            print("MISS(no image)", name)
            continue
        # CUINamedImage → CGImage
        cg = None
        for attr in ("cgImage", "image", "renditionImage"):
            if hasattr(img, attr):
                try:
                    cg = getattr(img, attr)()
                    if cg:
                        break
                except Exception:
                    pass
        if cg is None:
            # 尝试 NSImage 桥接
            try:
                ns = Foundation.NSImage.alloc().initWithCGImage_size_(img.cgImage(), Foundation.NSZeroSize)
                rep = Foundation.NSBitmapImageRep.imageRepWithData_(ns.TIFFRepresentation())
                png = rep.representationUsingType_properties_(Foundation.NSBitmapImageFileTypePNG, {})
                out = OUT / f"{name}.png"
                png.writeToFile_atomically_(str(out), True)
                print("wrote", out.name, "(via NSImage)")
                done = True
            except Exception as e:
                print("MISS(no cgImage)", name, type(img).__name__, e)
            continue
        # CGImage → PNG via ImageIO
        dest = Quartz.CGImageDestinationCreateWithURL(
            Foundation.NSURL.fileURLWithPath_(str(OUT / f"{name}.png")),
            "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dest, cg, None)
        Quartz.CGImageDestinationFinalize(dest)
        print("wrote", name + ".png")
        done = True
    except Exception as e:
        print("ERR", name, e)
print("done ->", OUT)
