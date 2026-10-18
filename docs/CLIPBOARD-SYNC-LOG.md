# 剪贴板同步 · 验证记录

> 契约第 7 节的"三道闸"跑出来的实际结果。自动化部分可复现，人工部分逐条打勾。
> 最近更新：2026-10-18（macOS 端完成 P0 文字 + P1 图片）

---

## 闸一 · 契约测试（0 人工）

```bash
# 只用 mock
QT_QPA_PLATFORM=offscreen ~/.workbuddy/binaries/python/envs/default/bin/python \
    -m pytest tests/test_clipboard_contract.py -p no:cacheprovider --basetemp=.pytest-tmp
# 连真实后端（会临时占用系统剪贴板，结束时恢复文本）
DUALSWITCH_CLIP_TEST=real ... -m pytest tests/test_clipboard_contract.py -p no:cacheprovider --basetemp=.pytest-tmp
```

| 时间 | 结果 |
|---|---|
| 2026-10-18 | mock：11 passed / 2 skipped |
| 2026-10-18 | real（macOS）：18 passed，其中 2 例是真实往返（文本 / 图片） |

macOS 后端专项 10 例（`tests/test_clipboard_macos.py`，需要 `DUALSWITCH_CLIP_TEST=real`）：
双写 PNG+TIFF、TIFF→PNG、敏感标记、HTML 兜底、空内容防御，全部 PASS。

---

## 闸二 · 单机同步自测（0 人工）

```bash
~/.workbuddy/binaries/python/envs/default/bin/python tools/clip_sync_selftest.py
```

| 场景 | 结果 | 备注 |
|---|---|---|
| 文本单向 | PASS | 对端新增 1 条 |
| 图片单向 | PASS | 665B，PNG 头正确，320×200 |
| 自写回弹 | PASS | 推送 0 次 |
| 环抑制 | PASS | 连喂 10 次 → 推送 1 次 |
| 重复投递 | PASS | 强制重发 3 次，对端多写入 0 条 |
| 20MB 大图 | PASS | 1381ms，sha256 一致 |
| 断线重连 | PASS | 断开被察觉，15s 内重连并继续传 |

> 偏差说明：脚本用**两个引擎实例**而不是两个进程（单机无法同端口两次绑定），
> socket / 帧 / 握手 / 加密 / ACK 全部是真的。细节见脚本 docstring。

---

## 闸三 · 本机格式矩阵（半自动）

```bash
~/.workbuddy/binaries/python/envs/default/bin/python tools/clip_selftest.py
# 或 dualswitch clip selftest
```

| 项 | 结果 |
|---|---|
| 纯文本 ASCII | PASS |
| 中文 + emoji | PASS |
| 多行 + 制表符 | PASS |
| HTML 富文本 | PASS（html 字段非空） |
| 截图风格 PNG 2×2 | PASS（2ms） |
| 4K 大图 3840×2160 | PASS（23.7MB，36ms） |
| 敏感标记（ConcealedType） | PASS（整条跳过） |

---

## CLI 端到端（单机，用一个假对端进程）

```bash
dualswitch clip pair --pin 246810     # 两端各执行一次
dualswitch clip status
dualswitch clip send "从 Mac 手工推送的一条"
```

| 步骤 | 结果 |
|---|---|
| `clip probe` | 列出 NSPasteboard 类型、changeCount、sensitive 标记 |
| `clip get` | 类型 / 指纹 / 大小 / 文本全部正确 |
| `clip pair` | psk 写入配置，`clip status` 显示「已配对: yes」 |
| `clip send` | `已送达对端：文本 13 字`，对端进程打印 `GOT text …` |

---

## 剩下的人工验证（需要 Windows 那台机器一起）

| # | 场景 | 状态 |
|---|---|---|
| 1 | Mac 复制中文 → 切到 Win → 记事本 Ctrl+V | ☐ 待联调 |
| 2 | Win 复制 Excel 单元格 → 切到 Mac → 粘到 Numbers | ☐ 待联调 |
| 3 | Mac 截图（⌘⇧4）→ 切到 Win → 粘到微信 | ☐ 待联调 |
| 4 | Win 截图 → 切到 Mac → 粘到 Word | ☐ 待联调 |
| 5 | 连续快速切换 5 次（每次先复制） | ☐ 待联调 |
| 6 | 不复制任何东西连切 3 次 | ☐ 待联调 |
| 7 | 从 1Password 复制密码后切屏 | ☐ 待联调（本机已验证 ConcealedType 被跳过） |
| 8 | 睡眠唤醒后切屏 | ☐ 待联调 |
| 9 | 断开 WiFi 再连上 | ☐ 待联调（自测里的断线重连已覆盖同机场景） |
| 10 | 两台防火墙全开 | ☐ 待联调 |

---

## 已知限制（本轮不做）

- 不做文件 / 目录（P2），文件剪贴板既不同步也不记录。
- 不做历史落库：记录只在内存里，退出即清，最多 10 条。
- `clipboard.mode = "always"` 只是占位，当前只支持切屏时同步。
- 配对是"两端输入同一个 6 位码"，没有一次性 PIN 展示/过期机制（见契约第 11 节）。
