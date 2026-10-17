#!/usr/bin/env bash
# 开发前强制验证：m1ddc 能否在 AOC Q2790PC 上切换输入源。
# 对应需求文档 §3.2 / §9。仅在 macOS 上运行。
#
# 用法：
#   ./verify_m1ddc.sh            # 默认验证 17（Windows/HDMI）与 18（Mac/USB-C）两个方向
#   ./verify_m1ddc.sh --scan     # 值不对时，逐个候选值找真正的 USB-C 通道
#   ./verify_m1ddc.sh --status   # 只读：打印显示器列表与当前输入源，不切屏
#
# 注意：需要在 Mac 本机终端里跑（要肉眼确认画面是否切换）。
#       通过 SSH 跑只能看到命令返回码，看不到画面。

set -uo pipefail

VCP_WINDOWS="${VCP_WINDOWS:-17}"
VCP_MAC="${VCP_MAC:-18}"   # AOC Q2790PC 实测：18 = USB-C（27 会被拒绝）
SCAN_CANDIDATES=(15 16 17 18 27 28)

MODE="verify"
case "${1:-}" in
  --scan)   MODE="scan" ;;
  --status) MODE="status" ;;
  "" )      MODE="verify" ;;
  -h|--help)
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  *) echo "未知参数：$1（可用 --scan / --status / --help）" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------- 前置检查
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "本脚本只能在 macOS 上运行（当前：$(uname -s)）" >&2
  exit 2
fi

if ! command -v m1ddc >/dev/null 2>&1; then
  echo "找不到 m1ddc。先安装：" >&2
  echo "  brew install m1ddc" >&2
  exit 2
fi

echo "== 环境 =="
echo "macOS   : $(sw_vers -productVersion 2>/dev/null || echo '?') ($(uname -m))"
echo "m1ddc   : $(command -v m1ddc)"
echo

echo "== 显示器列表 =="
DISPLAY_LIST="$(m1ddc display list 2>&1 || true)"
echo "${DISPLAY_LIST:-（空）}"
if [[ -z "${DISPLAY_LIST}" || "${DISPLAY_LIST}" == *"No display"* ]]; then
  echo
  echo "[FAIL] m1ddc 看不到显示器。先解决这个，再谈切输入源：" >&2
  echo "  1) USB-C 线是否支持 DP Alt Mode（不是所有 C 口都能出画面）" >&2
  echo "  2) 显示器 OSD 里 DDC/CI 是否开启" >&2
  echo "  3) 关掉 HDR / 可变刷新率再试" >&2
  exit 1
fi
echo

CURRENT="$(m1ddc get input 2>/dev/null | tr -dc '0-9' || true)"
echo "当前输入源: ${CURRENT:-读取失败（不影响切换，dual switch 走盲发）}"
echo

if [[ "$MODE" == "status" ]]; then
  echo "（--status 只读模式，未发送任何 Set）"
  exit 0
fi

# ---------------------------------------------------------------- 交互动作
RESULT_WINDOWS="未测"
RESULT_MAC="未测"
FOUND_MAC=""

ask_switched() {
  local prompt="$1" ans
  while true; do
    read -r -p "$prompt (y/n) " ans
    case "${ans:-}" in
      y|Y) return 0 ;;
      n|N) return 1 ;;
      *) echo "  请输入 y 或 n" ;;
    esac
  done
}

send_input() {
  local value="$1" desc="$2"
  echo "------------------------------------------------------------"
  echo "即将执行：m1ddc set input $value    （$desc）"
  echo "3 秒后发送，注意看屏幕…"
  sleep 3
  if ! m1ddc set input "$value"; then
    echo "[FAIL] 命令返回非 0（见上方错误）"
    return 1
  fi
  sleep 2
  ask_switched "画面是否已切换到「$desc」？"
}

# ---------------------------------------------------------------- 模式：默认验证
if [[ "$MODE" == "verify" ]]; then
  if send_input "$VCP_WINDOWS" "Windows / HDMI 画面"; then
    RESULT_WINDOWS="成功"
  else
    RESULT_WINDOWS="失败"
  fi

  if send_input "$VCP_MAC" "Mac / USB-C 画面"; then
    RESULT_MAC="成功"
    FOUND_MAC="$VCP_MAC"
  else
    RESULT_MAC="失败"
  fi
fi

# ---------------------------------------------------------------- 模式：扫描
if [[ "$MODE" == "scan" ]]; then
  echo "逐个尝试候选 VCP 值，找出本机真正的 USB-C 通道。"
  echo "候选：${SCAN_CANDIDATES[*]}"
  echo "每试一个值等 2 秒，由你确认画面是否切到了 Mac。"
  echo
  for v in "${SCAN_CANDIDATES[@]}"; do
    if send_input "$v" "Mac / USB-C 画面（候选值 $v）"; then
      FOUND_MAC="$v"
      echo "→ 命中：USB-C 通道 = $v"
      break
    fi
  done
  # 扫描结束后尽量切回 Mac，避免停在黑屏
  if [[ -n "$FOUND_MAC" ]]; then
    echo
    echo "切回 USB-C（$FOUND_MAC）…"
    m1ddc set input "$FOUND_MAC" || true
  fi
fi

# ---------------------------------------------------------------- 结论
echo
echo "============================================================"
if [[ -n "$FOUND_MAC" && "$FOUND_MAC" == "$VCP_MAC" ]]; then
  echo "[PASS] 两个方向均成功，DDC/CI 路径成立。"
elif [[ -n "$FOUND_MAC" ]]; then
  echo "[PASS*] 能切，但 USB-C 不是 $VCP_MAC，实际值是 $FOUND_MAC。"
  echo "        需要改配置：\"sources\": { \"windows\": $VCP_WINDOWS, \"mac\": $FOUND_MAC }"
else
  echo "[FAIL] 没有找到可用的 USB-C 通道。"
  echo "       见 docs/VERIFY-M1DDC.md「失败怎么办」；若确认线材/OSD 都没问题，"
  echo "       说明 DDC/CI 路径不成立，需求 §7 的阻塞风险发生。"
fi
echo "============================================================"
echo
echo "===== 把下面这段贴回给助手 ====="
echo "macOS: $(sw_vers -productVersion 2>/dev/null || echo '?') ($(uname -m))"
echo "显示器: $(echo "${DISPLAY_LIST}" | head -1)"
echo "初始读数: ${CURRENT:-读取失败}"
echo "set input $VCP_WINDOWS (HDMI) : $RESULT_WINDOWS"
echo "set input $VCP_MAC (USB-C) : $RESULT_MAC"
echo "实际 USB-C 通道: ${FOUND_MAC:-未找到}"
echo "==============================="

[[ -n "$FOUND_MAC" ]]
