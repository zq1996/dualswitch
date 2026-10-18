# 剪贴板同步 · 设计与并行开发契约

> 2026-10-18 立 · 适用 `src/dualswitch/clipboard/` 与 `src/dualswitch/sync/`
> **这份文档是给"另一端的 AI"看的**。开工前读第 1、3、5、7 节，动手时只碰第 5 节分配给你的文件。
> 方案背景与工作量评估见 `docs/CLIPBOARD-SYNC-PLAN.md`（只看结论的话：文字简单、图片中等、文件暂不做）。

---

## 0. 两端各自的入口（先看这两条）

### Windows 端 AI —— 你要做的事

```powershell
git pull --rebase
python -m pytest tests/test_clipboard_contract.py -q     # 先确认基线是绿的（应为 11 passed, 2 skipped）

# 然后只写这两个文件：
#   src/dualswitch/clipboard/windows.py     ← 平台后端（唯一归属你的源码）
#   tests/test_clipboard_windows.py         ← 你的测试
# 最后跑：
$env:DUALSWITCH_CLIP_TEST="real"; python -m pytest tests/test_clipboard_contract.py -q -s
python tools/clip_selftest.py --role windows
```

**其余文件一律只读**（尤其是 `clipboard/base.py`、`config.py`、`app.py`、`ui/`、`sync/`）。
需要改契约时，不要直接改，把需求写进第 11 节「变更请求」，由 Mac 端改完你再 pull。

> **状态（2026-10-18 18:2x）：两端都已落地，代码已在 `main` 上合并。**
> **Windows**：`clipboard/windows.py` + `tests/test_clipboard_windows.py`（58 例）已落地；
> 契约测试带 `DUALSWITCH_CLIP_TEST=real` 时 14 passed / 3 skipped。
> 第 6 节 M1 里那三个「本次唯一的未知数」已实测，结论写在 `windows.py` 文件头表格里：
> 截图工具给 `CF_DIB` 32bpp、浏览器额外给注册格式 `PNG`（优先取）、序列号先于数据变化。
> 顺手修了本文件第 7 节闸一里快照助手的崩溃（见第 11 节，**未动任何断言**）。
> **macOS**：网络层 / 引擎 / UI / 自测工具已全部落地，`tools/clip_selftest.py` 现在也存在了。
> **联调的完整流程、已踩过的坑、需要用户做的事**，见
> [`docs/CLIPBOARD-SYNC-HANDOFF.md`](CLIPBOARD-SYNC-HANDOFF.md)（联调前必读）。
> 合并后全量：**216 passed / 5 skipped**（Windows 本机，`QT_QPA_PLATFORM=offscreen`）。

### macOS 端 AI —— 你要做的事

按第 6 节的 M1 → M5 顺序实现：`clipboard/macos.py` → `sync/*` → 接线 → UI → 自测工具。

**2026-10-18 进度：M1–M5 全部落地**（文字 + 图片），自测见第 7 节两道闸。macOS 侧现状：

| 里程碑 | 文件 | 状态 |
|---|---|---|
| M1 后端 | `clipboard/macos.py` | ✅ NSPasteboard 直连，契约测试真实后端 18 例通过 |
| M2 网络 | `sync/{protocol,transport,discovery,engine}.py` | ✅ 帧/加密/对称 TCP/发现/引擎全实现 |
| M3 接线 | `config.py` `app.py` `settings.py` | ✅ `ClipboardConfig`、`on_before_switch` 钩子、剪贴板页签 |
| M4 界面 | `ui/clipboard_page.py` + `window.py` + `runner.py` | ✅ 主窗口内一页，列表 5 行，高度 620 |
| M5 自测 | `tools/clip_selfcheck` / `tools/clip_sync_selftest.py` | ✅ 格式矩阵 7/7、单机 7 场景 7/7 |

**Windows 端需要注意的三处实现细节**（都不改你代码，但会影响联调）：

1. 帧 header 里我们**总会写 `plen`**（payload 明文长度）。你只写 `image.size` 也能互通，
   接收端优先 `plen`、缺省回落 `image.size`。
2. 配对的盐是**固定串** `dualswitch-v1-clipboard`，不是 `device_id_A`（见第 11 节）。
   两端用 `dualswitch clip pair --pin <同一个6位码>` 即可得到同一个 key。
3. `clipboard.peer_port` 新增（默认 0 = 与 `port` 相同），只用于单机自测起两个端口。


---

## 1. 范围（这次只做两件事）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0** | 文字（含 HTML 富文本降级为纯文本） | 本次目标 |
| **P1** | 图片（统一 PNG，≤32MB） | 本次目标 |
| **P2** | 文件 / 目录 | **本次不做**，接口预留但不实现 |

**非目标**（明确不做，避免范围膨胀）：

- 不做多设备（只支持"另一台"，`other_host()` 已经定义了谁是另一台）
- 不做历史记录（剪贴板只保留"最近一次"，不落库）
- 不做公网中继（只局域网）
- 不做常驻同步（只在切屏那一刻传；配置项 `mode=always` 预留但不在本次实现）

---

## 2. 核心机制：为什么是"切屏触发"

双系统 + 单显示器 = 同一时刻只有一台机器被人使用。所以剪贴板只需要在**切换的那一瞬**搬运：

```
按下热键（AnyMachine）
   │
   ├─① 同步读本机剪贴板（本地操作，<5ms）→ 入发送队列
   │
   └─② 立即发 DDC 切换（30ms，用户感知不到等待）
         │
         └─ 后台线程把队列里的内容推给对端（文本 ~40ms，图片 ~150ms）
```

**关键约束：同步绝不能阻塞切屏。** 顺序是"先读、再切、后传"，不是"先传完再切"。
`SwitchController.trigger()` 已经是"只入队就返回"的设计，hook 挂在同一个位置即可（见第 6 节 M3）。

---

## 3. 冻结契约（`clipboard/base.py`）

这个文件已经写好并提交，**两端都按它实现**。`tests/test_clipboard_contract.py` 是它的可执行版本。

### 3.1 数据模型

```python
ClipItem(kind: "text"|"image"|"mixed", text, html, image, ts, source, digest)
ImageData(png: bytes, width: int, height: int)
```

| 字段 | 约定 |
|---|---|
| `kind` | 三种之一；`mixed` = 图 + 附带文字（如从浏览器复制带说明的图） |
| `text` | UTF-8，≤2MB，超限截断（不抛异常） |
| `html` | 富文本。P0 只用于"原样搬运"，不做 HTML→纯文本转换（目标应用自己会降级） |
| `image.png` | **必须是 PNG 字节**。不接受 TIFF / DIB / QImage。转换在平台后端里做 |
| `digest` | `sha256(text + 0x00 + html + 0x00 + png)`，**不含 ts/source**（否则永远不相等） |

### 3.2 后端协议（三个方法）

| 方法 | 必须满足的语义 |
|---|---|
| `poll() -> ClipItem \| None` | ① 内容没变 → `None`；② 空/不支持的类型/命中 `SKIP_MARKERS_*` → `None`；③ 图片超 `MAX_IMAGE_BYTES` → `None`；④ **`write()` 之后由它引起的那次变更 → `None`** |
| `write(item)` | 写进去后本地必须能**直接粘贴**：文本带 plain text；图片必须同时带 PNG（macOS 还要 TIFF、Windows 还要 CF_DIB），否则部分应用粘不出来 |
| `probe() -> dict[str,str]` | 自检信息，`doctor` / 自测脚本打印。必须 <50ms |
| `close()` | 释放资源，可重复调用 |

**第 ④ 条是环抑制的第一道闸，务必实现。** 平台做法：

- macOS：`write()` 后记 `pb.changeCount()`，`poll()` 时若等于记录值直接返回 `None`
- Windows：`write()` 后记 `GetClipboardSequenceNumber()`，同上

### 3.3 变更规则

新增字段 = 改 `base.py` + 改本表 + 改 `test_clipboard_contract.py`，**三处同一次提交**。
只改一端的实现不改契约，`test_chrome_contract.py` 的同类检查会拦住你。

---

## 4. 线上协议（P0 定死，别自由发挥）

### 4.1 端点

| 项 | 值 | 说明 |
|---|---|---|
| TCP 端口 | `47823` | 双向对称：**两端都监听，也都主动连**。没有 server/client 之分 |
| UDP 端口 | `47824` | 发现广播，失败时用手填 IP 兜底 |
| 设备 ID | `{host}-{8位hex}` | 首次运行生成，写进 `config.json` 的 `clipboard.device_id` |

**对称设计是刻意的**：两端代码几乎一样，谁的实现都不特殊，联调时少一半"到底哪边的问题"。

### 4.2 帧格式

```
偏移  长度  字段
0     4     magic  = b"DSYN"
4     1     version= 1
5     1     type   见 4.3
6     4     header_len（大端 uint32）
10    N     header  = UTF-8 JSON
10+N  M     payload = 原始字节（加密后），长度 = 帧总长 - 10 - N
```

连接上连续多帧，按长度切。**必须先读 10 字节头再读 body**，不要 `recv(65536)` 一把梭。

### 4.3 消息类型

| type | 名称 | header 关键字段 | payload |
|---|---|---|---|
| `0x01` | HELLO | `device`, `host`, `proto`, `auth`, `nonce` | 空 |
| `0x02` | HELLO_OK | `device`, `host`, `ok`, `reason` | 空 |
| `0x10` | ONECLIP | `digest`, `ts`, `kind`, `text?`, `html?`, `image?{w,h,size}` | PNG（有图时） |
| `0x11` | ACK | `digest`, `ok`, `detail` | 空 |
| `0x20` | PING | `ts` | 空 |
| `0x21` | PONG | `ts` | 空 |
| `0x7f` | ERROR | `code`, `msg` | 空 |

P0 只实现 `HELLO / HELLO_OK / ONECLIP / ACK / PING / PONG`。**没有 PULL/分块**——P1 的图片最大 32MB，
一次写完交给 TCP 分片即可；分块是 P2（文件）才需要的。

### 4.4 认证与加密

**配对**（一次性，两端各做一次）：

```
A 端界面显示 6 位 PIN（随机，5 分钟有效）
B 端输入 PIN → 计算 key = PBKDF2-HMAC-SHA256(PIN, salt=device_id_A, 200_000)
            → 连 A，HELLO 里带 auth = HMAC(key, nonce)
A 端同法算 key，校验通过 → HELLO_OK，双方把 key 存进 config（clipboard.psk）
```

**每帧**：`seal(key, plaintext)` = `SHA256(key + counter)` 生成 keystream，XOR 后附 `HMAC-SHA256(key, ciphertext)[:16]`。
`counter` 每条消息自增，接收端校验单调（防重放）。

> 说明：这是应用层自研的"够用级"加密，不是经过审计的 AEAD。威胁模型只覆盖"同一局域网有人抓包"，
> 不防"已经拿到 PIN 且能中间人"。P2 如果要求更高，换 TLS 重写 `transport.py` 的 40 行即可，协议层不用动。
>
> **调试开关**：`clipboard.debug_plain = true` 时跳过加密（帧头加 `"enc": false`），方便用抓包看内容。
> 默认关闭，且写入配置时在日志里记一条 warning。

### 4.5 环抑制（三层，缺一不可）

| 层 | 机制 | 归属 |
|---|---|---|
| 1 | 后端 `write()` 吞掉自写回弹 | `clipboard/*.py` |
| 2 | `digest == last_applied_digest` 直接丢弃 | `sync/engine.py` |
| 3 | `ts` 单调：旧内容永不覆盖新内容 | `sync/engine.py` |

联调时如果出现"两台机器互相刷屏"，按这个顺序查：先看层 1 的单元测试过没过。

---

## 5. 文件所有权（防冲突的唯一规则）

> **规则：不在你名下的文件，只读。** 需要改，写进第 11 节，等所有者改完再 pull。

| 路径 | 归属 | 说明 |
|---|---|---|
| `src/dualswitch/clipboard/base.py` | **macOS** | 冻结契约，已写好 |
| `src/dualswitch/clipboard/__init__.py` | **macOS** | 导出表 |
| `src/dualswitch/clipboard/mock.py` | **macOS** | 契约参考实现 |
| `src/dualswitch/clipboard/macos.py` | **macOS** | — |
| `src/dualswitch/clipboard/windows.py` | **Windows** | **你唯一要写的源码** |
| `src/dualswitch/sync/*.py` | **macOS** | 网络/协议/引擎，平台无关 |
| `src/dualswitch/ui/clipboard_page.py` | **macOS** | 剪贴板页主体（共享代码，用 `chrome()` 取外壳） |
| `src/dualswitch/ui/settings.py` `runner.py` `window.py` `widgets.py` | **macOS** | 含新增的「剪贴板」设置页与页面栈接线 |
| `src/dualswitch/ui/chrome/win.py` | **Windows** | 标题栏加剪贴板图标 + 两个新字段（约 12 行） |
| `src/dualswitch/ui/chrome/mac.py` | **macOS** | 列表加「剪贴板」行 + 两个新字段 |
| `src/dualswitch/config.py` | macOS 加完字段后冻结 | Windows 端只读 |
| `src/dualswitch/app.py` | macOS 加完 hook 后冻结 | Windows 端只读 |
| `tests/test_clipboard_contract.py` | **共用** | 两端都要跑通，谁都不许改断言 |
| `tests/test_clipboard_windows.py` | **Windows** | — |
| `tools/clip_selftest.py` | macOS | 两端都要跑，但不改 |
| `docs/CLIPBOARD-SYNC-CONTRACT.md` | 共用 | 各自只追加自己那一节 |

### Git 纪律（5 条，照着做就不会撞）

1. 开工前 `git pull --rebase`；收尾 `git add <自己名下的文件>`，**永远不要 `git add -A`**
2. 不在别人的文件上跑格式化（`black .` / `isort .` 全仓会制造大量无意义 diff）
3. 小步提交：每完成一个"能跑通的最小单元"就提交一次，别攒一整天
4. 冲突时以**所有权**为准，不以"谁的改动更新"为准
5. 提交信息带上归属前缀：`[clip-win]` / `[clip-mac]`

---

## 6. 里程碑与调用链

### M1 · 平台后端（可并行，两端各干各的）

```
clipboard/macos.py    MacClipboardBackend   ~180 行
clipboard/windows.py  WindowsClipboardBackend ~140 行
```

**共同要求**：实现 `poll / write / probe / close`，跑通 `test_clipboard_contract.py`。

macOS 侧要点（已实测，别再踩）：

- **没有剪贴板变更事件**，`QClipboard.dataChanged` 实测 0 次触发 → `poll()` 里比对 `NSPasteboard.changeCount()`
- 写图片：只写 `public.tiff` 会让浏览器粘不出来 → pyobjc 直接写 `public.png` + `public.tiff` 两份
- 读图片：`QClipboard` 只给 `application/x-qt-image` → 自己 `QImage.save()` 转 PNG
- 文件剪贴板不用管（P2）

Windows 侧要点（**需要你实测确认，这三条是本次唯一的未知数**）：

- 从资源管理器复制文件时，Qt 是否稳定给出 `urls`（P2 才用，先记结论）
- 图片在剪贴板里是 `CF_DIB` 还是 `CF_PNG`；`CF_DIB` → PNG 的转换是否有 alpha / 行序问题
- **`GetClipboardSequenceNumber` 在多少 ms 内稳定**（决定 `poll()` 之后要不要 `sleep(10)` 再读，
  经典竞态：序列号变了但数据还没写好）

### M2 · 网络层（Mac 独占，平台无关）

```
sync/protocol.py   帧编解码 + seal/unseal + ClipItem ↔ header 映射   ~150 行
sync/transport.py  对称 TCP 服务 + 连接管理 + 心跳 + 重连退避         ~200 行
sync/discovery.py  UDP 广播 + 手填 IP 兜底                            ~90 行
sync/engine.py     轮询剪贴板 → 推送 → 落盘 → 写回 → 环抑制          ~200 行
```

`engine` 的对外接口（UI 和 `app.py` 只用这三个）：

```python
class SyncEngine:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def push_now(self, reason: str = "hotkey") -> None: ...   # 切屏时调用，必须 <5ms 返回
    def status(self) -> dict[str, str]: ...                   # 给 UI 状态卡 / doctor 用
```

### M3 · 接线（Mac 独占，一次性，之后冻结）

改动点只有三处，改完 Windows 端就不用碰了：

| 文件 | 改动 |
|---|---|
| `config.py` | 新增 `ClipboardConfig`（`enabled/image_enabled/port/mode/device_id/peer/psk/max_image_mb/debug_plain`）+ `_merge_clipboard()` |
| `app.py` | `SwitchController` 增加可选的 `on_before_switch` 回调列表；`trigger()` 里遍历调用（try/except 包住，异常不能影响切屏） |
| `settings.py` | 页签列表加 `"剪贴板"`，`stack.addWidget(self._page_clipboard())`，新方法放在**文件末尾**（减少与将来改动的重叠） |

### M4 · UI（页面共享 / 入口各归各的）

设计稿与规格见 [`docs/UI-DESIGN-CLIPBOARD.md`](UI-DESIGN-CLIPBOARD.md)，
可交互稿 `design/clipboard/index.html`（`?page=clip` 剪贴板页，`?empty=1` 空态）。

**硬规则：不新增窗口。** 剪贴板是 `MainWindow.pages`（`QStackedWidget`）里的一页，
和设置页同级，翻进去再返回 —— 与现有 `attach_settings / show_settings_page` 那套完全对称。

新增 `ui/clipboard_page.py`（共享，Mac 端写）：`ClipboardPage(QWidget)`，
`backRequested` 信号 + `refresh()`，依赖 `engine.records()` 返回的 `ClipRecord` 列表。

入口按平台习惯分开，**不要强行统一**：

| 平台 | 剪贴板入口 | 归属 | 改动 |
|---|---|---|---|
| Windows | 标题栏图标（齿轮左边） | **Windows 端** | `chrome/win.py` 约 12 行 |
| macOS | 列表里「剪贴板」行（设置行上方） | macOS 端 | `chrome/mac.py` |
| 两端 | 设置 → 剪贴板页签 → 「查看记录…」 | macOS 端 | `settings.py` |

界面契约新增两个字段（`chrome/base.py`，两端同时加）：

| 字段 | Windows | macOS | 用途 |
|---|---|---|---|
| `INLINE_CLIPBOARD_ROW` | `False` | `True` | 入口放列表还是标题栏 |
| `EXTRA_H` | `0` | `20` | 主窗口额外高度（mac 多一行列表） |

设置页内容：同步开关（文字 / 图片）、配对状态、图片上限、`debug_plain`（藏在高级页）、查看记录入口。

### M5 · 自测工具（Mac 独占，但两端都要用）

见第 7 节。**这是"少人工调试"的核心，优先级高于 UI。**

### 依赖关系

```
M1(mac) ─┐
         ├─► M2 ─► M3 ─► M4
M1(win) ─┘         │
                   └─► M5 ─► 联调（唯一人工环节）
```

两端 M1 完全并行，互不阻塞。Windows 端在 M2 完成前只能自测后端（跑契约测试 + `clip_selftest --role windows`），
这段时间它可以把 Windows 侧三个未知数摸清楚——**那正好是最需要真机验证的部分**。

---

## 7. 少人工调试：三道自动化闸

目标：把"必须两台机器同时在手"的验证从 ~100 项压到 **10 项以内**。

### 闸一 · 契约测试（两端各自跑，0 人工）

`tests/test_clipboard_contract.py` 已就绪，11 个用例覆盖去重、自写回弹、Unicode 往返、
PNG 往返、超限拒绝、probe 字段、失败抛错、digest 稳定性、kind 自洽、降级。

```bash
pytest tests/test_clipboard_contract.py -q                            # mock，任何环境
DUALSWITCH_CLIP_TEST=real pytest tests/test_clipboard_contract.py -q  # 真实后端（会临时占用剪贴板，内部会恢复文本）
```

### 闸二 · 单机双进程模拟（**最重要**，0 人工，没有第二台机器也能验 70%）

`tools/clip_sync_selftest.py`：在**一台机器**上起两个进程，各用一个临时配置目录 +
`MockBackend`，通过 `127.0.0.1:47823/47824` 互相连接，自动跑完：

| 场景 | 断言 |
|---|---|
| 文本单向 | A.feed → 200ms 内 B.written 出现同 digest |
| 图片单向 | 同上，且 B 拿到的是 PNG |
| 自写回弹 | A 写完后 A 不再推送，消息计数不增长 |
| 环抑制 | 连续 feed 同一内容 10 次，只产生 1 次传输 |
| 断线重连 | 杀掉一端监听，3s 内自动重连成功 |
| 大图 | 20MB PNG 完整到达，sha256 一致 |
| 乱序/重复 | 重复投递同一 digest，接收端只应用一次 |

```bash
python tools/clip_sync_selftest.py            # 全自动，输出 PASS/FAIL 表 + 退出码
python tools/clip_sync_selftest.py --case text,echo,reconnect
```

**这个脚本写出来之后，协议层的 bug 基本不可能漏到真机联调。**

### 闸三 · 本机格式矩阵（半自动）

`tools/clip_selftest.py --role mac|windows`：往本机剪贴板依次放 6 类内容，
再按平台能力逐项回读校验（不是验证"能不能粘到微信里"，那个只能人工）：

```
[PASS] 纯文本 ASCII        写入 24B  读回一致
[PASS] 中文 + emoji        写入 68B  读回一致
[PASS] 多行 + 制表符        写入 41B  读回一致
[PASS] HTML 富文本         写入 310B 读回 html 非空
[PASS] 截图风格 PNG 2×2     写入 78B  读回 PNG 头正确、2×2
[PASS] 4K 大图 3840×2160    写入 8.9MB 读回 PNG、耗时 142ms
[FAIL] 敏感标记           期望跳过，实际读到了 ← 密码管理器标记没生效
```

输出一张表 + 退出码，失败项附"该改哪个函数"。

### 剩下的 ~10 项人工验证（真机联调时一次做完）

| # | 场景 | 期望 |
|---|---|---|
| 1 | Mac 复制中文 → 切到 Win → 记事本 Ctrl+V | 内容正确，无乱码 |
| 2 | Win 复制 Excel 单元格（富文本）→ 切到 Mac → 粘到 Numbers | 至少纯文本正确 |
| 3 | Mac 截图（⌘⇧4）→ 切到 Win → 粘到微信 | 图片出现，不失真 |
| 4 | Win 截图 → 切到 Mac → 粘到 Word | 同上 |
| 5 | 连续快速切换 5 次（每次先复制） | 每次内容都对，无串台 |
| 6 | 在不复制任何东西的情况下切 3 次 | 对端剪贴板不被清空/覆盖 |
| 7 | 从 1Password 复制密码后切屏 | 对端粘不出密码 |
| 8 | 睡眠唤醒后切屏 | 3s 内恢复同步 |
| 9 | 断开 WiFi 再连上 | 自动重连，不弹错 |
| 10 | 两台机器的防火墙全开 | 首次连接有明确提示，不是静默失败 |

---

## 8. CLI（调试的主要入口，**先做这个再做 UI**）

```bash
dualswitch clip status                 # 后端名 / 设备ID / 对端 / 连接状态 / 上次同步
dualswitch clip probe                  # 打印本机剪贴板的全部类型（两端格式对照用）
dualswitch clip send "hello"           # 手工推送一条文本到对端
dualswitch clip send --image shot.png  # 手工推送一张图
dualswitch clip get                    # 打印当前剪贴板（校验用）
dualswitch clip pair --pin 123456      # 配对
dualswitch clip selftest               # 本机格式矩阵
```

**为什么先做 CLI**：真机联调时"切来切去看 UI"很慢，命令行一行能定位是"没读到 / 没发出 / 没收到 / 没写入"的哪一环。

---

## 9. 已知坑（都实测过，别重复踩）

| # | 现象 | 结论 |
|---|---|---|
| 1 | `QClipboard.dataChanged` 在 macOS 上永不触发 | 必须轮询 `changeCount`，100ms 一次，单次 ~3μs |
| 2 | Qt 写图片到剪贴板只有 `public.tiff` | 浏览器/Electron 粘不出来，必须补 PNG |
| 3 | Qt 读图片是 `application/x-qt-image` | 不是标准 MIME，必须 `QImage.save()` 转 PNG |
| 4 | `Qt` 的 `QUrl` 列表 ↔ `public.file-url` 正常 | P2 的文件剪贴板不用碰 `CF_HDROP` 裸 API |
| 5 | `org.nspasteboard.ConcealedType` 可读可写 | 敏感内容过滤低成本可实现 |
| 6 | 序列号在数据落地前就变了（Windows 经典竞态） | `poll()` 读失败时 `sleep(10ms)` 重试一次，别死等 |
| 7 | macOS 文件名可能是 NFD 分解形式 | 跨平台传文件名前 `unicodedata.normalize("NFC", name)`（P2 用） |
| 8 | `offscreen` 平台的 `QClipboard` 是空实现（`mimeData()` 返回 `None`） | 剪贴板相关测试**不能用** `QT_QPA_PLATFORM=offscreen`，要么用 mock，要么起真实的 cocoa |

---

## 10. 验收标准（贴进 PR 描述，逐条打勾）

**Windows 端**

- [ ] `pytest tests/test_clipboard_contract.py -q` → 11 passed, 2 skipped
- [ ] `$env:DUALSWITCH_CLIP_TEST="real"; pytest tests/test_clipboard_contract.py -q` → 13 passed
- [ ] `python tools/clip_selftest.py --role windows` → 全 PASS（敏感标记那项允许 MANUAL）
- [ ] `dualswitch clip probe` 输出的类型列表贴在 PR 里（两端对照用）
- [ ] 资源管理器复制 3 个文件时，`probe` 里能看到 `CF_HDROP`（P2 备料，不实现）
- [ ] 只改了 `clipboard/windows.py` 和 `tests/test_clipboard_windows.py`（`git diff --stat` 自证）

**macOS 端**

- [ ] `pytest -q` 全套 96+ 用例仍全绿（不许打挂既有功能）
- [ ] `tools/check_mac_ui.py` 仍 34 项通过
- [ ] `python tools/clip_sync_selftest.py` → 7 个场景全 PASS
- [ ] 第 7 节表格里 10 项人工验证通过，结果记到 `docs/CLIPBOARD-SYNC-LOG.md`

---

## 11. 变更请求（需要改契约时写在这里，不要直接改文件）

| 日期 | 提出方 | 要改什么 | 原因 | 处理 |
|---|---|---|---|---|
| 2026-10-18 | windows | 无契约字段变更；修 `tests/test_clipboard_contract.py` 的 `_snapshot()` / `_restore()` 两个助手 | 前者 `ctypes.c_wchar_p(GetClipboardData(13))` 没声明 `restype`，64 位下句柄被截断成 int32 再当指针解引用 → 访问违例（Windows 后端接入后才触发，之前一直 skip 所以没暴露）；后者把 Python 缓冲区指针直接交给 `SetClipboardData`（不是 HGLOBAL，必然失败且会清空剪贴板） | 已修，**未动任何断言**；改为调用 `windows.snapshot_text()` / `windows.restore_text()` |
| 2026-10-18 | windows | 第 5 节归属表之外，Windows 端**先做了**剪贴板页与 `window.py` / `runner.py` / `theme.py` / `widgets.py` / `statusitem.py` 的接线 | 用户要求 `start-ui` 里能看到剪贴板页，而 M4（UI）当时没人做；页面不落地，M1 的后端在界面上没有出口 | 合并时**页面改用 macOS 端那版**（引擎驱动、有专属测试）；Windows 侧保留 `clipboard/windows.py`、标题栏图标、托盘菜单项、`chrome.EXTRA_H=0`，`ui/clipboard_feed.py` 退化成「引擎起不来时的本机记录源」（接口与 `SyncEngine` 一致） |
| 2026-10-18 | windows | 第 4.2 节帧头补一个 payload 长度字段 | 契约的帧头只有 `header_len`，接收端无从得知 payload 有多长，无法切帧 | 已由 macOS 端以 header 里的 `plen` 解决（见下条），Windows 端按同一口径实现 |
| 2026-10-18 | macOS | 配对盐改成固定串 `dualswitch-v1-clipboard`，不用 `device_id_A` | 契约原方案里 B 必须先拿到 A 的 device_id 才能算 key，配对要多一次握手；固定盐后两端输入同一 PIN 即得同一 key，密钥强度不变（PIN 仍是唯一秘密） | 已实现 |
| 2026-10-18 | macOS | 帧 header 增加 `plen`（可缺省） | 帧格式没有总长度字段，payload 长度只能靠 `image.size` 反推；发送端写 `plen` 让长度显式化，接收端优先 `plen` / 回落 `image.size`，老实现不受影响 | 已实现 |
| 2026-10-18 | macOS | 新增 `clipboard.peer_port`（默认 `0` = 与 `port` 相同） | 单机双引擎自测必须用两个不同端口（同一端口不能绑两次）；真实使用两端都是 47823，这个字段留 0 即可 | 已实现 |

格式：`| 2026-10-19 | windows | ClipItem 增加 files 字段 | P2 需要传目录 | 待 macOS 端评估 |`

---

## 12. 一句话记住

> **平台差异只允许存在于 `clipboard/{macos,windows}.py` 的 4 个方法里。
> 其它任何地方出现 `sys.platform` 分支，都是设计错了。**
