# Bilive 五页前端改版验收

## 2026-10-02 录播库与字幕审核流程完善

范围：在现有界面上完善录播选择后的内容、字幕、成片生成与人工批准流程。仅修改 `frontend/workbench.js`、`frontend/ui.js`、`frontend/styles.css` 和本验收记录，保留已有未提交修改。

当前页面证据与检查步骤：

1. **浏览录播与候选：通过。** 真实页面原先直接显示 `needs_review`，空字幕只显示空表头。候选及片段状态现使用中文，候选增加完整标题提示与选中语义，长标题可换行。修改前证据：[内容页](.runtime/uiux-20261002/02-content-before.jpg)。
2. **补充与审核字幕：通过。** 空态明确当前流程需要至少一行字幕；没有字幕行时禁止保存，当前版本字幕未确认保存时禁止生成成片。草稿、已保存字幕、待补充字幕分别提示。新增行自动聚焦文本，删除按钮点击内部图标也能删除并更新编号。点击行号定位：源视频加片段起点，成片使用相对时间；非法或超区间时间拒绝定位。播放器入出点及重新判断建议同步更新区间摘要。真实空字幕最终证据：[录播库](.runtime/uiux-20261002/06-library-after.jpg)。
3. **生成成片：通过。** 保存后旧成片失效，页面突出“生成成片”；提交后台任务立即锁定编辑，显示等待/处理中文状态。核查非零源起点定位 `4 + 1.05 = 5.05` 秒、成片定位 `1.4` 秒；非法 `99:99` 不改变播放位置。隔离场次验证草稿刷新恢复、非法时间保存失败保留草稿、保存字幕、真实 Worker/FFmpeg 生成、成片预览。
4. **人工批准：通过。** 隔离成片批准后字幕与生成/批准按钮锁定，行号定位仍可用；提示当前成片受保护。隔离环境无上传消费者，不执行实际上传或投稿。证据：[批准后锁定](.runtime/uiux-20261002/05-approved-locked.jpg)。

键盘检查：源/成片及内容/字幕标签支持左右方向键、Home、End；只有当前标签进入 Tab 顺序，面板绑定标签。通过“查看候选统计”切换内容时同步更新 Tab 顺序。该结论覆盖本轮操作，未进行完整屏幕阅读器验收。

最终真实页面在应用内浏览器 1920×1080 检查：页面与主区无横向溢出，字幕操作和底部成片操作可见，最下方按钮底部约 1033px。真实页面只读查看，未修改真实字幕、审核状态或录制配置。观测的真实/隔离浏览器日志无前端错误。页面截图均重新检查，加载中或状态未稳定的截图不作为验收证据。

验证：`tests/test_manual_workbench.py` **24 passed**（56.22 秒）；两个修改的 JavaScript 文件 `node --check` 通过；`git diff --check -- frontend` 通过。浏览器检查明细：[results.json](.runtime/uiux-20261002/results.json)。隔离服务为临时 `127.0.0.1:18251`，使用 `.runtime/uiux-isolated-20261002` 内短媒体、字幕、任务和队列；完成后关闭。

真实使用入口仍为 `http://127.0.0.1:2233/workbench/#/library`，刷新页面即可读取前端修改。没有重启生产服务、执行真实发布或回收，也没有提交或推送。本轮不重复宣称此前完整项目验收结果。

## 2026-10-02 可用性补验（当前结论）

直播间列表 UI/UX 修复（用户截图中的文字竖排）：

1. 浏览列表：使用当前 4 个真实房间复现。等效 150% 显示时，旧自动列宽将文字区域挤到约 19px，行高达到 300–660px；截图见 [.runtime/frontend-real-evidence/live-layout-before-150-1920.png](.runtime/frontend-real-evidence/live-layout-before-150-1920.png)。这使名称、标题、房间号难以扫描，操作列也可能被挤出可视区域。
2. 修复布局：直播间表格改为固定列布局，明确状态、录制、时长与操作的宽度分配；缩小列表封面，名称/标题/房间号保持横排，超长文字使用省略号与完整悬停提示。窄可视区适当缩小详情栏。录制时长只在展示层取整到秒，保持接口原始精度。表头滚动时固定，选中行增加蓝色指示线。常规截图见 [.runtime/frontend-real-evidence/live-layout-after-100-1920.png](.runtime/frontend-real-evidence/live-layout-after-100-1920.png)，放大截图见 [.runtime/frontend-real-evidence/live-layout-after-125-1920.png](.runtime/frontend-real-evidence/live-layout-after-125-1920.png) 与 [.runtime/frontend-real-evidence/live-layout-after-150-1920.png](.runtime/frontend-real-evidence/live-layout-after-150-1920.png)。截图均已实际打开检查。
3. 选择房间与刷新：真实选中雪狐桑后，列表选中态与右侧名称/标题一致，完整标题在详情可读；截图见 [.runtime/frontend-real-evidence/live-layout-selected-150-1920.png](.runtime/frontend-real-evidence/live-layout-selected-150-1920.png)。常规及 125%、150% 等效显示均验证名称保持一行、行高约 117–121px、“查看”单行、无列表或页面横向溢出、固定表头正常；轮询与选中保留滚动位置，无脚本错误。结果见 [.runtime/frontend-real-evidence/live-layout-results.json](.runtime/frontend-real-evidence/live-layout-results.json)。所有截图最终尺寸为 1920×1080；通过不同 CSS 可视区与像素比例覆盖用户放大显示场景。

可访问性检查限于本轮文字布局、缩放可读性和原生按钮可达性，不代表完整键盘/屏幕阅读器合规验收。本轮仅改前端显示，不改真实房间、录制开关、端口、代理或后台规则。

直播间滚动修复：此前选中房间会重建列表 DOM，但点击路径未保存滚动位置，实测列表从 417px 跳到 0。现在每次重绘前保存当前主区、列表与详情位置；后台读取结果未改变时不恢复旧快照，避免用户在读取期间滚动后被拉回。真实房间接口在 1920×1080 浏览器中验证点击行、切换不同房间、“查看”、重复选中、定时刷新及读取期间滚动均保持位置，且选中态和详情回链正确，无脚本错误。因当前真实房间较少，验收仅在浏览器限制滚动区高度以触发滚动，不创建假房间或改写接口数据；结果见 [.runtime/frontend-real-evidence/live-scroll-results.json](.runtime/frontend-real-evidence/live-scroll-results.json)。

用户后续反馈已处理：“切换场次 / 管理”改为蓝底白字主按钮，真实 1920×1080 浏览器确认可点击、弹窗与场次下拉框正常，无脚本错误；截图见 [.runtime/frontend-real-evidence/library-switch-blue-1920.png](.runtime/frontend-real-evidence/library-switch-blue-1920.png)。按用户明确要求实际安装两个当前项目登录启动任务 `\BiliveV2\BiliveV2Recorder`、`\BiliveV2\BiliveV2Dashboard`，读回确认目录、当前用户、登录触发、隐藏窗口与无限运行时长；原项目任务和正在运行的服务保留。注册逻辑模拟回归 `TASK_PLAN_PASS`。尚未实际重启电脑，不能将注册验收等同于重启后完整链路验收。

用户要求“与原项目一样现在可以使用，并且不走代理”后，本轮范围补充为本机运行与连接修复。**原主入口已恢复为 [http://127.0.0.1:2233/workbench/#/live](http://127.0.0.1:2233/workbench/#/live)**，2234 为内部工作台，2235 为按需 Worker。临时 18250 服务保持关闭。下文的早期端口状态是历史记录。

实测确认：

封面修复的接口、地址限制、直连下载与录制器回归共 **27 passed**；JavaScript 语法检查通过。

- 封面补验：原接口适配曾遗漏录制器返回的 `room_info.cover`，现已保留该字段并接入列表与详情。浏览器通过同源 `/studio-api/rooms/{room_id}/cover` 读取，由工作台关闭环境代理直连 B 站 CDN，使用 840×472 WebP 缩略图并缓存；不转发录制器鉴权或 Cookie。三个真实房间封面约 16–34 KB，均已加载。1920×1080 实测详情切换、状态轮询和四处图片加载成功，浏览器没有向外部 CDN 发起图片请求，无页面脚本错误或横向溢出。截图见 [.runtime/frontend-real-evidence/live-covers-1920.png](.runtime/frontend-real-evidence/live-covers-1920.png)，结果见 [.runtime/frontend-real-evidence/cover-acceptance-results.json](.runtime/frontend-real-evidence/cover-acceptance-results.json)。封面不存在或读取失败仍显示明确缺图状态。
- 3 个真实配置房间加载成功：22384516、22966160、8792912。标题、直播状态来自 B 站；原有“监测开启、录制关闭”配置已恢复。没有迁移原项目的历史数据。
- 修复统一启动脚本不能补齐部分服务的问题。已有工作台时可以补录制器；已有录制器但工作台缺失时，使用本项目密钥独立校验原生 API，不再依赖工作台代理页面判断录制器身份。重复启动正常复用现有服务。
- 后台服务通过正常 Windows 后台方式启动，避免临时沙箱执行结束后清理其子进程。跨多次浏览器、接口与命令检查，2233/2234 持续正常。Worker 空闲退出属于现有规则；实际验证 `/api/worker/wake` 能从离线唤醒为 running。
- 录制器、工作台、Worker、上传消费者在加载本地环境后清除本进程代理变量并设置 `NO_PROXY=*`。上传 Session 同时关闭 `trust_env`。不会修改系统代理；实测代理软件 TUN 未开启，录制器连接 B 站服务器的 443，未连接 127.0.0.1:7890。应用内的本地接口转发不消耗代理流量。
- 真实控制检查发现旧“启动监测”文案对应上游 `/start`，会同时启用录制。现在“启动/停止监测”使用上游监测方法的窄接口；新增接口继承原生 API-key 鉴权，未带密钥请求实际返回 401。保留原 `/start`、`/stop` 的业务行为。
- 直播页每 5 秒读取真实状态，保持选择和独立滚动；对话框输入和正在执行的控制不被打断。控制反馈先显示“正在提交”，防止将上次成功提示误认为当前操作已完成；控制区展开状态保持。
- 真实 `large-v3`、CUDA、float16 推理探测通过。修复预检静音可能长时间生成输出的问题：仅预检限制为一个输出 token，正式转写参数不变。实际探测约 9.5 秒完成。

补充验证：相关 Python 回归 **35 passed**；两套虚拟环境 `pip check` 均通过；Python 编译、JavaScript 语法、PowerShell 语法和所改文件的 diff 空白检查均通过。真实 2233 主入口浏览器 **8 组通过**，五页 1920×1080 截图无横向溢出、页面脚本错误或意外请求失败。结果见 [.runtime/frontend-real-evidence/live-acceptance-results.json](.runtime/frontend-real-evidence/live-acceptance-results.json)，当前五页截图位于 `.runtime/frontend-real-evidence/`。未选择片段时显示“未选择片段”，不再误报字幕未保存；该空态及实际视频播放另外检查通过。

实测覆盖：真实房间、选择及轮询、添加输入保留、监测开关不启用录制、暂停接口、房间回链、配置只读、五页真实状态及真实 MP4 播放。

测试期间旧 `/start` 的语义导致短暂真实录制。已停止并恢复原开关，留下的一段实际直播经现有 Worker 转封装为 MP4，原入口可播放；其弹幕和录播记录保留，没有投稿或生命周期回收。该事实与早期“只使用隔离媒体”的验收范围有明确区别。原项目历史媒体未迁移，真实投稿与付费 MiMo 分析没有执行。

使用方式：打开上述主入口，或双击 `启动Bilive.cmd`。选择直播间后在右侧“录制控制”中操作；“启动监测”只监测，“启用录制”才开启录制。Worker 空闲退出后，人工处理动作会沿用现有机制自动唤醒。

## 初始前端隔离验收（历史）

最终结果：**passed**。2026-10-02，在约定的 **1920×1080、DPR 1** 桌面尺寸完成页面及交互验收。当前范围内没有未解决的 P0、P1 或 P2 问题。生产服务、真实投稿和真实源文件回收没有执行，相关线上状态不属于此结论。

**交付入口纠正（2026-10-02）**：最初把隔离验收预览作为主要入口交付，未充分区分测试记录与真实记录。用户指出后，已关闭临时 18250 服务。当前真实工作台为 [http://127.0.0.1:2234/#/library](http://127.0.0.1:2234/#/library)，通过原有 `start_windows_dashboard.ps1` 启动，端口及数据配置未改。读取 `D:\bilive\Videos`、`D:\bilive\src\db\data.db`；实际接口录播、staged 队列和生命周期记录均为 0 条，视频目录只有 JSON/JSONL 文件。2233 录制器未运行，房间接口 503。没有启动录制器、Worker 或投稿消费者。正常完整入口仍是 `http://127.0.0.1:2233/workbench/`，需要原有录制器运行后提供代理。

纠正后另外在真实 2234 接口下检查五页。截图在 `.runtime/frontend-real-evidence/`，它们展示当前真实空态与录制器不可用状态，与下文隔离环境的正常数据截图有明确区别。原测试数据、控制替身均只在 `.runtime` 验收脚本内，没有注入产品前端或真实数据目录。

## 实现与范围

- 普通 JavaScript 工作台保持原有 hash 路由、同源 API 和业务校验。公共布局为 240px 侧栏、32px 主区水平边距、16px 正文、36px 页面标题；移除了影响布局的顶部文字条。图标使用本地 Tabler SVG，附上游许可证。
- 录播库采用场次面包屑、播放器与内容/字幕编辑区、横向候选卡片及成片操作栏。时间码输入转换为秒，保留字幕与样式草稿、后台版本校验、旧成片失效和人工批准锁定。连续弹幕密度显示缺失说明，已有证据另有入口。
- 直播间采用房间表格和 420px 详情栏。添加、监测、录制、分割和移除沿用原接口；主播录播和档案链接传递实际房间参数。
- 发布队列采用五个状态标签、视频表格、本地每页 20 条分页及 360px 详情栏。各分组读取现有单状态分页接口并合并；读取未完成时不显示完整总数。真实可播放成片取帧，缺少媒体时明确显示缺图。进度显示真实阶段，无伪造百分比。
- 录播回收采用第 5 天提醒/到期/已有回收记录三个标签、场次表格与 600px 详情栏。排队、执行、失败、完成及后台拒绝原因分别呈现；冻结清单和逐项结果可展开。
- 设置采用四个标签及服务、任务、配置、路径摘要。启动/停止按实际状态显示，配置只读；凭据存在不表示登录已验证，索引只在成功查询后显示可用。主播档案沿用原有内容并采用公共样式。
- 列表和详情独立滚动；刷新保留有效选择和滚动位置。同一片段手动刷新保留播放器位置。过期读取或写入回调不会覆盖新页面，作业状态轮询不重建正在编辑的字幕表。

本轮仅修改 `frontend/` 与验收材料。既有未提交文件保留，后台源代码和规则未因本轮改版修改。没有提交、推送、部署或服务接管。

## 参考图与对照方式

参考图来自用户附件，目录为 `C:/Users/zk/.codex/generated_images/01a0ecc8-f081-7f83-b12f-24eccf57d61a/`：

| 页面 | 参考文件 | 最终截图 | 并排对照 |
| --- | --- | --- | --- |
| 设置 | exec-13ce057f-375c-4252-9520-a5acc4ca2a2e.png | [settings-1920.png](.runtime/frontend-qa-evidence/settings-1920.png) | [settings-compare.png](.runtime/frontend-qa-evidence/settings-compare.png) |
| 录播回收 | exec-39726a01-f214-48fe-ad91-d0aa2a300ff4.png | [recycle-1920.png](.runtime/frontend-qa-evidence/recycle-1920.png) | [recycle-compare.png](.runtime/frontend-qa-evidence/recycle-compare.png) |
| 发布队列 | exec-f852969d-54fd-4720-a86d-fcc7ac3b1b2a.png | [publish-1920.png](.runtime/frontend-qa-evidence/publish-1920.png) | [publish-compare.png](.runtime/frontend-qa-evidence/publish-compare.png) |
| 直播间 | exec-16ece28c-09f6-4c5c-b298-7c1b85136d28.png | [live-1920.png](.runtime/frontend-qa-evidence/live-1920.png) | [live-compare.png](.runtime/frontend-qa-evidence/live-compare.png) |
| 录播库 | exec-c0c4515a-08d5-4275-869e-f2b1b0381390.png | [library-1920.png](.runtime/frontend-qa-evidence/library-1920.png) | [library-compare.png](.runtime/frontend-qa-evidence/library-compare.png) |

参考图本身为 1487×1058，实际页面按用户指定的 1920×1080 验收。并排图对两侧分别等比缩小、标明尺寸并补灰底，没有拉伸参考图。对照检查布局关系、间距、文字层级、颜色、边框、选中态与操作位置；数据内容和业务状态来自验收接口，不能按附件固定数值比较。额外查看了 [字幕输入对照](.runtime/frontend-qa-evidence/subtitles-focus-compare.png) 和 [服务卡片对照](.runtime/frontend-qa-evidence/services-focus-compare.png)。

## 验收环境

应用内浏览器控制初始化失败，错误为 `node_repl kernel exited unexpectedly`，涉及 `apply deny-read ACLs`。经用户同意，改用本机 Playwright Chromium 143。最终截图及交互均使用 1920×1080、DPR 1；其他尺寸没有作为精细验收结果。

隔离验收曾使用 `http://127.0.0.1:18250/#/library`，此临时服务已关闭，不能作为正式使用入口。验收数据位于 `.runtime/frontend-qa-final-v4/`，测试脚本为 `.runtime/frontend_qa_server.py`、`.runtime/frontend_browser_qa.py`、`.runtime/frontend_interaction_qa.py`、`.runtime/frontend_extra_qa.py` 和 `.runtime/frontend_comparison_qa.py`。

隔离环境使用实际 API 应用、隔离数据目录、FFmpeg 生成的 12 秒 MP4、真实片段/字幕/成片文件和可控的录制、服务、队列响应。控制类接口替身只影响验收状态；成片重新生成实际运行隔离 Worker 和 FFmpeg。队列重试不执行上传消费者，回收入队测试不执行源文件移动。不使用生产凭据，也未启动或替换 2233/2234 的生产服务。

## 验证结果

1. 四个 JavaScript 文件均通过 `node --check`，`git diff --check -- frontend` 通过。
2. 五页最终截图检查：没有横向溢出，主容器高度与内容高度均为 1080；没有页面脚本错误。录播播放器容器和入出点行完整可见，字幕输入清晰，底部操作可达。长表格、详情、证据区采用独立滚动。
3. 现有接口回归：`tests/test_manual_workbench.py`、`tests/test_runtime_control.py`、`tests/test_recorder_integration.py`、`tests/test_source_review.py`、`tests/test_upload_queue.py`，**67 passed**。
4. 浏览器主流程 **14 组通过**，补充边界流程 **5 组通过**；均无 `pageerror`。原始结果见 [interaction-results.json](.runtime/frontend-qa-evidence/interaction-results.json)、[extra-results.json](.runtime/frontend-qa-evidence/extra-results.json) 和 [initial-results.json](.runtime/frontend-qa-evidence/initial-results.json)。

交互覆盖：场次/片段/房间回链、候选排序、标签和选中详情、队列分页、房间新增与控制、字幕原始时间草稿恢复、非法时间阻止提交、样式草稿、保存后旧成片失效、真实重新生成与播放、证据分页、人工批准后的编辑锁定、失败重试的远端媒体限制、已发布结果链接、第 5/7 天生命周期筛选、回收失败与重排拒绝原因、服务启动停止、只读配置、空态、失败状态，以及快速切页期间的过期响应。

补充检查了写入完成后切页、未完成场次的后台拒绝、审核保存成功但回收入队失败的分别提示、列表刷新后滚动位置和选中项保持、部分服务不可用与完全离线的区别。源媒体在回收测试后仍可读取。

状态截图：

- [未保存字幕](.runtime/frontend-qa-evidence/library-draft-1920.png)
- [已批准锁定](.runtime/frontend-qa-evidence/library-approved-1920.png)
- [发布失败](.runtime/frontend-qa-evidence/publish-failed-1920.png)
- [媒体不可用](.runtime/frontend-qa-evidence/publish-unavailable-1920.png)
- [直播间空态](.runtime/frontend-qa-evidence/live-empty-1920.png)
- [服务部分不可用](.runtime/frontend-qa-evidence/settings-partial-1920.png)
- [服务离线](.runtime/frontend-qa-evidence/settings-offline-1920.png)

## 视觉与行为修复记录

- P2：设置页摘要和诊断内容导致首屏过长。压缩卡片间距并将诊断收入展开区，最终主容器不溢出。
- P2：录播页底部卡片/操作栏挤出 1080P。调整编辑区高度和候选区间距，保留可见操作栏。
- P2：录播播放器按宽度自然撑高，入出点输入被截断。改为可收缩的媒体容器，视频保持完整画面；最终播放器区域 `clientHeight=scrollHeight=628`，控件底部 767px，小于容器底部 783px。
- P2：详情区继承旧样式造成双边框和多余留白。清理冲突并压缩回收文件项间距；长失败记录及清单仍可独立滚动。
- P2：缩略图加载导致列表刷新后约 4px 的滚动偏移。关闭列表滚动锚定后验证 250px 刷新仍为 250px。
- 行为问题：字幕新增/删除后重新编号，原始非法时间文本保存在草稿中；异步动作捕获原片段键并按页面代次判断，避免切页后误清新草稿或覆盖新页面。

## 已接受的呈现差异与未验证项

接口未提供直播封面、当前文件名和最近事件时，明确显示不可用。没有连续密度数据时不绘制柱状图；没有待处理总数时显示 `—`。服务设备可见和依赖探测没有被描述为推理验证。发布进度只显示真实阶段，不使用附件中的示例百分比。

短媒体验收使用测试画面，视频保留原生播放控件并保持完整比例；没有把参考图中的主播画面作为真实封面。长回收失败/清单详情需要滚动，操作在详情区内可达。品牌色统一采用深色标题风格。

生产 API 在线状态、真实登录、实际投稿结果、真实回收执行、真实录制时长推进和设备推理均未在本轮执行验证。生产页面连接原有同源接口，隔离验收数据不会写入前端。此次结论为本地前端与隔离接口验收通过。
