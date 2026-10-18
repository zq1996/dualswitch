"""查看 / 调整文件与目录的「强制完整性标签」（Mandatory Integrity Label）。

为什么需要它
------------
托盘图标靠 ``Shell_NotifyIcon`` 交给 explorer。Windows 的 UIPI 规则不允许**低完整性
级别**的进程向更高级别的窗口发消息 —— 本机 explorer 是提权运行的（High），所以：

  · 带 ``Low Mandatory Level`` 标签的 exe → 进程以 Low 启动 → 图标永远不出现；
  · Qt 不抛异常（``isVisible()`` 照样 True），pystray 只抛 ``[WinError 5] 拒绝访问``。

本仓库所在的 ``D:\\work`` 带 ``Low`` 标签且带 (OI)(CI) 继承，``dist`` 里打出来的 exe
就继承了它 —— 这不是代码问题。打包脚本 ``packaging/windows/build.ps1`` 现在会把产物
标签抬到 High；本脚本用来查现状、以及手工修一次。

用法
----
    python tools/fix_integrity.py                    # 只读：看仓库里有没有低标签
    python tools/fix_integrity.py --path dist        # 只读：看某个目录
    python tools/fix_integrity.py --set High --path dist/dualswitch   # 改标签（需管理员）
    python tools/fix_integrity.py --self             # 看当前进程 / explorer 的级别

注意：``--set`` 只改标签，不动 DACL；``High`` 不会「提升」进程（进程级别 = min(用户, 文件)），
只是保证它不被文件标签压在 explorer 之下。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SE_FILE_OBJECT = 1
LABEL_SECURITY_INFORMATION = 0x00000010
ERROR_SUCCESS = 0

RID_NAMES = {
    0x0000: "Untrusted",
    0x1000: "Low",
    0x2000: "Medium",
    0x2100: "Medium-Plus",
    0x3000: "High",
    0x4000: "System",
    0x5000: "Protected",
}


def _advapi():  # noqa: ANN202
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    a.GetNamedSecurityInfoW.restype = wintypes.DWORD
    a.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    a.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    a.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    a.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    return a


def read_label_rid(path: Path) -> int | None:
    """读对象的完整性标签 RID；没有标签返回 None。"""
    if os.name != "nt":
        return None
    advapi32 = _advapi()
    sacl = ctypes.c_void_p()
    desc = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        str(path),
        SE_FILE_OBJECT,
        LABEL_SECURITY_INFORMATION,
        None,
        None,
        None,
        ctypes.byref(sacl),
        ctypes.byref(desc),
    )
    if rc != ERROR_SUCCESS or not sacl.value:
        return None
    try:
        # ACL 头 8 字节（Revision/Size/AceCount/...），第一个 ACE 紧随其后
        buf = (ctypes.c_ubyte * 16).from_address(sacl.value)
        if int(buf[4]) < 1:                      # AceCount
            return None
        ace_off = 8
        if int(buf[ace_off]) != 0x11:            # SYSTEM_MANDATORY_LABEL_ACE_TYPE
            return None
        # ACE 头 4 字节 + Mask 4 字节 → SID 从 ACE 起点 +8 开始
        sid = ctypes.c_void_p(ctypes.addressof(buf) + ace_off + 8)
        count = advapi32.GetSidSubAuthorityCount(sid)[0]
        return int(advapi32.GetSidSubAuthority(sid, count - 1)[0])
    finally:
        if desc.value:
            ctypes.windll.kernel32.LocalFree(desc)


def set_label(path: Path, level: str) -> bool:
    """用 icacls 改标签（带继承，递归）。level = Low / Medium / High。"""
    args = ["icacls", str(path), "/setintegritylevel"]
    args.append(f"(OI)(CI){level}" if path.is_dir() else level)
    if path.is_dir():
        args.extend(["/T", "/C"])
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def walk(root: Path) -> list[Path]:
    items = [root]
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            items.extend(Path(dirpath) / d for d in dirnames)
            items.extend(Path(dirpath) / f for f in filenames)
    return items


def show_self() -> None:
    from dualswitch import winenv

    print("本进程  :", winenv.describe(winenv.integrity_level()))
    print("资源管理器:", winenv.describe(winenv.shell_integrity_level()))
    is_blocked, reason = winenv.tray_restriction()
    print("托盘图标:", "会被系统拒绝" if is_blocked else "可以正常注册")
    if is_blocked:
        print("原因    :", reason)
        print("修复    :", winenv.fix_hint())


def main() -> int:
    ap = argparse.ArgumentParser(description="查看 / 调整完整性标签")
    ap.add_argument("--path", default=str(ROOT), help="目标目录或文件（默认仓库根目录）")
    ap.add_argument("--set", dest="level", choices=["Low", "Medium", "High"], help="改成这个级别")
    ap.add_argument("--all", action="store_true", help="连没有标签的条目也打印")
    ap.add_argument("--self", action="store_true", help="只看当前进程与 explorer 的级别")
    args = ap.parse_args()

    if os.name != "nt":
        print("仅 Windows 需要这个工具。")
        return 0

    if args.self:
        show_self()
        return 0

    root = Path(args.path).resolve()
    items = walk(root)
    counts: dict[str, int] = {}
    low: list[Path] = []
    for item in items:
        rid = read_label_rid(item)
        if rid is None:
            counts["无标签"] = counts.get("无标签", 0) + 1
            if args.all:
                print(f"  -        {item}")
            continue
        name = RID_NAMES.get(rid, hex(rid))
        counts[name] = counts.get(name, 0) + 1
        if rid <= 0x1000:
            low.append(item)
        print(f"  {name:<8} {item}")

    print(f"\n{root}")
    print(f"共 {len(items)} 项：" + "，".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if low:
        print(
            f"\n⚠ {len(low)} 项是 Low：里面的 exe 会以低完整性级别运行，"
            "托盘图标会被系统拒绝（UIPI）。"
        )
        print("  修复：python tools/fix_integrity.py --set High --path <目录>")

    if args.level:
        print(f"\n改为 {args.level} …")
        ok = set_label(root, args.level)
        print("完成。" if ok else "失败（需要管理员权限）。")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
