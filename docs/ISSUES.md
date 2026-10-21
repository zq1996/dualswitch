# 任务台账

> 格式见 `docs/COORDINATION.md` 第 2 节。**每条必须有「验收」命令**，没有就不算登记。
> 状态：`待做` → `进行中` → `已合并 <sha>`。改状态只改自己 owner 的行。

---

## ISSUE-001 没接外接屏时误报「当前画面 Windows」

- **owner**: mac
- **影响**: 两端（`app.py` 是共享代码，Windows 端 pull 即可，**不要另写一份**）
- **现象**: 拔掉外接屏启动，约 4 秒后界面变成「当前画面 Windows / 已同步外部切换 · 当前 Windows」，
  日志出现 `检测到画面已在 windows（显示器离开本机）`。根因：`detect_source()` 把「枚举不到」
  一律当成「画面在另一台」，缺 `unknown` 这一态。
- **期望**:
  - 本次会话**从未**枚举到外接屏 → 卡片显示「未检测到外接显示器」，两张卡都不点亮，
    页脚不再出现「已同步外部切换」；
  - 顺手把「切换到 …」按钮置灰或改成「重新检测」，避免盲发后报
    `Could not find a suitable external display`；
  - **保持不变**：见过屏、之后消失 → 仍判为「在另一台」（这是正常切走的信号）。
- **验收**:
  1. `QT_QPA_PLATFORM=offscreen pytest -q tests/test_source_state.py --basetemp=.pytest-tmp`
     （新增文件：三态 ①②③ —— 从未见过 / 见过后消失 / 当前可见）
  2. 真机：拔掉外接屏启动，等 10 秒，界面不出现 Windows 状态；插回屏后 5 秒内恢复正确状态。
  3. `pytest -q --basetemp=.pytest-tmp` 全绿。
- **状态**: 已合并 `d8667f1`（mac 端完成，Windows 端 pull 即生效，**无需改代码**）
  · 15 例新增测试；真机无外接屏启动 7 秒，日志不再出现「检测到画面已在 windows」
  · 全量 198 passed；`tools/check_mac_ui.py` 全部通过

---

## ISSUE-002 屏停在另一台的输入上时，本机无法切回（**双向**）

- **owner**: mac（`sync/protocol.py` + `sync/engine.py` 都在共享层；Windows 端 0 行代码）
- **影响**: 两端，两个方向都成立 —— Mac 看不到屏时请 Windows 代发，Windows 看不到屏时同样请 Mac 代发
- **现象**: 屏走 HDMI（Windows）时 macOS 枚举不到它，`m1ddc set input` 报
  `Could not find a suitable external display`；反过来屏走 USB-C 时 Windows 也看不到它。
  谁不持有画面，谁就切不回来。
- **设计**: 见 [`REMOTE-SWITCH.md`](REMOTE-SWITCH.md)。**本地优先、失败才请对端代发**，
  所以「屏在别的输入时本机还能不能枚举到」这个未验证项不再阻塞实现：
  能枚举到走本地，枚举不到才走链路。能力双向，代码只有一份（共享层）。
- **契约（`[contract]`）**: 新增 `T_SWITCH = 0x30` / `T_SWITCH_ACK = 0x31`，
  header `{"vcp":96,"value":18,"req":"<幂等 id>"}`；旧端落到「忽略未知帧」，不断连。
- **验收**:
  1. `pytest -q tests/test_remote_switch.py --basetemp=.pytest-tmp`（18 例：帧往返、超时、
     回执、拒绝、幂等、非法 VCP、本地失败回落、**两个方向都回落**、对端未就绪不发请求、
     未知态按钮 + 两端文案）
  2. 真机方向 A：屏在 HDMI，Mac 点「从 Windows 切过来」→ 3 秒内切过来；
     Windows 端日志 `替对端切屏 VCP 0x60=18 → ok`
  3. 真机方向 B：屏在 USB-C，Windows 点「从 Mac 切过来」→ 3 秒内切回；
     Mac 端日志 `替对端切屏 VCP 0x60=17 → ok`
  4. 拔掉任一端网线：按钮 250ms 内变灰
- **状态**: 已合并 `012a4f3`（mac 侧全链路 + 18 例测试）；Windows 端 pull 即可。
  真机验收（需要接显示器，两个方向各一次）待做 —— 当前机器没外接屏。
  · **2026-10-19 21:2x Windows 端本机端到端 8/8**（`tools/e2e_remote_switch.py`：
    两个真实引擎 + 真实 TCP/加密/握手；方向 A/B、幂等、非法 VCP、对端离线快速失败、
    对端重启重连、真实 CLI 执行器已挂上）。Mac 不在场，**只剩最后一步真实 DDC**：
    真机两个方向各一次，见 `REMOTE-SWITCH.md` 第 5 节的两行日志。

---

## ISSUE-003 切换页「运行中」药丸是方的（圆角没生效）

- **owner**: mac
- **影响**: 仅 mac（**Windows 端已修**，2026-10-21）
- **现象**: 切换页 hero 右上角的「运行中」标签是直角矩形，和设计稿的胶囊形不符。
- **根因**: `ui/mac2/theme.py` 里 `#pillRun` / `#pillWarn` / `#devTagOn` / `#devTagOff` / `#chip` 写的是
  `border-radius: 999px`。**Qt QSS 对超过控件半高的圆角是整条忽略**（不是夹紧到半高），
  于是变成方角。浏览器里 `999px` 是标准胶囊写法，Qt 不是。
- **期望**: **别调 QSS 圆角参数，直接自绘**。2026-10-21 Windows 端实测：19px 高的控件上，
  `border-radius` 写 9/10/999 **全是方角**（同高度 21px 的设备标签用 10px 却正常），调参是碰运气。
  建议照 Windows 的做法加一个自绘 `Pill`（`paintEvent` 里半径取「高度/2」），顺手把
  chip（24 高，12px）、设备标签（21 高，10px）也改成具体值。
- **验收**:
  1. `grep -c "999px" src/dualswitch/ui/mac2/theme.py` 输出 `0`
  2. `QT_QPA_PLATFORM=offscreen python tools/shots_v2.py` 出图，`v2-switch` 里「运行中」是胶囊形
  3. 新增一条守卫用例：自绘 pill 抓图后**角落像素 ≠ 中心像素**（方角时两者相同）
  4. `pytest -q` 全绿
- **状态**: 待做
  · Windows 参考实现：`src/dualswitch/ui/win2/widgets.py::Pill`（自绘）+ `theme.py`（chip 12 / 标签 10）
    + `tests/test_ui_win2.py::test_pill_is_drawn_with_rounded_corners` 与 `test_qss_has_no_oversized_radius`

---

## ISSUE-004 预览气泡：白卡片贴白窗口没有阴影 / 长 URL 被裁掉半截

- **owner**: mac
- **影响**: 仅 mac（**Windows 端已修**，2026-10-21）
- **现象**:
  1. 鼠标悬停缩略图弹出的预览气泡是白底卡片，压在白色主窗口上完全分不出边界（用户原话「融到一起了」）；
  2. 链接记录的预览只显示 `https://www.bilibili.com/?`，后面半截没了。
- **根因**:
  1. 设计 §6.6 要求「圆角 13，三层阴影」，实现里只有 QSS 背景色，**没有画任何投影**；
     气泡是 `WA_TranslucentBackground` 顶层窗口，卡片铺满整个窗口，没有留出画阴影的边。
  2. 正文高度用 `QFontMetrics.boundingRect(..., TextWordWrap)` 量，它的折行规则和
     `QTextLayout` 不一致：长 URL 被算成 1 行，实际排了 2 行，第二行被固定高度裁掉。
- **期望**:
  1. 气泡窗口四周留 14px 透明边，`paintEvent` 里画递减透明度的圆角矩形当投影（卡片内缩 14）；
     定位时按**卡片边**算与缩略图的间距，别把阴影算进 12px 里。
  2. 量高改用 `QLabel.heightForWidth(宽度)`（和实际排版同一个引擎），URL 能折几行就留几行。
- **验收**:
  1. `QT_QPA_PLATFORM=offscreen python tools/shots_v2.py`：`v2-preview-bubble` 四周能看到投影；
     再造一条 `https://www.bilibili.com/?spm_id_from=333.1007.0.0` 的链接记录，气泡里 URL 完整可见
  2. `pytest -q` 全绿（建议补两条：气泡窗口比卡片大一圈；链接标签 `height() >= heightForWidth(width)`）
- **状态**: 待做
  · Windows 参考实现：`src/dualswitch/ui/win2/preview.py`（`SHADOW=14` / `paintEvent` 画阴影 /
    `_measure_body_child` 用 `heightForWidth`）+ `tests/test_ui_win2.py` 的两条用例

---

## 待登记

新问题先在对话里描述清楚「什么现象 + 期望什么」，再按上面格式补一条，
**不要**先写设计文档。
