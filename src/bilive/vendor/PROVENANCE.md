# Reused source

Source: Bilive repository `D:/alldata/pi/bilive`.
Pinned Git commit: `b2e2f10de03423515ee0751d18b7928226d8de5d`.

The following files were read using `git show` at the pinned commit, retaining
their copyright and implementation. `src.*` imports were redirected to
`bilive.vendor.*`. No old application, database, credentials or runtime data is imported.

- `src/autoslice/burst_detector.py`
- `src/autoslice/analysis_result.py`
- `src/autoslice/mllm_sdk/mimo_video.py`
- `src/autoslice/mllm_sdk/audio_analyzer.py`
- `src/cuda_runtime.py`
- `src/upload/bilibili_web.py`
- `src/blrec_danmu_fix.py`
- `src/upload/cookie.py`
- `src/upload/models.py`
- `src/upload/bilibili_runtime.py` (upstream imports use the installed bilitool package)
- `src/dashboard/recording_trash.py::move_to_recycle_bin` copied without changes
  into `bilive/lifecycle/recycle.py`; only its standalone imports and provenance header are new.
- `src/blrec_compat.py` and `src/blrec_patch.py`: FPS normalization/guard;
  normalize import points to `bilive.vendor`. Patched hash recalculated for that import.
  The archived Windows source has CRLF while official PyPI beta.4 has LF;
  both exact known source/patch hashes are registered, unknown versions/bytes still refused.
- `src/server/recorder_server.py` Cookie read/load/dump functions in
  `bilive/recorder/cookie.py`; runtime Cookie remains excluded from TOML dumps.
- `src/server/recording_remux.py` event subscription/dispatch adapted to v2
  ActionJobStore and local WorkerControl; remote/Pi branches removed.
- `src/server/studio_proxy.py` stream proxy in `bilive/vendor/studio_proxy.py`;
  adds explicit /workbench page/assets mapping and validates local-only upstream.
  Upstream error bodies are not reflected. Native /api/v1 stays in blrec.
- Profile persistence, evidence gate, recommendation generation/application,
  density validation functions extracted from `src/dashboard/source_lifecycle.py`
  into `streamer_profiles.py`. OS locking uses v2 ProcessLock; malformed profile
  state now fails closed. Review evidence collection adapts v2 segment shapes.
- `SubtitleStyle` and its number formatter extracted from `src/burn/subtitle_burn.py`;
  v2 additionally validates finite ranges and ASS-safe input before saving/rendering.
- `eagle-plugin` files and Node tests reused from this pin. Entry defaults to local
  2233 `/studio-api`, with `/workbench` links. Sync planning/apply behavior unchanged;
  only bookmarks are mirrored. Source index field contract adapted to v2 records,
  with no live-cover network fetch and unavailable roots refusing an empty snapshot.

Recorder dependency is official PyPI `blrec==2.0.0b4` in `.venv-recorder`
(Python 3.10 / FastAPI 0.88 / Pydantic 1), separately from modern `.venv-win`.
No patched wheel from the old deployment is imported or shipped.
The exact upstream FPS source fixture is retained only to verify the guard;
its GPLv3 license is `BLREC-LICENSE`.

Configuration and logging bridges are new adapters to the v2 settings/runtime.
Neither upstream `auto_slice_video` nor `bilitool` submodules are forked here.
The baseline's bilitool submodule records local compatibility commit
`5518ffff1b288bbdc7b90547ab6defb32ce887bd`, authored in the old checkout and
unavailable from the configured public upstream. Its sole change from upstream
parent `aefaff618235d7f94db35213a1c938126b25cac7` is `BiliUploader.publish_video`.
Both the baseline runtime client and this migrated client use bilitool only for
CDN upload; Web publishing uses the reused `BilibiliWebClient`, never that method.
The installable dependency therefore pins the exact public parent. Git comparison
proved its CDN/controller/Model code unchanged by the local commit. This is an
explicit dependency adaptation, not a fork or a silent replacement of publishing.
Installing/importing it does not grant publish authorization.

Pinned tests copied with import paths adapted:
`test_mimo_video_analyzer.py`, `test_burst_event.py`,
`test_bilibili_web_submit.py`, `test_audio_model_unload.py`.
Also reused `test_studio_proxy.py`, `test_blrec_danmu_fix.py`, and FPS-specific
cases from `test_blrec_hardening.py` with namespace/fixture paths adapted.

Automatic analysis alignment adaptations:
- Sentence snapping and timestamp slicing functions extracted from pinned
  `src/autoslice/candidate_analyzer.py`; operate on one explicit timestamp origin.
- Score rank, time/text similarity and duplicate marking extracted from pinned
  `src/burn/slice_only.py`; quality callback and segment ID binding adapted.
  Invalid/nonfinite intervals are routed to review before rank/ID generation.
- `src/autoslice/transcript_correct.py` copied with namespace imports adapted.
  Pipeline additionally checks that cue count/timestamps are unchanged and keeps
  original subtitles if correction fails or violates that contract.
- MiMo encoder retains its original global semaphore default for reused callers;
  an optional per-pipeline semaphore implements explicit encode_concurrency.
  Candidate preparation/judgment uses bounded request_concurrency; GPU ASR remains
  serialized. Results are deduplicated before formal ASR, independently of return
  order, with a second check before render if alignment creates new overlap.

CUDA preflight:
- `_configure_cuda_dll_search_paths` and `_check_cuda_asr_runtime` extracted from
  pinned `src/server/preflight.py`, using the reused CUDA helper.
  Worker resolves the local cached model before probing; successful probes are
  cached, failed probes are cleared so dependency repair is visible on retry.
  Settings diagnostics only inspect cache/device/compute types, never claim
  successful inference from device visibility.

Historical maintenance:
- `probe_media`, `validate_media`, `remux_flv`, `recover_recording`,
  `recover_recordings`, and `audit_mp4_recordings` extracted unchanged from pinned
  `src/maintenance/runtime_cleanup.py`; its database reset code is excluded.
  The v2 CLI wraps per-file operations with configured root/protected artifact
  checks, read-only authoritative queue access and explicit execution controls.
- Task trigger/hidden interactive startup semantics adapted from pinned
  `install_windows_startup_tasks.ps1` and retention-task installer. v2 task plans
  use separate names/path, default to plan output, and refuse foreign/running
  targets rather than stopping existing processes during registration.
- Configuration compatibility maps pinned `src/config/server_config.py` keys to
  v2 settings, preserving existing environment priority. Dashboard Host/private
  address checks and same-origin writes follow pinned `src/dashboard/app.py`.
