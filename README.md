# Bilive · Windows 单机直播切片工作台

Bilive 把一场 B 站录播整理成可投稿的切片。在录播库选择场次后，工作台用弹幕定位候选区间，结合音画提出选段与看点。你可以补选片段、改字幕、试听聊天精剪，再用成片、标题和封面一起检查投稿。

## 开始使用

本机已配置依赖和凭据时，双击 `启动Bilive.cmd`，或在项目根目录运行 `.\start_bilive.ps1`。启动器会补齐缺失服务并打开[本机工作台](http://127.0.0.1:2233/workbench/)。启动日志在 `logs/startup/`。首次配置见[安装与启动](docs/usage/README.md#安装与启动)。

1. **选片。** 在「录播库」选择场次，点「AI 分析本场录播」。也可在「主播档案」为常看的主播开启[每晚 23:30 批量分析](docs/usage/README.md#每晚-2330-批量分析)。进入「01 选择候选」后查看前后文，调整入点和出点，也可以手动补选。
2. **精剪。** 在「02 核对字幕」保存当前字幕，再到「03 确认成片」。聊天片段会准备精剪预览，可在剪点听衔接、看原段、逐段恢复。有要采用的剪点时，点「采用这版并生成成片」。
3. **投稿。** 点「去投稿」核对标题和封面。可下载所选原帧、复制本片的 GPT 提示词，在网页版生成封面后传回。只想留到稍后，点「保存草稿」。准备好后点「提交投稿」，在发布队列查看进度。

## 按需查阅

| 你想做什么 | 文档 |
|---|---|
| 配置工作台、处理候选、找回审核草稿或查保留期 | [使用说明](docs/usage/README.md) |
| 做封面、编辑投稿、查草稿和队列状态 | [投稿页说明](docs/development/submission-editor.md) |
| 查近期交付、真实切片待验项和后续方向 | [开发与验收计划](docs/development/optimization.md) |
| 修改 API、Worker、媒体或上传逻辑 | [项目结构与状态约定](docs/development/project.md) |
| 追溯旧实验、故障和验收证据 | [历史索引](docs/history/README.md) |

## 开发验证

在仓库根目录运行。成片回归需要 FFmpeg 和 ffprobe，会实际渲染并解码临时视频，检查首尾画面、拼接、字幕与重烧结果。

```powershell
.\.venv-win\Scripts\python.exe -m pytest -q tests/test_chat_trim.py tests/test_finalize_pipeline.py
.\.venv-win\Scripts\python.exe -m pytest -q
node --test tests/*.test.cjs
```

后端位于 `src/bilive/`，前端为 `frontend/` 下的原生 JavaScript/CSS。服务和维护脚本在 `scripts/`，Eagle 索引插件在 `eagle-plugin/`。服务模板为 [bilive-server.toml](bilive-server.toml)，本机配置通过 `BILIVE_CONFIG` 选择。视频判断使用远端 MiMo，字幕识别使用本地 faster-whisper。复用组件与许可证见[来源说明](src/bilive/vendor/PROVENANCE.md)。
