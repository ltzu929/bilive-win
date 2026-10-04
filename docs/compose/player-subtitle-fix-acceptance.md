# 播放器点击与本地字幕识别验收

日期：2026-10-03。范围：`D:\bilive` 工作台播放器与当前片段的本地转写。

## 修改

- 候选切换事件仅绑定 `button.candidate-card[data-segment]`，不再误绑定同样带有 `data-segment` 的源播放器。视频画面和原生控件使用浏览器播放行为。
- 字幕区增加“本地模型识别字幕”。API 提交持久化 `transcribe_subtitles` 任务，Worker 只提取当前已保存范围的音频，复用本地 faster-whisper 模型。
- 结果随动作任务保存，以片段相对秒返回；页面填入浏览器草稿，刷新或切回片段可恢复。保存字幕前不改变服务端字幕版本、旧成片或发布状态。
- 未保存字幕/样式、活跃任务及已批准片段限制再次识别；后台检查版本、源文件、时间范围和字幕时间戳。空语音与推理错误均显示失败，不冒充成功。
- 实机发现 faster-whisper 1.2.1 调用的 `metadata_errors` 参数在 PyAV 19 被移除。pipeline 依赖增加 `av>=11,<19`，本项目 `.venv-win` 调整为 PyAV 18.1.0。

## 验证

- `tests/test_manual_workbench.py`、`tests/test_asr_preflight.py`、`tests/test_analysis_pipeline.py` 定向回归：53 passed。随后新增适配器错误回归并修正错误显示，转写相关检查 7 passed，依赖预检/设置兼容检查 8 passed。
- `node --check frontend/workbench.js`、修改的 Python 文件 `compileall`、`pip check`、相关文件 `git diff --check` 均通过。
- 1920×1080 实际 Chromium：点击源视频画面开始播放，再点击暂停；两次操作均保持同一个视频 DOM。候选切换仍正常，页面无横向溢出。
- 实际 `#/library` 页点击新按钮：雪狐桑场次 `blive_24393_2026-10-03-142259.mp4`，源区间 1780–1838 秒；本机 large-v3 / CUDA float16 识别 30 行，持久任务 `c32dc40a4d7d4f0abc0fc5aa05593098` 状态 done，用时 18.127 秒。
- 页面收到 30 行字幕草稿，保存按钮可用；刷新及切换候选后返回均恢复草稿。服务端仍 revision=1、已保存字幕=0、publish_approval=false，没有保存字幕或批准发布。
- 页面截图：`.runtime/subtitle-player-fix.png`（运行证据，不纳入提交）。

字幕识别质量仍需用户试听核对。本轮未执行真实成片生成、上传或投稿，也未将完整录制到投稿流程标为通过。
