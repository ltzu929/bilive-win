# Bilive v2 — Windows 单机工作台

按《Bilive 新版开发规格》在本目录从头组织的 Windows 单机产品：B 站直播录制、弹幕候选召回、AI 分析、人工补漏与审核、字幕成片、人工确认投稿和录播回收。

## 运行

```powershell
# 开发安装
cd D:\bilive
pip install -e ".[dev]"

# 启动工作台（默认读取 bilive-server.toml）
$env:PYTHONPATH = "D:\bilive\src"
uvicorn bilive.api.app:app --host 127.0.0.1 --port 2234
```

浏览器打开 `http://127.0.0.1:2234/`。

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
python -m pytest tests -q
```
