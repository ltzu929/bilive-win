# Bilive — Windows 单机直播工作台

用于 B 站直播录制、弹幕候选召回、AI 分析、人工补漏与审核、字幕成片、人工确认投稿和源录播回收。所有组件在 Windows 本机运行。

## 开始使用

本机已配置依赖和凭据时，双击 `启动Bilive.cmd` 或运行 `./start_bilive.ps1`，打开 `http://127.0.0.1:2233/workbench/`。缺失服务由启动器补齐，其他服务占用端口时拒绝覆盖，日志在 `logs/startup/`。

录制结束后先验证转封装；默认在录播库手动启动分析。按“候选 → 字幕 → 成片”审核，生成仅暂存，人工批准后进入上传队列。关闭网页不停止后台录制。

## 文档入口

- [使用说明](docs/usage/README.md)：启动、配置、审核、发布、清理与验证。
- [项目说明](docs/development/project.md)：产品约定、架构与实现边界。
- [优化计划](docs/development/optimization.md)：收益、质量及 MiMo 后续方向。
- [历史记录](docs/history/README.md)：过去的验收和问题调查，按需查阅。

完整目录见 [文档导航](docs/README.md)。

## 开发验证

修改裁剪、字幕烧录或重烧时，先运行成片回归；提交前运行全量 Python 回归：

```powershell
.venv-win/Scripts/python.exe -m pytest -q tests/test_finalize_pipeline.py
.venv-win/Scripts/python.exe -m pytest -q
```

成片回归使用临时目录中的真实 FFmpeg 音视频，不调用外部模型或投稿。生成测试检查解码后的片段首尾内容、时长和字幕出现/消失；重烧测试检查画面中的字幕确实改变。涉及这些行为的修改应保留上述结果检查，不能退化为“文件存在”或只核对 ASS 文本。小视频使用显式可读字号、纯色背景和容差，不固定字体栅格或编码文件字节；它不证明中文识别、真实选段质量或所有字体显示正确。NVIDIA 用例仅在检测到 `nvidia-smi` 时运行，软件编码回归不依赖 NVIDIA。需安装 FFmpeg 和 ffprobe。

## 项目目录

```text
src/bilive/   API、任务、Worker、媒体、上传、生命周期与复用适配
frontend/     原生 JavaScript/CSS 工作台
tests/        业务规则、媒体与任务回归
scripts/      服务、维护、计划任务与反馈采集入口
eagle-plugin/ 源索引书签同步插件
docs/         按用途分类的当前说明、计划与日期记录
```

服务模板为 `bilive-server.toml`，本机通常由 `.secrets/env` 中的 `BILIVE_CONFIG` 指向 `.secrets/runtime.toml`。凭据、机器路径、录播与运行数据不入库。视频判断和字幕校正默认使用 `mimo-v2.6-flash`；本地 ASR 仍为 faster-whisper large-v3。来源与许可证见 [PROVENANCE](src/bilive/vendor/PROVENANCE.md)。

人工发布批准、已批准媒体保护和根目录约束适用于全部入口。仓库模板关闭自动分析和自动清理；生命周期默认期限为未处理 5 天、全部保留片段发布后 24 小时。配置保存、生效和运行服务是不同状态，具体以设置提示及实际服务核对为准。
