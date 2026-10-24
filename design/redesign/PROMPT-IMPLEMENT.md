# 新窗口实现提示词（v2 页面 · 新建一套）

> 用法：开新会话整段贴给它。目标是**新建一套 UI**，不动现有代码，等验收后再删旧的。

---

请为 dualswitch（macOS 端）按既定设计稿**新建一套 UI 实现**（v2 宽窗口版）。

## 硬性要求（先读，再动手）

1. **新建一套，不改现有代码**。新代码放 `src/dualswitch/ui/mac2/`（新目录，含 `__init__.py`），  
   入口做成 `python -m dualswitch ui2`（在 `cli.py` 里加一个新子命令分发到新 runner，不碰 `ui` 旧命令）。  
   旧的 `src/dualswitch/ui/mac/**` 一行不动 —— 后续验收通过后我会让你整体删除旧树。
2. **只做 macOS 端**。`ui/win/**`、`clipboard/windows.py`、`ddc/windows.py`、`hotkey/windows.py`、  
   `packaging/windows/**`、`tests/test_clipboard_windows.py` 一律只读，不许碰。
3. **整个应用只有一个窗口**：剪贴板/设置都是窗口内换页（侧栏导航），绝不弹独立面板/独立窗口。
4. 跑测试固定命令：`QT_QPA_PLATFORM=offscreen python -m pytest -q --basetemp=.pytest-tmp -p no:cacheprovider`  
   （本机 /tmp 不可写，必须带 `--basetemp`）。注意：剪贴板相关用例不能用 offscreen 平台的 QClipboard，  
   涉及剪贴板读写的用 `clipboard/mock.py`。
5. Python 用 `/Users/zhqi/.workbuddy/binaries/python/envs/default/bin/python`。

## 设计依据（先读完再写代码）

1. **交互规格（唯一实现依据）**：`design/redesign/INTERACTIONS-v2.md`  
   —— 全部交互逐条写明，标 🆕新增/🔄改/✅照搬，含菜单栏菜单 8 行结构、剪贴板行解剖、  
   预览窗触发时序与尺寸上限、同步四态、设置分组、新增功能排期表（§5）、视觉令牌（§7）。
2. **视觉基准**：`design/redesign/index-v2.html` —— 可交互 HTML 原型，样式以它为准。  
   需要看某个控件长什么样就打开它对照（浏览器直接开）。
3. 设计背景（了解为什么这么设计）：`design/redesign/NOTES-v2.md`、`design/frset-ref/DESIGN-REFERENCE.md`。
4. 契约与既有约束：`docs/CLIPBOARD-SYNC-CONTRACT.md`（同步只做文字+图片，**文件是 P2 不做**）、  
   `docs/REMOTE-SWITCH.md`（远端代发）、`docs/archive/UI-DESIGN-CLIPBOARD.md`（同步四态语义、行规格红线）。

## 后端接口（只调用，不修改这些文件）

- `src/dualswitch/app.py`：`SwitchController` —— 切屏控制；`detect_source()`/`source_state`  
  返回 `windows`/`mac`/`SOURCE_UNKNOWN`（三态，unknown 时 UI 必须置灰，见 INTERACTIONS §2.1）
- `src/dualswitch/sync/engine.py`：`SyncEngine`、`ClipRecord(kind:"text"|"image"|"mixed", ...)`
- `src/dualswitch/config.py`：`SOURCE_UNKNOWN`、`cfg.other_host()`
- 共享层归 mac 端所有（`clipboard/base.py`、`sync/*`、`config.py`、`app.py` 可读可改），  
  但本轮**尽量只新增不改共享层**；确需改共享层时提醒我「Windows 端 pull 后要跟着验证」。

## 实现要点（规格里都有，这里强调最容易做错的）

- **快捷键用键帽组件**（每键独立圆角块，gap 3px），4 处：主按钮/菜单/设置页 2 行 —— 不要文本拼接。
- **剪贴板预览窗**：只悬停**缩略图** 450ms 触发（不是整行）；气泡在缩略图**左侧**带指向箭头；  
  尺寸**随内容自适应**（图≤412×330、文字≤392×270、代码≤400×290）；弹簧动画；关闭延时 220ms。
- **打开方式按类型分派**：图片→Preview.app/QLPreviewPanel；文件→访达；链接→NSWorkspace.open。
- 缩略图右下角**来源系统角标**（Mac 复制的显 Mac 角标、Windows 显 Windows 角标），不是来源应用。
- 单击行=预览、双击行=复制、右键=更多操作、失败/仅本机药丸=重试按钮。
- 双态空态分开：「剪贴板为空」vs「当前筛选下暂无内容」。
- 「仅本机」和「失败」必须分开（前者不是错误）。

## 图标

`design/redesign/icons/frset-extracted/` 里的 PNG 是从参考应用提取的，**只作视觉比对**，  
**禁止打进产品包**。产品图标照着它重绘（描边/圆角/光学重量对标），放 `src/dualswitch/ui/assets/`：

- 平台角标（apple-logo.png / windows-logo.png + dim 变体）：参照 `design/redesign/icons/make_icons.py`  
  的自绘几何（256×256 RGBA），产出同名文件即可被 `logo_pixmap(kind)` 加载。
- 剪贴板类型图标/工具图标：对照提取图重绘，20/24/44 三档尺寸（INTERACTIONS §7）。

## 验收（做完自测，然后停下来汇报，不要自行删除旧代码）

1. `python -m dualswitch ui2` 能起宽窗口（780×580、侧栏三页），明暗都正常。
2. 全量测试全绿（命令见上），**为新交互补用例**：键帽渲染、预览窗 450ms 延时与缩略图触发、  
   双态空态、来源角标、打开方式分派、菜单栏菜单结构（行数/文案）。
3. 交互逐条对照 `INTERACTIONS-v2.md` 自查一遍，把「做到了/没做到」列成清单汇报。
4. **汇报完等我确认**，我验收后才让你删除 `ui/mac/**` 旧树。

遇到规格没写清的细节：**先问我，不要自行发挥**。规格与原型冲突时以 `INTERACTIONS-v2.md` 为准。
