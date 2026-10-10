# Bilive v2 — Windows 单机工作台

按《Bilive 新版开发规格》在本目录从头组织的 Windows 单机产品：B 站直播录制、弹幕候选召回、AI 分析、人工补漏与审核、字幕成片、人工确认投稿和录播回收。

## 运行

已配置本机凭据和依赖时，双击 `启动Bilive.cmd`，或运行 `./start_bilive.ps1`。
它在后台分别启动工作台、Worker 和录制器，再打开 `http://127.0.0.1:2233/workbench/`。
当前项目已运行时直接打开；如果只启动了部分服务，会补齐缺失服务。其他服务占用端口时拒绝覆盖。启动日志在 `logs/startup/`。
机器配置保存在忽略的 `settings.toml`、`.secrets/env`、`.secrets/runtime.toml` 和 `.secrets/bilibili.cookie`。

Bilive 的录制器、工作台、Worker 和上传进程启动时均强制直连：清除本进程的代理变量，并设置 `NO_PROXY=*`，以绕过 Windows 系统代理。此设置不修改系统代理或其他应用；直播 CDN、MiMo 和上传请求均适用。若以后自行开启代理软件的 TUN/透明代理，需要在该软件内另外保持 Bilive 流量直连。

```powershell
# 建立相互独立的运行环境（需要 uv、Git，媒体处理需要 FFmpeg）
cd D:\bilive
.\setup_windows_workbench_env.ps1
.\setup_windows_recorder_env.ps1

# 两个终端分别启动
.\start_windows_dashboard.ps1
.\start_windows_recorder.ps1
```

录制器环境为 Python 3.10 / blrec 2.0.0b4 / FastAPI 0.88；工作台和 Worker 使用现代 `.venv-win`。
录制器 API key 在忽略的 `.secrets/env` 配置 `BLREC_API_KEY`。录制和投稿 Cookie 使用忽略的 `.secrets/bilibili.cookie`，不写入 settings.toml。
浏览器工作台入口是 `http://127.0.0.1:2233/workbench/`，打开 `http://127.0.0.1:2233/` 也会进入新版工作台。
原生录制管理页面单独保留在 `http://127.0.0.1:2233/tasks`。
`/studio-api/` 同源代理到工作台 2234；原生 `/api/v1/` 保留给 blrec。2235 仅为本机 Worker 控制。
直接查看轻接口也可打开 `http://127.0.0.1:2234/`。

任务提交后工作台会按需唤醒 Worker。也可以在另一个终端手动运行本机控制服务（首次由写侧初始化上传库）：

```powershell
cd D:\bilive
$env:PYTHONPATH = "D:\bilive\src"
.\start_pipeline.ps1
```

在录播库选择配置目录下的 MP4，保存入出点与人工依据，添加带相对时间的字幕并保存，再生成成片。
页面显示持久任务状态；Worker 离线时任务等待。成片成功只暂存为 `staged`，人工允许发布后才进入 `queued`。
Worker 管理独立上传消费者；依赖、Cookie 或登录不可用时暂停。`queued` 不代表视频已经上传或投稿。

隔离运行可设置 `BILIVE_VIDEOS_DIR`、`BILIVE_DB_PATH`、`BILIVE_ARTIFACTS_DIR`、`BILIVE_JOBS_DIR`。
API 与 Worker 必须使用相同路径；默认分别是 `Videos`、`src/db/data.db`、`artifacts`、`.bilive-jobs`。
运行时始终从项目根目录启动。

Windows Worker 默认使用低于正常的进程优先级，不固定 CPU 亲和性。
项目内 FFmpeg 一次只运行一个转码，软件解码、软件编码和滤镜线程数限制为 4。
字幕识别默认批量为 2、CPU 线程为 2，可在 `slice.subtitle` 配置中调整。
Whisper 模型在同一场分析的所有候选和补做的正式识别之间复用。`unload_audio_model_after_analysis = true` 在整场任务结束或异常退出时释放模型；单独识别字幕、重新判断则在各自任务结束时释放。没有加载模型的任务不等待显存释放。
这些限制优先保证桌面响应，处理时间可能增加；它们不保证 GPU 利用率、显存或磁盘带宽的硬上限。
代码或配置变更需重启 Worker 生效，运行中的任务应先完成。

AI 分析仅在生成单个片段时持有该片段的锁，不在转码、模型识别或远程判断期间锁住整个工作台。
已完成片段的字幕可在同场其他候选仍分析时保存；版本校验、发布保护和回收期间禁止编辑仍然有效。

字幕草稿按场次和片段缓存在当前浏览器，保存使用服务端版本校验。修改字幕或范围会撤回未批准队列行并使旧预览失效；
已允许发布或进入上传流程的成片禁止覆盖。范围修改保留片段身份，但需要重新确认并保存相对时间字幕。

本轮实现与验收边界见 [人工审核验收记录](docs/compose/manual-workbench-acceptance.md)。
后续全项目进度与验收差距见 [完整开发跟踪](docs/compose/development-status.md)。

手动选择录播后启动 AI 分析，分析接入 Worker：源 MP4 需要同名 XML 弹幕；MiMo API key、faster-whisper 和本地模型依赖不足时保留 pending。
生产处理依赖声明在 `pipeline` 可选组；bilitool 固定到可下载的上游提交 `aefaff618235d7f94db35213a1c938126b25cac7`。
旧子模块 `5518ffff` 仅修改本项目不调用的 publish_video；CDN/controller/Model 与其公共父提交一致，Web 投稿修复由复用客户端保留。
来源及适配关系见 `src/bilive/vendor/PROVENANCE.md`。没有模型缓存时不会在预检中下载模型。

上传消费者入口为 `python scripts/start_upload_consumer.py`，需要固定 bilitool 和忽略的 `.secrets/bilibili.cookie`。
该入口只消费已人工授权的行；Worker 会管理这个独立进程。开发验收不使用真实 Cookie，也不执行真实投稿。

整场审核入口位于录播库。所有保留片段须完成当前版本成片并允许发布，内容丢弃须保存依据；
零候选或无可保留内容必须明确勾选确认。审核记录独立保存，再排源包回收；排队失败会单独展示。
回收页读取持久清单与逐项结果，部分失败可重排；未知移动结果会停止。已发布且本地成片已清理不阻止终审。
源包由 Windows 回收站处理，成片、历史成片与独立审核记录受保护。

每日保留期维护入口：`python scripts/recording_retention.py`。它持久化第 5 天预警并在第 7 天排到期回收，
只排队，不启动 Worker 或清空其他任务。需要处理到期任务时使用工作台启动 Worker。
设置页提供真实服务探测、Worker/上传消费者控制及当前配置摘要。

直播间页读写 blrec 原生房间配置；录制结束事件持久化转封装动作，验证 MP4 后才删除 FLV；默认不会排入 AI 分析；仅当用户在“设置 → 处理参数”开启并保存“录制结束后自动 AI 切片”时，才为新完成的录播排入分析。
默认在录播库选择场次后，点击“AI 分析本场录播”才提交分析；可取消等待或跳过；跳过仍需整场人工审核。processing 中禁止冲突控制。

主播页按房间保存档案、密度默认参数、标签简介、字幕样式及人工指导；这些设置用于新分析或新建片段，已存在片段保留自己的快照。
终审保存字幕/弹幕和区间依据，技术失败单独记录。建议至少 5 条有效人工样本且同时有正负样本，生成后必须人工应用。
Eagle 插件目录为 `eagle-plugin`，默认通过 2233 同源入口读取完整源索引，只创建和更新书签卡片；源消失只移除镜像卡片，不复制或反向删除视频。
索引根不可用或读取失败时停止同步。插件 API 模拟回归已覆盖增量计划，尚未在真实 Eagle 应用中安装验收。

## 目录

```
src/bilive/     领域模型、状态机、任务队列、配置、API、Worker
frontend/       工作台界面（设计稿六页面）
tests/          领域与任务回归
docs/compose/   规格与交付记录
bilive-server.toml
```

## 约束（来自规格）

- Dashboard 轻接口只做查询/草稿/原子写 job；重处理归 Worker。
- 只读接口缺库返回 `unavailable`，不隐式建库。
- 手动 upload retry 仅接受 `failed` 且无 `remote_filename`。
- 只有明确 MiMo `drop` 才允许自动删候选。
- 源/片段/媒体 ID 必须限制在配置根内。
- 不把 Pi/SMB/SSH 分支带回本项目。

## 测试

```powershell
$env:PYTHONPATH = "D:\bilive\src"
.\.venv-win\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.\.venv-win\Scripts\python.exe scripts/verify_upload_adapter.py
node --test eagle-plugin/tests/*.test.mjs
```

自动分析按配置分别限制 MiMo 请求与分析副本编码并发。正式字幕使用有界 padding 窗口、有效预跑复用与句边界吸附，字幕时间相对最终片段起点。
低分、缺分和跨候选重复先留人工；对齐后新形成的重复保留字幕但不渲染。轻量校正失败或改动时间/行数时保留原转写。
这些阶段仍不能替代人工发布批准。

已有片段可保存字幕样式并重烧。新片段先使用全局字幕配置，再合并主播档案覆盖；保存样式会保留当前字幕，撤回未批准 staged，并要求重新生成。已批准成片受保护。
自动候选的“重新判断”调用原候选重新获取 MiMo 建议，保存独立判断记录；原范围、字幕、成片和发布队列保留。选择建议只填写编辑草稿，仍须人工保存并生成当前成片。
录播库的拖拽回收与回收按钮执行同一终审检查，不直接移动文件。

设置页显示本地 ASR 模型缓存、CUDA 设备和计算类型；这些轻量诊断不会加载模型。Worker 在领取正式分析前实际加载本地模型并执行短 CUDA 推理探测，失败保留 pending，修复依赖后重新检查。模型解析及转写均使用本地缓存，不隐式下载。
模型探测在动作队列锁外进行，领取前重新核对取消/终态及载荷。上传列表及路径查询使用 SQLite 只读连接，缺库或缺必要表返回不可用，由显式写侧启动初始化。

录播页“分析证据与占用”读取已有历史，显示候选密度/突增、阶段错误、工件和本场文件大小，支持候选分页及源定位；不会由查询触发分析。任务终态保存本次执行耗时。

处理配置支持旧 `slice.multi_modal` 中的 Whisper 参数、VAD 细节和释放模型选项，旧 `slice.subtitle` 外观/校正参数及 `slice.analysis` padding/吸附参数。已有新版键与旧别名同时存在时优先新版键，已支持的环境覆盖仍优先。判断和字幕校正使用 `slice.mimo.base_url`；旧本地视觉地址/模型不作为生产判断入口。
`dashboard.allowed_hosts` 和 `BILIVE_DASHBOARD_ALLOWED_HOSTS` 允许额外请求 Host；默认允许本机/私网，同源写入仍必需。它们不代表跨域写入白名单。

历史媒体维护与每日保留期是独立入口。先停止录制，并确保 Worker/上传消费者和持久动作队列空闲；该命令不重置上传库。

```powershell
# 默认只报告，报告文件放在源媒体/操作工件之外。
.\.venv-win\Scripts\python.exe scripts/recording_history.py --report .runtime/reports/history.json --audit-mp4
# 显式执行历史 FLV 修复；验证 MP4 后才移除 FLV。
.\.venv-win\Scripts\python.exe scripts/recording_history.py --report .runtime/reports/history.json --execute --audit-mp4
```

只有另加 `--delete-invalid` 才删除已确认无效的历史文件；默认保留。登记的成片/上传路径受保护，无法读取权威库时停止，查询不建库。日常转封装不使用此删除选项。

计划任务脚本默认输出计划；显式 `-Install` 才注册到独立 `\BiliveV2\` 路径。登录任务分别通过 `pythonw.exe` 和无控制台启动器运行录制器和工作台，输出保存在 `logs/startup/`，不弹出终端窗口。保留期任务每日 03:00，支持错过后补运行；维护仍只排队，Worker 按正常流程处理。同目录旧登录任务可只更新下一次启动入口，保留当前运行进程和任务设置；不属于本目录的同名入口仍会被拒绝。

```powershell
.\install_windows_startup_tasks.ps1
.\install_windows_recording_retention_task.ps1
# 实际安装需另行决定：上述命令加 -Install。
```

录播库候选下方提供“废弃当前片段”，原因选填。废弃会撤回未批准成片暂存并永久阻止该片段继续生成或发布，保留源录播及审核记录。已批准发布或存在活跃任务的片段沿用保护规则。

自动 AI 切片开关为 `analysis.auto_start`（兼容 `slice.analysis.auto_start`），默认 false；Worker 每次转码后读取当前设置，关闭会取消等待中的自动任务，已开始任务完成当前分析。开启不会追溯分析历史录播，发布仍需人工确认。

`.runtime/worker-paused` 存在时暂停 Worker 自动唤起，待处理任务继续保留；录制服务仍正常录制。通过工作台显式启动 Worker 会解除暂停。

裁剪与字幕烧录使用 `[video].encoder`：本机配置为 `h264_nvenc`（NVENC p5、VBR CQ 26，兼容的 yuv420p H.264）；软件编码可显式选择 `libx264`，环境覆盖为 `BILIVE_VIDEO_ENCODER`。硬件不可用会报告失败，不自动重复转码。硬件编码质量参数与 x264 CRF 不等价，文件体积可能更大。
