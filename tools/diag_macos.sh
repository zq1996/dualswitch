#!/usr/bin/env bash
# macOS 侧 DDC/CI 诊断：定位「m1ddc 命令执行了但显示器没反应」到底卡在哪一层。
#
# 用法：
#   ./diag_macos.sh               # 只读诊断（默认，安全）
#   ./diag_macos.sh --write-test  # 额外做一次亮度写入测试，用来判断 DDC 写通道是否通
#
# 判断逻辑：
#   亮度能读能写  → DDC 通道正常，问题在「输入源 VCP 0x60」或「VCP 值不对」
#   亮度读不到    → DDC 通道没建立，问题在连接/线材/OSD 开关/芯片支持
#   看不到显示器  → macOS 根本没识别到外接屏，先解决连接

set -uo pipefail

MODE="read"
case "${1:-}" in
  --write-test) MODE="write" ;;
  "" ) MODE="read" ;;
  -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "未知参数：$1" >&2; exit 2 ;;
esac

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "本脚本只能在 macOS 上运行（当前：$(uname -s)）" >&2
  exit 2
fi

HAS_M1DDC=0
command -v m1ddc >/dev/null 2>&1 && HAS_M1DDC=1

echo "############################################################"
echo "# 1. 系统与工具"
echo "############################################################"
echo "macOS      : $(sw_vers -productVersion 2>/dev/null || echo '?') ($(sw_vers -buildVersion 2>/dev/null || echo '?'))"
echo "架构       : $(uname -m)   $( [[ "$(uname -m)" == "x86_64" ]] && echo '← Intel Mac，m1ddc 不支持！')"
echo "机型       : $(sysctl -n hw.model 2>/dev/null || echo '?')"
echo "m1ddc      : $(command -v m1ddc 2>/dev/null || echo '未安装')"
[[ $HAS_M1DDC -eq 1 ]] && echo "m1ddc 版本 : $(m1ddc --version 2>&1 | head -1 || echo '?')"
echo

echo "############################################################"
echo "# 2. macOS 是否识别到外接显示器"
echo "############################################################"
echo "（如果这里只有内置屏，说明显示器当前没在给 Mac 出画面，"
echo "  m1ddc 当然控不到它 —— 先确认显示器是不是正显示 Windows 画面）"
echo
system_profiler SPDisplaysDataType 2>/dev/null | grep -E "Displays:|Resolution|UI Looks like|Display Type|Main Display|Mirror|Connection Type|Online|Automatically Adjust|Framebuffer" | sed 's/^ *//'
echo

echo "############################################################"
echo "# 3. m1ddc 显示器列表"
echo "############################################################"
if [[ $HAS_M1DDC -eq 1 ]]; then
  OUT="$(m1ddc display list 2>&1)"; RC=$?
  echo "$OUT"
  echo "exit=$RC"
  [[ -z "$OUT" || $RC -ne 0 ]] && echo ">>> 空/失败：m1ddc 找不到可控制的外接显示器"
else
  echo "跳过（m1ddc 未安装）"
fi
echo

echo "############################################################"
echo "# 4. DDC 读值测试（逐项独立，失败不影响后续）"
echo "############################################################"
if [[ $HAS_M1DDC -eq 1 ]]; then
  probe() {
    local label="$1"; shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    printf '%-26s rc=%-3s %s\n' "$label" "$rc" "$(echo "$out" | head -2 | tr '\n' ' ')"
  }
  probe "get input (0x60)"    m1ddc get input
  probe "get luminance (0x10)" m1ddc get luminance
  probe "get contrast (0x12)"  m1ddc get contrast
  probe "get volume (0x62)"    m1ddc get volume
  echo
  echo "--- 多显示器逐个试（若上面失败，可能索引不对）---"
  for d in 1 2 3; do
    out="$(m1ddc display "$d" get input 2>&1)"; rc=$?
    printf 'display %s get input  rc=%-3s %s\n' "$d" "$rc" "$(echo "$out" | head -1)"
  done
else
  echo "跳过（m1ddc 未安装）"
fi
echo

if [[ "$MODE" == "write" && $HAS_M1DDC -eq 1 ]]; then
  echo "############################################################"
  echo "# 5. 写入测试（改亮度，看完会还原）"
  echo "############################################################"
  CUR="$(m1ddc get luminance 2>/dev/null | tr -dc '0-9')"
  if [[ -z "${CUR:-}" ]]; then
    echo ">>> 读不到当前亮度，跳过写入测试（说明 DDC 读通道就不通）"
  else
    if [[ "$CUR" -gt 40 ]]; then NEW=$((CUR - 25)); else NEW=$((CUR + 25)); fi
    echo "当前亮度 $CUR，改成 $NEW …"
    if m1ddc set luminance "$NEW"; then
      sleep 1
      read -r -p "屏幕亮度有没有明显变化？(y/n) " ans
      m1ddc set luminance "$CUR" >/dev/null 2>&1 || true
      echo "已还原为 $CUR"
      if [[ "${ans:-}" == "y" ]]; then
        echo ">>> [关键结论] DDC 写通道正常。问题不在连接，而在「输入源指令」本身。"
      else
        echo ">>> [关键结论] 命令成功但亮度没变：DDC 指令被显示器忽略（通道通、执行不通）。"
      fi
    else
      echo ">>> set luminance 失败，DDC 写通道不通"
    fi
  fi
  echo
fi

echo "############################################################"
echo "# 6. 回传报告"
echo "############################################################"
echo "===== 把下面这段贴回给助手 ====="
echo "macOS: $(sw_vers -productVersion 2>/dev/null) / $(uname -m) / $(sysctl -n hw.model 2>/dev/null)"
echo "m1ddc: $(command -v m1ddc 2>/dev/null || echo 未安装)"
echo "--- display list ---"
m1ddc display list 2>&1 | head -5
echo "--- 读数 ---"
for c in input luminance contrast volume; do
  printf '%s=%s\n' "$c" "$(m1ddc get $c 2>&1 | head -1)"
done
echo "--- 显示器是否被 macOS 识别 ---"
system_profiler SPDisplaysDataType 2>/dev/null | grep -cE "Resolution" | xargs -I{} echo "Resolution 行数: {}"
system_profiler SPDisplaysDataType 2>/dev/null | grep -E "Display Type" | sed 's/^ *//'
echo "==============================="
