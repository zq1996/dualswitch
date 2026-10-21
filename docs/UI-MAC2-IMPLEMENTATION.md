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
