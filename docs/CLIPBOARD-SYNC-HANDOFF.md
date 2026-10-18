# 跨端剪贴板 · 联调交接说明（给 Windows 端 AI）

> 2026-10-18 · macOS 端已把 P0（文字）+ P1（图片）全部实现并自测通过。
> 这份文档只讲一件事：**你（Windows 端）接下来怎么自测、怎么和我联调、哪些地方已经踩过坑。**

---

## 0. 一句话现状

macOS 端的 `clipboard/macos.py` + `sync/*`（网络/协议/引擎）+ UI + CLI + 自测工具**已经全部落地并 push**。
你现在 `git pull --rebase` 拿到的是完整可用的网络层和自测工具，只差 **`clipboard/windows.py` 这一个后端文件**。

也就是说：**协议和引擎你不用写、不用测、不用改**，你只写「Windows 剪贴板怎么读、怎么写」。
这就是契约第 5 节文件所有权划的边界，别越界（`base.py` / `sync/` / `ui/` / `config.py` / `app.py` 都是我的，只读）。

---

## 1. 你要做的（按顺序，前三步都不用第二台机器）

### 第 1 步：跑契约测试（确认基线）

```powershell
git pull --rebase
python -m pytest tests/test_clipboard_contract.py -q
# 期望：11 passed, 2 skipped（这 2 个 skipped 就是"真实后端"那两个往返用例）
```

### 第 2 步：写 `clipboard/windows.py`（你唯一的源码）

只写这两个文件：
- `src/dualswitch/clipboard/windows.py` —— 实现 `poll / write / probe / close`
- `tests/test_clipboard_windows.py` —— 你的测试

参考实现有两个，照抄结构即可：
- `clipboard/mock.py`：`poll()` 的去重 + 「自写吞掉」语义的**标准答案**
- `clipboard/macos.py`：真实后端的写法（只不过它用 NSPasteboard，你用 Win32）

**Windows 侧三个必须自己摸清的未知数**（契约第 6 节 M1 就列了，这是你真正的活）：
1. 从资源管理器复制文件时，Qt 是否稳定给 `urls`（P2 才用，先记结论）
2. 图片在剪贴板里是 `CF_DIB` 还是 `CF_PNG`；`CF_DIB`→PNG 转换有没有 alpha/行序问题
3. `GetClipboardSequenceNumber` 在多少 ms 内稳定（决定 `poll()` 后要不要 `sleep(10)` 再读 —— 经典竞态：序列号变了但数据还没落地）

### 第 3 步：验证你的后端

```powershell
$env:DUALSWITCH_CLIP_TEST="real"
python -m pytest tests/test_clipboard_contract.py -q -s      # 期望 13 passed（mock 的 11 + 真实后端 2）
python tools/clip_selftest.py --role windows                 # 期望全 PASS，「敏感标记」允许 MANUAL
python -m dualswitch clip probe                              # 把输出贴给我（两端格式对照用）
```

### 第 4 步：单机验证网络层（你不需要第二台机器）

网络层是我写的、平台无关，所以你在 Windows 上也能直接跑：

```powershell
python tools/clip_sync_selftest.py
# 期望 7/7：文本 / 图片 / 自写回弹 / 环抑制 / 重复投递 / 20MB 大图 / 断线重连
```

这一步过了，就证明**协议在 Windows 上也能跑通**，剩下的问题只剩「两台真实机器 + 真实剪贴板后端」。
**这一步过了再来找我联调，否则联调时你会分不清是网络问题还是后端问题。**

---

## 2. 联调怎么进行（需要两台机器 + 用户在场）

### 2.1 先配对（一次性）

两端各自执行（同一个 6 位码）：

```bash
# macOS（终端）
dualswitch clip pair --pin 246810

# Windows（PowerShell）
python -m dualswitch clip pair --pin 246810
```

### 2.2 看连接状态

```bash
dualswitch clip status          # 期望「认证通过: yes」「引擎状态: connected」
```

### 2.3 用 CLI 代替"切来切去"做初验

真机联调时**先别用热键切屏**，先用 CLI 单向发一条，把「没读到/没发出/没收到/没写入」四环定位清楚：

```bash
# 在 Windows 上复制一段文字，然后在 Mac 上：
dualswitch clip get             # Mac 能看到自己剪贴板里的内容
# 在 Mac 上手工推一条给 Windows：
dualswitch clip send "从 Mac 推过去"
# Windows 端看有没有收到（clip get 看当前剪贴板，或直接 Ctrl+V 到记事本）
```

四环里卡在哪一环，就查哪一环，不要一上来就怀疑协议（协议已经被 7/7 的自测覆盖了）。

### 2.4 最后做 10 项人工验证

`docs/CLIPBOARD-SYNC-LOG.md` 第 4 节那张表，两台机器一起逐条打勾。这才是唯一需要用户动手的环节。

---

## 3. 我已经踩过、你大概率也会踩的坑（务必先看）

| # | 坑 | 结论 / 正解 |
|---|---|---|
| 1 | **对称 TCP 不能"谁先 ready 谁赢"** | 两端同时拨号时各自看到的两条链路顺序不同，会互相踢掉对方连接，表现为永远连不上、日志里「关闭重复连接」来回刷。**已经修成一致选举：拨号方 device_id 小者胜出**。你不用动这块，但如果看到「关闭重复连接」日志，别慌，那是正常的选举过程。 |
| 2 | **帧里没有总长度字段** | payload 长度靠 header 推：优先 `plen`，缺省回落 `image.size`。你只写 `image.size` 也能互通（我加了兼容）。 |
| 3 | **自写回弹只吞一次，不能记 digest** | 契约测试 `test_text_roundtrip_unicode` 要求「写完能读回来」，所以 `write()` 里**不要**把内容指纹记下来（记了就永远读不回）。防环靠引擎第 2 层，跟你的后端无关。 |
| 4 | **连接断了要把 `authenticated` 翻 False** | 否则界面显示「已配对」但一条也发不出去。这个在 transport 里，你不用管。 |
| 5 | **剪贴板相关测试不能用 `QT_QPA_PLATFORM=offscreen`** | 该平台下 `QClipboard` 是空实现。契约测试用 `DUALSWITCH_CLIP_TEST=real` 时，会自动连真实后端，别加 offscreen。 |
| 6 | **配对盐是固定串 `dualswitch-v1-clipboard`** | 不是契约原文的 `device_id_A`（那样要多一次握手）。两端 `clip pair --pin <同一个码>` 即得同一 key。 |

---

## 4. 需要用户做什么

用户（两台机器的主人）只做两件事：

1. **两端各跑一次 `clip pair --pin <同一个 6 位码>`**（配对是终身的，一次就好）。
2. **在场做 10 项人工验证**（复制中文/截图/快速切换/睡眠唤醒/断 WiFi 等）。

其余——写后端、跑自测、定位问题——都不需要用户。

---

## 5. 我这边随时能配合的事

- 你如果发现契约有歧义，**别改 `base.py`**，把需求写进 `docs/CLIPBOARD-SYNC-CONTRACT.md` 第 11 节，我改完你再 pull。
- 你 `clip probe` 的输出贴给我，我帮你对照两端格式差异（这是联调最常用的排障手段）。
- 如果真机联调时发现协议层 bug（虽然自测已覆盖 70%，但真机可能有新情况），我来修 `sync/*`，你只负责 `windows.py`。
