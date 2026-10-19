# 双端协作规则（两端 AI 开工前只读这一份）

> 2026-10-19 立。**只协调「接口」，不协调「实现」；同一处代码只有一个作者。**
> 配 `docs/ISSUES.md`（任务台账）与 `docs/PLATFORM-SPLIT.md`（界面分层）。

---

## 1. 先分类：这件事归谁写

| 类别 | 文件 | 唯一作者 | 另一端 |
|---|---|---|---|
| **共享语义** | `app.py`、`config.py`、`ddc/base.py`、`ddc/__init__.py`、`sync/*`、`ui/{theme,widgets,window,settings,runner,statusitem,clipboard_page,clipboard_feed}.py`、`clipboard/base.py`、`clipboard/mock.py` | **macOS 端** | 只读，等 pull |
| **平台实现** | `ddc/macos.py`、`hotkey/macos.py`、`clipboard/macos.py`、`ui/chrome/mac.py`、`packaging/macos/` ↔ `ddc/windows.py`、`hotkey/windows.py`、`clipboard/windows.py`、`ui/chrome/win.py`、`packaging/windows/` | 各写各的 | 互不读 |
| **契约** | `sync/protocol.py` 的常量与字节格式、`ui/chrome/base.py` 的字段表、`clipboard/base.py::ClipItem` | 变更须双端确认 | 见第 4 节 |

**判据**：如果 Windows 端为了修一个 bug 需要改共享文件 —— 说明这个 bug 该由 macOS 端修，
Windows 端等 pull。**两端各改一遍不是"并行"，是在制造 merge 冲突**：
2026-10-18 两次合并都留下过同名的两份 `attach_clipboard` / `show_clipboard`，
`tests/test_no_duplicate_defs.py` 就是为这件事存在的。

---

## 2. 任务只有一种写法：编号 + 可观察行为 + 验收命令

台账在 `docs/ISSUES.md`。**不写实现方案**（方案由作者自己定，写进 commit message）。

```
## ISSUE-007 一句话标题
- owner: mac | win
- 影响: 两端 | 仅 mac | 仅 win
- 期望: 用户能看到/做到什么（不写怎么实现）
- 验收: pytest -q tests/test_xxx.py -k yyy      ← 硬要求，缺这条不算登记
- 状态: 待做 | 进行中 | 已合并 <sha>
```

顺序是反的：**先写这条会失败的测试，再改实现**。验收命令跑绿 = 这条 issue 关闭，
不需要互相读代码、也不需要写验收报告。

---

## 3. 交接用 diff，不用设计稿

"双方照同一份文档各自实现"是最慢的一种：语义会漂，且两边都要读长文。

优先级从高到低：

1. **mac 端先给出参考实现**（一个 commit / 一个 diff），Windows 端照着 port；
2. 参考实现放不进共享层时，给**黄金样本**：一段固定输入 → 固定输出的测试；
3. 实在只能文字描述时，**≤30 行**，只写 wire format 与外部行为。

---

## 4. 契约变更（唯一需要"文档 + 双端确认"的场景）

1. 先改 `docs/*CONTRACT.md` 的对应小节 + 版本号/日期，**≤30 行**；
2. 同一个 commit 里加"黄金样本"测试（字节级 fixture 或固定输入输出），两端跑同一份；
3. commit 前缀 `[contract]`。另一端看到这个前缀：pull → 立刻跑契约测试 → 再动自己的代码；
4. 只加不改：新增字段/类型必须能被子类或旧端忽略（`protocol.py` 的 `plen` 兼容口子就是范例）。

---

## 5. 节奏

- **小步 commit + 频繁 pull**，不要攒一整天再 rebase。
- 动共享文件前，先在 `ISSUES.md` 那条 issue 的 `owner` 行写上自己并标 `进行中`；
  另一端看到就不会碰同一块。
- pull 后第一件事跑测试：macOS `QT_QPA_PLATFORM=offscreen pytest -q --basetemp=.pytest-tmp`，
  Windows `pytest -q`。红就先修，别在红的基础上继续写。
- 一端在**等另一端**时，去做自己平台目录里的活，不要为了"同步"去改共享层的注释。

---

## 6. 两端 AI 的开场白（可直接粘贴）

> 你是 **{Windows|macOS}** 端。开工前读 `docs/COORDINATION.md` 与 `docs/ISSUES.md`。
> 只改归属你的文件（共享文件只读）。做完一条 issue 就跑它的验收命令，
> 回报格式：`ISSUE-nnn 已合并 <sha>，验收命令输出：…`。
> 需要改契约或共享文件时**停下来**，在 `ISSUES.md` 里改成 `owner: 待协商` 并说明理由。
