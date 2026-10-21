# V2 界面实现说明（视觉 / 图标 / 交互落点）

> 2026-10-21 第二轮返工后定稿 · 对应 `src/dualswitch/ui/mac2/**`  
> 规格真源：`design/redesign/INTERACTIONS-v2.md` + `design/redesign/index-v2.html`  
> 本文只记「实现怎么做的、哪里和原型对应、哪些还没做」，不重复规格本身。

---

## 1. 图标：全部来自开源图标库

用户明确要求：**不再手绘 SVG，图标一律取开源库**，重点是 Windows / Mac 平台标识。

| 用途 | 来源 | 许可 | 形态 |
|---|---|---|---|
| 控件图标（导航/工具/行内动作/空态/预览头） | [Lucide](https://lucide.dev) | ISC | 24×24 线性描边，`stroke="currentColor"` |
| 平台 logo（Apple / Windows） | [Simple Icons](https://simpleicons.org) | CC0 1.0 | 24×24 实心路径 |

- 路径数据内联在 `ui/mac2/iconlib.py`（自动生成，勿手改），**运行时不联网、不依赖外部资源**。
- 渲染在 `ui/mac2/icons.py`：按「名称 + 尺寸 + 颜色 + 描边 + DPR」缓存 `QPixmap`；
  颜色在渲染时注入，所以 hover / 选中反白 / 明暗主题都不需要额外图片。
- 换图标：把 `<name>.svg` 放进临时目录，跑 `python tools/gen_iconlib.py --src <dir>` 重新生成。
  新增的名字要同时加进脚本的 `LUCIDE` 列表。

### 平台标识的两种用法

| 场景 | 做法 |
|---|---|
| hero 62px / 设备卡 32px | `plate_pixmap()`：渐变圆角牌（Win `#4C8DFF→#2C63D8` / Mac `#6B7484→#3D4450`）+ 白色 logo |
| 缩略图右下角 19px 角标 | `badge_pixmap()`：同款牌子 + 1.8px 卡片色描边（模拟「挖」进缩略图） |

> Simple Icons 的 windows 是满版四格，logo 占比压到 `0.66`（`icons._LOGO_INSET`）才有
> 「蓝牌白窗」的观感；占比 0.86 时白格会吃满整块牌，看起来像蓝图标压在白底上。

---

## 2. 尺度：字号一律按像素，不按 point

设计稿是 CSS px。Qt 的 `setPointSize(11)` 在 96dpi 下等于 14.7px —— 上一版整窗
「字大一号、行高发胖」就是这个原因。

- 全局默认字体：`runner` 里 `QFont.setPixelSize(theme.APP_FONT_PX)`（12.5）。
- QSS 里所有 `font-size` 都写 `Npx`。
- 自绘控件里的文字（键帽 / chip / 导航 / 元信息）统一用 `theme.qfont(px, weight, mono)`。
- 键帽宽度按**同一字体**的 `QFontMetrics` 算，`setWordWrap(False)` + `setFixedSize`
  才能保证「Space」不被折成两行。

## 3. 颜色：令牌字符串不能直接喂 QColor

`theme.Tokens` 里的半透明色是 CSS 写法（`rgba(78,135,255,33)`）：

- QSS 能解析；
- **`QColor("rgba(...)")` 在 PySide6 6.12 里判为无效 → 画出来是纯黑**。

所以自绘控件一律走 `theme.qcolor(token)`（把第 4 个分量当 0–255 的 alpha 解析）。
上一版右上角工具钮变成黑方块、平台牌透明，都是直接 `QColor(token)` 引起的。

## 4. QLabel 的 sizeHint 不可信的两处

1. **`wordWrap=True` 的 QLabel**：`sizeHint().height()` 不含换行后的高度，
   预览气泡会把正文最后一行裁掉。现在在 `PreviewBubble.sync_size()` 里按实际宽度
   用 `QFontMetrics.boundingRect()` 量一遍再钉高。
2. **`QLayout.sizeHint()` 有缓存**：改完子控件尺寸后 `activate()` 不保证刷新，
   所以气泡高度改成「头 + 正文 + 底栏」三块 `sizeHint()` 直接相加。

## 5. 撤下控件必须 `hide()` + `setParent(None)`

只 `deleteLater()` 的控件在事件循环处理前**仍然可见**，会在原位重复绘制
（症状：键帽文字叠影、预览正文残影）。统一走 `widgets.destroy()`。

## 6. 剪贴板页：按钮全图标

| 位置 | 控件 | 说明 |
|---|---|---|
| 工具栏 | 搜索框 + ⏸ + 🗑 | 34×34 圆角方钮，无文字 |
| 筛选 | chips | 过滤器（文案 + 计数），保留文字 —— 不是「按钮」 |
| 行内 hover | 复制 / 预览(图) / 访达 / 删除 | 25×25，`QGraphicsOpacityEffect` 淡入；动作区宽度固定 109px，hover 不抖动 |
| 预览窗底栏 | 复制 / 预览(图) / 访达 | 26×26，主按钮是复制 |
| 全部 | `IconButton` 自绘 | hover / 选中换色，不依赖 QSS 图片 |

## 7. 暂停记录（真功能）

- 按钮 → `ClipboardPage.set_paused()` → **`SyncEngine.set_paused()`**。
- 引擎暂停时仍 `poll()` 一次后端（消费 `changeCount`）但丢弃结果：否则恢复的第一
  次 poll 会把暂停期间复制的内容当新内容补发出去。
- 暂停只停「本机采集」，接收对端内容照常；按钮变实心主色 + 图标换成 ▶。

## 8. 删除语义（两种，别混）

| 入口 | 行为 |
|---|---|
| 行内 × / 右键「删除这一条」/ 聚焦行 ⌫ | `engine.drop_record(id)`，只删一条，toast「已删除 1 条」 |
| 工具栏 🗑 | 菜单：清理 1 / 7 / 30 天前 + 清空全部… |
| 清空全部 | 弹 `ClearConfirm` 二次确认（文字写明条数 + 只清本机 + 不可撤销） |

不会出现「点一次删除就把列表清空」。

## 9. 设置项都已接到底层

| 设置 | 落点 |
|---|---|
| 切换模式 标准/强制 | `app.SwitchController._execute()`：`force` 跳过远端代发，直接发 Set |
| 启用跨端剪贴板 | `runner._toggle_clip_engine()`：停 / 重启 `SyncEngine` |
| 悬浮预览 | `ClipboardPage(hover_enabled=...)`，关掉后悬停不弹 |
| 保留历史 7/30/不限 | `SyncEngine._expire_old()`（每 60s 扫一次，0 = 不限） |
| 存储上限 | 值显（读 `storage_limit_mb`），不参与淘汰 |
| 热键两行 | 键帽组件 + 录制（Esc 取消） |

## 10. 已知缺口（规格里有、当前做不到）

1. **元信息行的「应用名」**：`ClipRecord` 没有应用字段（契约里没有），现在只显示
   `来源 · 时间 · 大小 · 类型`。要补得改 `docs/CLIPBOARD-SYNC-CONTRACT.md`（双端）。
2. **「文件」类型永不出现**：契约 P2 不同步文件，`kind` 只有 text/image/mixed，
   所以「文件」chip 恒为 0；文件卡样式的预览窗目前走不到。
3. **剪贴板热键**：已注册全局 ⌘⇧V（第二个 CGEventTap），但「按一次呼出到剪贴板页」
   只在应用运行时有效，未做「呼出即聚焦搜索框」。
4. **菜单栏菜单的键帽**：走 NSMenu 原生 `keyEquivalent` 呈现（系统画），
   不是自绘键帽 —— 视觉上等宽符号，跟窗口内的键帽不完全一致。
5. **hero 的「上次切换耗时」**：只有真正切换过才有数据，首帧是「尚未切换 · 按一下开始」。

---

## 11. 第二轮返工（2026-10-21 晚·二次验收）

| 问题 | 处理 |
|---|---|
| 右上角两个图标按钮不要了 | 删掉 40px 工具条与两个 `IconButton`，侧栏直接从标题栏下沿开始（原型同步删除） |
| 未检测到显示器配色丑 | 禁用主按钮改 `card3`+`line` 描边+`ink3`；hero 牌 `card3`+描边；键帽强制隐藏（`KeyCaps.set_keys` 会 `setVisible(True)`，要在 unknown/busy 后压回去）；设备卡 45% |
| 来源角标太小 | `BADGE` 19→22，描边 2px；角标 logo 单独按 0.56 占比画（原来复用大牌 0.86，白苹果铺满整块看着像白方块） |
| 类型缩略图没照 Frset | 实色瓷砖 + 白字形：text `#657DFF` / code `#7A5AF8` / link `#6B7BD3` / image `#4E87FF`；file/unknown 浅灰 |
| 图片分截图与图片文件 | 截图 = pasteboard 里的 TIFF/PNG；图片文件 = `public.file-url`，用 ImageIO 抽缩略图（≤1600px），`path` 记原路径、标题用文件名、预览直接开原文件 |
| 列表要「来源 · 来源应用」 | 来源改平台名（Mac/Windows）；`ClipItem.app/app_id` 由 backend 在 poll 时抓前台应用；有 bundle id 时元信息行画 13px 应用图标（`apps.py`），截图只有文字 |
| 预览窗箭头没了 / 按钮贴边 / 图文重叠 | 箭头改子控件（父级画会被卡片盖住）；头/正文/底栏三段都 `setFixedHeight`，正文按宽度量高，超上限只压正文；图片只缩不放并按剩余高度换算 |

新增/改动的文件：

- `ui/mac2/icons.py`：`_plate_bg` / `_draw_logo` / `badge_pixmap` 重写；`type_tile_pixmap` 支持无描边
- `ui/mac2/apps.py`（新）：bundle id → 应用图标（DPR 超采样、缓存）
- `ui/mac2/preview.py`：`PopoverArrow` 子控件、`sync_size` 重写、头部按类型着色
- `clipboard/macos.py`：`_frontmost_app()`、`_image_from_file_url()`、`_preview_of_image_file()`（ImageIO）
- `clipboard/base.py` / `sync/protocol.py` / `sync/engine.py`：可选字段 `app` / `app_id` / `path`
- `docs/CLIPBOARD-SYNC-CONTRACT.md` 第 11 节：登记了这两个可选字段（Windows 端可跟进）

### 遗留（还没做）

1. **Windows 端不填 `app`**：跨端推来的条目只有 `Windows · 时间 · …`，没有来源应用；
   契约已登记，等对端跟进。
2. **多选文件**仍按文本处理（不猜是不是图片）。
3. 截图识别靠前台应用 bundle id（`com.apple.screencaptureui`）；用第三方截图工具
   （如 CleanShot）时显示的是那个工具的名字，不是「截图」。

---

## 12. 第三轮返工（2026-10-21 14:00）

| 问题 | 根因 / 处理 |
|---|---|
| 窗口不能拖动 | `NSWindowStyleMaskFullSizeContentView` 让内容铺到标题栏下，侧栏吃掉拖拽区 → 整套透明标题栏逻辑删除 |
| 菜单太大 / 行内图标 | 15px NSImage 被 `setSize_(pixmap.width())` 设成了 30pt（DPR 2 的物理宽）→ 行高翻倍。按要求干脆删掉行内图标，只留状态灯 |
| 未连接页看不清 | 主按钮文案内联写死白色，禁用时仍白字压浅灰 → `_set_btn_text_color()` 按状态改；副标题/未连接卡名 ink3→ink2；dead 牌 0.45→0.6 |
| 角标只有 1/4 有内容 | `badge_pixmap` 先画后设 DPR，嵌套 pixmap 的逻辑尺寸被当物理像素 → 改成 `_canvas()` 先设 DPR 再画，全部逻辑坐标 |
| 复制访达图片是空白 PNG | Finder 同时给 file-url 和文件图标位图，代码先读位图 → 改成 file-url 优先、图片走文件预览，普通文件不用图标位图 |
| 来源应用没图标 | `apps._load_icon` 里 `pix` 变量名笔误（NameError 被吞）→ 修好；图标 14px、按 DPR 超采样 |
| 预览窗不移出关闭 | 只有缩略图 leave 计时 → 气泡 `leaveEvent` 也走 `schedule_close()` |
| 设置：外观 / 保留历史 | 两行删除；`appearance` 固定 light；引擎仍按 30 天兜底清理 |
| 存储上限写死 | 改可编辑（10–5000MB）+「清理」按钮；引擎新增 `_enforce_storage_limit()` 超限丢最旧 |
| 数值框文字看不见 | QSS 改边框后 QSpinBox 内部 lineEdit 宽度算成 1px → 改用 QLineEdit + QIntValidator |

---

## 13. 第四轮：文件（访达复制）与单击复制（2026-10-21 14:40）

用户贴了 Frset 的两张截图（图片文件 / 普通文件）作为「正确做法」，逐条落地。

| 点 | 处理 |
|---|---|
| 图片文件缩略图是空白 PNG | 后端 `_read()` 里 **file-url 优先**：单文件视为「文件记录」，图片文件读文件抽缩略图，忽略 Finder 塞进来的文件图标位图 |
| 文件没有文件名/目录 | 新增 `ClipItem.path` / `file_size`，`make_file()` 把 basename 写进 `text`；`ClipRecord` 同步两字段 |
| 元信息要目录 | 文件行的 `meta_line` = `来源 · 应用 · 时间 · 目录`（`short_dir` 把家目录缩成 `~`），不再追加「文件」标签 |
| 文件卡 | 气泡正文改成居中三段（文档图标+扩展名 / 文件名 / 目录），头部标题写「文件」；底栏左侧 `大小 · 时刻` |
| 按钮组合 | 截图=预览；图片文件=预览+打开文件；普通文件=打开文件。行内 / 右键 / 底栏三处一致 |
| 单击行 | 改成**直接复制**；双击改预览；键盘 ↩ 仍是复制、⌫ 删除 |

细节：

- `icons.file_pixmap(path, size)`：Lucide 文档字形 + 下面一行扩展名（PNG/DMG/TXT…），
  字形按扩展名分派（`file-image` / `file-archive` / `file`）。
- `openers.classify_record(kind, text, path)`：**path 非空优先判为「文件」**，所以图片文件
  也是文件卡；`ClipItem.compute_digest()` 把 `path` 也算进去（不同目录同名文件不能撞成一条）。
- `SyncEngine._send()`：`item.path and item.image is None` → 只在本机记一条「仅本机」，
  **不发对端**（文件本身传不过去，契约 P2）；带 `png` 的图片文件照常按图片同步。
- `write()`：文件记录写回 `public.file-url`（访达里能直接粘出文件），不再把文件名当纯文本写。
- 文件行显示的大小用 `file_size`（真实文件大小），容量统计仍用 `size`（入库字节数）。

测试：`test_ui_mac2` 62 例，新增文件分类/标题/目录/按钮组合/文件卡/扩展名字形/引擎不发送/
单击复制（`QTest.mouseClick` 真事件）等 10 例。

---

## 14. 第五轮修补：长文本叠字 / 文件引用 URL（2026-10-21 15:00）

| 问题 | 根因 | 处理 |
|---|---|---|
| 文字预览叠字 | 正文只 `setFixedHeight` 没裁文本，QLabel 把多余行画到矩形外，压到底栏和注脚上 | **改成完整显示**：正文放进 `QScrollArea`，高度按实际换行量算；超屏才滚动（见下方补充） |
| 文件记录 `id=6571367.29…` / `/.file`、打不开 | 访达给的是**文件引用 URL**（`file:///.file/id=…`），`urlparse` 解析出来不是真实路径 | `_file_url_path()` 改走 NSURL：`isFileReferenceURL()` → `filePathURL()`；解析后文件不存在则按文本处理 |
| 重复复制 | 引擎本来就按 digest 去重（不堆行），但重复项留在原位 | 重复项**提到最前**并刷新时间（列表按最近使用排序） |

验证：`fit_wrapped_text` 用二分（几十次 `boundingRect`，万字文本也在毫秒级）；
测试补了长文本截断不叠字、短文本不裁、重复复制只有一条且置顶、引用 URL 解析四例。

### 补充：文本预览不截断（同日 15:20 用户纠正）

第一版为了防叠字做了「按可用高度截断 + 省略号」，用户明确否掉：**预览就是看全文，不许截断**。
现在的规则：

- 内容多长，气泡就多高（不再受 `PV_TXT_MAX_H=270` 约束）；
- 高度上限 = `min(分类上限, 屏幕可用高 − 120)`；到顶后**正文内部滚动**，不裁字；
- 正文用 `QScrollArea`（无边框、透明底）承载，QLabel 高度按实际换行量给出，
  所以「不需要滚动」时滚动区高度 == 文字高度，不会留白；
- 真·极端文本（> `theme.PV_TEXT_MAX_CHARS` = 2 万字）才限制渲染量，并在正文尾部写明
  「只渲染了前 N 字 / 共 M 字；复制仍是全文」—— 目的是别让 QLabel 排版把机器拖卡，
  不是视觉裁切。
- 滚动条 handle 改成 `ink3`：原来的 `line` 色在白底上等于看不见。
