# 2026-10-13 MiMo 视频 Batch 可行性验证

用户同意先验证同一视频的 Flash/Pro Batch 请求，再开展小批质量对照。实验与脚本在忽略目录 `artifacts/research/mimo-batch-20261013/`，不进入生产片段或上传队列。

## 固定输入与检查

复用已有呜米研究 c1，源区间 7147–7387 秒，240 秒候选内有歌回计划、团子来源和下播。使用已保存辅助 ASR、弹幕、当前生产提示，未沿用旧研究的宽松保留标准。两条请求除模型名与 custom_id 外，视频、提示及参数完全相同。

模型为 `mimo-v2.6-flash` / `mimo-v2.6-pro`；相同 fps=1、media_resolution=default、thinking=disabled、temperature=1，max_completion_tokens=4096。研究预算比生产 2048 更宽，但两模型一致；没有修改生产配置。

本地已检查请求 JSON、唯一 custom_id、两组输入一致、源候选大小/mtime 未变与文件大小。结果校验器通过离线样例：有效 keep、空 clips、缺失 ID、意外/重复 ID、返回模型不符、length 截断及越界选段。未运行全量回归，离线检查不代表视频 Batch 推理或语义质量已通过。

## 首次上传失败与收缩输入

初始 Base64 39,481,800 字节，2 行 JSONL 79,002,974 字节，均低于文档说明的 50 MB / 128 MB。文件服务仍返回 400，称第 1 行无法解析为 JSON；本地解析通过，服务未返回文件 ID，也未创建推理任务。

只做上传诊断，不提交推理：4 个文本文件（184 字节至约 1 MB）和 3 个相同字段文件（视频 URL、4 MB / 16 MB 假 Base64）均被接受。假 Base64 文件仅用于文件语法诊断，不用于模型。结果支持大小/长行限制的排查方向，但未定位服务端阈值，不据此断言视频不支持。

重新采用现有适配器的低码率路径（450k 视频 / 64k 音频），保留 720p、源帧率和完整候选，研究 Base64 限制为 28 MB，最终 2 行 JSONL 为 40,966,174 字节。两模型共用同一副本，生产码率未变。原始拒绝文件和 manifest 保留。

## 暂停与远端结果

第二次 40,966,174 字节文件上传仍返回相同的 400 JSON 解析错误，未返回文件 ID，未创建推理任务。2026-10-13 用户明确要求先暂停，后续上传、模型调用、质量对照与自动跟进均停止；未建立后台监控。上传、任务创建与结果验证分别记录。最终任务状态以本地 `batch.json`、`status.json` 和 `validation.json` 为准。当前尚未取得推理结果，不能比较模型质量或宣称视频 Batch 端到端通过。

文件上传诊断不创建推理任务。第一阶段上限 2 条推理请求，失败不自动付费重试；通过后再冻结第二阶段样本与提示。实际账单尚未查询，不能把 tokens 估算当作已确认费用。源录播、原任务、片段、批准与投稿状态没有修改，也没有重启服务。

官方资料（2026-10-13 核对）：[Batch API](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/text-generation/batch-api)、[视频理解](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/multimodal-understanding/video-understanding)、[Chat Completions](https://mimo.mi.com/docs/zh-CN/api/chat/openai-api)。Batch 为闲时调度，文档列出夜间 1:00–8:00，不保证提交后立即完成。
