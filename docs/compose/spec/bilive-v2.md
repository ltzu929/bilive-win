---
feature: bilive-v2
status: designed
updated: 2026-09-30
branch: develop
commits:
---

# Bilive 新版（Windows 单机工作台）

## Report

## [S1] Problem

Bilive 需要在 Windows 单机上完成 B 站直播录制、弹幕候选召回、AI 分析、人工补漏与审核、字幕成片、人工确认投稿和录播回收。旧仓库 `D:\alldata\pi\bilive`（基线 `b2e2f10`）功能完整但带有 Pi/SMB/SSH 分布式遗留，且界面未按最新设计稿组织。需要在新目录 `D:\bilive` 从头组织项目：保留已验证的重处理工具边界与业务语义，用设计稿定义的工作台界面重新交付完整流程，而不是缩成字幕编辑器。

## [S2] Design

### 2.1 总体形态

- **产品**：Windows 单机 Bilive 工作台。浏览器入口浏览、预览、草稿、轻量编辑、提交任务与看状态；关闭网页不停止后台录制/处理。
- **进程边界**（沿规格 §8）：
  - blrec：`localhost:2233`，录制与弹幕兼容，不改历史媒体。
  - Dashboard/工作台轻接口：`localhost:2234`，只做校验、查询、草稿、原子写任务。
  - Worker：`127.0.0.1:2235`，预检、领取任务、分析、成片、转封装、人工动作执行、回收。
  - 上传消费者：Worker 管理的独立进程，无公开端口；从 SQLite 领取已授权行。
- **技术基线**：Python/FastAPI + SQLite + Angular；重处理工具（ffmpeg / faster-whisper / MiMo / 上传适配）按旧仓库已验证实现移植，不重写协议。
- **开发边界**：不修改旧仓库；不连接旧生产数据/凭据；真实投稿与接管服务需另行授权。

### 2.2 对象与状态（规格 §2–§3，必须等价）

核心对象：直播间、源录播（FLV/MP4）、候选、segment、成片与工件、字幕行与草稿、analysis sidecar、录像任务/任务历史、action job、上传队列行、录播生命周期记录、主播档案/经验/建议。

关键状态机（只允许规格定义的迁移）：

- 录像任务：`pending → processing → done | failed`。
- 动作任务：`.pending.json → .processing.json → .done.json | .failed.json`。
- 片段判断：`keep | manual_keep | review | judge_failed | drop`。
- 上传：`staged → queued → uploading → uploaded → publishing → published`。
- 生命周期：`review_state` × `trash_status`（见规格 §3.4）。

时间语义：源区间=源绝对秒；MiMo trim=候选相对秒；字幕=片段相对秒。禁止混用。

### 2.3 处理流水线（规格 §4）

录制结束转封装 → 候选召回 → 可选预 ASR → MiMo 判断 → 质量门/去重 → 正式字幕 → 裁剪烧录 → 元数据暂存 staged → 人工授权/上传投稿 → 终审回收。

人工动作：`retry-judge` / `finalize` / `render` / `reburn` / `approve_publish` / 人工补漏 / 整场无内容确认 / 拖拽回收。Dashboard 不执行重处理，只写 job 并唤醒 Worker。

### 2.4 UI 结构（以用户设计稿为准）

左侧全局导航：`录播库`、`直播间`、`发布队列`、`主播档案`、`录播回收`、底部`设置` + 服务状态点。

六个主页面按设计稿实现（视觉目标，不单是菜单）：

| 页面 | 一句话目标 | 关键区域 |
|---|---|---|
| 录播库（工作台） | 选场次、看源录播、候选、字幕与成片动作 | 面包屑、源录播/当前成片、视频+弹幕密度、入出点、片段候选横滑、字幕编辑表、finalize/reburn/允许发布 |
| 直播间 | 实时监控直播与录制任务 | 直播间列表（直播中/未开播/异常）、右侧详情（文件/已写入/时长/事件）、添加直播间 |
| 发布队列 | 跟踪成片上传投稿 | Tab（全部/待确认/处理中/已发布/失败）、列表+进度、右侧详情（来源录制/投稿结果/媒体上传）、重试/返回片段 |
| 主播档案 | 档案与审核经验 | 资料头、审核经验样本（正面/补漏/负面/技术记录）、待应用建议、当前指导 |
| 录播回收 | 到期回收与记录 | 即将到期/已到期/回收记录、回收范围（回收什么/保留什么）、强制到期说明 |
| 设置与服务状态 | 服务与配置 | 服务状态四项、任务概况、只读处理配置摘要、路径与账号摘要 |

视觉语言：浅色工作台、白底卡片、左侧栏淡蓝灰、主色蓝 `#2563EB`、警示橙、成功绿、中性灰描边；列表行带头图/时长徽标；详情右栏吸顶卡片。

### 2.5 后端模块划分（新目录）

```
D:\bilive\
  src/bilive/
    config/       # bilive-server.toml 解析、环境变量
    db/           # SQLite 连接、上传队列、只读失败语义
    jobs/         # action job 文件队列、互斥、原子写
    domain/       # 对象模型、状态机、身份函数
    media/        # remux、ffmpeg、字幕烧录（移植旧实现）
    analysis/     # 密度召回、MiMo 判断、ASR（移植旧适配）
    upload/       # 上传消费者、bilibili 适配（移植旧适配）
    lifecycle/    # 保留期、回收、整场终审
    api/          # FastAPI 轻接口（Dashboard）
    worker/       # Worker API、watcher、锁、空闲退出
  frontend/       # Angular 工作台（设计稿页面）
  tests/          # 与旧仓库对等的关键回归
  scripts/        # Windows 启动/安装脚本
  docs/compose/spec/
```

复用策略：不整仓复制；按模块从旧仓库移植已验证算法/协议（身份函数、质量门、job 互斥、上传重试、回收清单），在新包名下保留行为等价测试。删除 Pi/SMB/SSH 专属分支。

### 2.6 数据与配置

- `bilive-server.toml`：可入库默认配置（worker/video/upload/slice/…/dashboard）；机器绝对路径与秘密不入库。
- `.secrets/env` 与 Cookie 仅本机；不新建第二套秘密机制。
- 运行数据不提交：Videos、logs、artifacts、.runtime、SQLite、模型缓存。
- 开发期使用隔离空数据与脱敏样本；不指向旧生产目录。

## [S3] Out of Scope

- 多平台直播、多人协作、无人确认发布、通用插件平台。
- 修改或接管旧生产环境、复制旧真实媒体/队列/凭据、真实 B 站投稿（未授权时用模拟客户端）。
- 复刻旧五段看板徽章；旧 URL 全量兼容。
- 手机布局首轮验收。
- 把草图像素当唯一验收——以能力清单 + 设计稿视觉方向为准。

## Tasks

- [ ] T1: 项目骨架与配置基线 — acceptance: `D:\bilive` 可安装依赖并 `import bilive`；`bilive-server.toml` 默认值与规格 §7 一致；git 忽略运行数据/秘密 (covers: S2.5, S2.6)
- [ ] T2: 领域模型与任务持久化 — acceptance: 录像任务/动作任务/上传队列/生命周期对象可创建并遵守状态迁移；同目标动作互斥、payload 不可变 (covers: S2.2; depends: T1)
- [ ] T3: FastAPI 轻接口只读面 — acceptance: 缺库查询返回 unavailable 而非建库；列表有界分页；源/片段/媒体 ID 限制在配置根内 (covers: S2.1, S2.2, S2.3)
- [ ] T4: Worker 与 job 执行闭环 — acceptance: pending 可被唯一领取；预检失败保留 pending；空闲退出与真实忙碌可区分 (covers: S2.1, S2.3; depends: T2)
- [ ] T5: 应用外壳与六页面静态骨架 — acceptance: 左侧导航 + 六页按设计稿布局渲染；状态徽标/详情栏/Tab 结构正确 (covers: S2.4; depends: T1)
- [ ] T6: 录播库工作台交互 — acceptance: 场次→候选→字幕表可浏览编辑；草稿/已保存/冲突可区分；finalize/reburn/允许发布只提交 job 不在请求内处理 (covers: S2.3, S2.4; depends: T3, T5)
- [ ] T7: 发布队列与上传状态 — acceptance: staged 对外不可见为可发布；手动 retry 仅接受 failed 且无 remote_filename；published 必有 bvid (covers: S2.2, S2.4; depends: T2, T5)
- [ ] T8: 录播回收与保留期 — acceptance: 第5天预警、第7天排 force_expired；成片与审核记录保护；冻结清单部分失败可追踪 (covers: S2.2, S2.4; depends: T2, T5)
- [ ] T9: 主播档案与审核经验 — acceptance: 技术失败不作内容负样本；建议证据门槛（≥5 且正负都有）满足才生成；人工应用才更新指导 (covers: S2.4; depends: T3, T5)
- [ ] T10: 设置与服务状态页 — acceptance: 服务四项与任务概况来自真实状态；只读配置摘要不暴露秘密；唤醒/停止 Worker 显式生效 (covers: S2.4; depends: T4, T5)
- [ ] T11: 转封装与候选→成片链路 — acceptance: 验证 MP4 成功才删 FLV；低分/缺分/重复留人工；字幕保存后再烧录；部分 ffmpeg 输出不算成功 (covers: S2.3; depends: T4)
- [ ] T12: 回归与验收对表 — acceptance: 旧仓库 E2–E8 对应测试在新包下等价通过或标注未验证；A01–A13 覆盖表给出通过/未验证 (covers: S1, S2.2, S2.3)
