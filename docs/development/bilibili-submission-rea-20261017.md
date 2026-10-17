# B 站投稿页逆向与 bilive 选项差距

调查日期：2026-10-17。范围：只读分析官网投稿入口、公开 JavaScript 及 bilive 当前投稿代码。没有上传视频、提交稿件、修改线上稿件或修改项目业务代码。

## 结果与验证边界

官网当前投稿模块使用 `/x/vu/web/add/v3`，与 bilive 当前客户端使用的提交入口一致。公开脚本中已定位更多表单与参数的对应关系，可以据此扩展本地工作台。

本轮临时浏览器没有账号登录态：从 `/platform/upload/video/frame` 跳转至 `https://passport.bilibili.com/login`。因此以下为**当前发布的前端代码证据**，不能视为该账号具备权限，或服务端已实际接受相应参数。未进行真实投稿验收。

从创作中心的路由代码定位到独立投稿模块 `https://member.bilibili.com/york/videoup`，随后获取该页面引用的公开脚本。下载的脚本作为静态文本分析，没有在本地执行这些下载文件。

## 可扩展选项

| 功能 | 官网脚本中的字段或行为 | bilive 当前状态 | 接入时的要求 |
| --- | --- | --- | --- |
| 动态文案 | `dynamic`，含 @ 等富文本时另有 `dynamic_v2` | 客户端支持纯文本 `dynamic`，UI、API、片段、队列未传递 | 优先接纯文本，并贯穿持久化和重试 |
| 定时发布 | 基础参数组装时将 `archive.dtime` 写入 `dtime` | 未接通 | 按官网和账号返回能力确认可选时间；区分提交成功、等待发布和公开可见 |
| 可见范围 | `is_only_self`；另有 `space_hidden` 控制字段 | 未接通 | 先验证账号能力和两个字段各自语义，不能合并为一个“隐藏”开关 |
| 关闭弹幕 | `up_close_danmu` | 未接通 | 官网由 `white.danmu` 决定显示能力 |
| 关闭评论 | `up_close_reply` | 未接通 | 官网由 `white.reply` 决定显示能力 |
| 精选评论 | `up_selection_reply` | 未接通 | 官网由 `white.selected_comment` 决定显示能力，需验证与关闭评论的关系 |
| 创作声明 | `creation_statement: {id: ...}`；选项取自 `pre.neutral_mark.mark_list` | 未接通 | 实时取选项，不硬编码声明 ID；官网提示添加后不可再次编辑 |
| 内容授权与投稿类型 | `copyright`、转载时的 `source`、`no_reprint` 联动 | 消费者固定 `copyright=1`；底层仅支持 1/2 | 不直接替换既有默认值，先确认当前官网三态语义，统一处理关联字段 |
| 自选封面及比例 | 官网组装 `cover`、`cover43`，还有智能/多封面相关字段 | 自动截首帧，上传单一封面；无自选入口 | 自选图片/选帧有实用价值；多比例、智能和多封面需单独验证 |
| 简介中的 @ | `desc_v2`，并从富文本生成 `desc` | 仅纯文本简介 | 与纯文本分开建模，验证用户 ID 与文本的对应关系 |
| 合集投稿不打扰 | 存在小节且启用 `noDisturbance` 时组装 `no_disturbance=1` | 已有合集关联，但没有这一选项 | 官网会同时清空并锁定动态编辑；不能简单独立添加布尔字段 |
| 平台字幕 | 官网存在字幕文件列表与按任务配置能力 | 仅烧录字幕；客户端固定 `subtitle.open=0` | 是独立字幕资源流程，不能用烧录字幕成功代表平台字幕成功 |

商业推广、联合投稿、充电/付费、首映、预约、章节等也有前端代码，但涉及额外权限、资源或独立流程。本轮未把它们判定为可直接接入功能。尤其 `recreate` 在新投稿中固定为 0、编辑态读取旧稿状态，不能仅凭字段名把它暴露为“允许二创”。

## 重要协议细节

当前官网枚举为 `ORIGINAL:1, REPRINT:2, UNSELECTED:3`，声明内部另有 `NO_MARK:-1, REPRINT:-2`。这些是当前脚本中的具体取值，不能推广为完整的服务端协议承诺。

`deriveCopyright(statement, authChecked)` 在转载声明时返回 2，勾选内容授权时返回 1，否则返回 3。新投稿中的 `no_reprint` 由 `copyright === ORIGINAL` 推导。UI 授权文案是“内容为自制：未经作者允许，禁止转载”。因此创作声明、版权类型、禁止转载不是三个完全独立的配置。

官网通过 `/x/vupre/web/archive/pre`、`/x/vupre/web/archive/white` 等只读接口取得声明和能力数据。本轮仅从公开脚本确认调用关系，未读取账号态接口结果。

## bilive 接入位置

目前已有标题、简介、TAG、数字分区、话题/活动、合集/小节和默认封面。应复用现有链路：

`frontend/workbench.js` → `MetadataRequest` → `save_metadata` 片段快照 → `UploadQueue.stage` / 数据库 → `UploadConsumer` → `BilibiliWebClient`。

关键位置：

- `frontend/workbench.js:1191`：现有分区、话题和合集输入。
- `frontend/workbench.js:1295`：元数据保存请求。
- `src/bilive/api/app.py:228`：请求模型。
- `src/bilive/media/workbench.py:179`：片段元数据保存和批准状态保护。
- `src/bilive/upload/queue.py:46`：队列暂存。
- `src/bilive/upload/consumer.py:48`：投稿元数据组装，当前版权固定为 1。
- `src/bilive/vendor/upload/bilibili_web.py:25`：提交 payload。
- `src/bilive/vendor/upload/publication_options.py:124`：独立合集关联流程。

以上行号来自本轮工作区快照，后续修改可能移动。合集关联现有独立失败/重试状态应保留；关联失败不应触发重新投稿。主播档案默认值应继续只作用于新片段，保留单片覆盖和明确清空。

建议首先接入动态文案、自选封面、定时发布；随后根据账号能力接入可见范围和评论/弹幕控制。创作声明值得接入，但要先核对账号返回选项及与版权默认值的兼容性。此处为实现建议，尚未修改业务功能。

## 证据与局限

- REA 浏览器观察 Evidence：`ev_3fac4963c854ea7b9b0db44d57f4c28396f0dbbdf1bd7e48ede1bc34da15166e`。观察到了登录重定向；部分网络响应因 64 KiB 内容预算未保留，不能视为完整抓包。
- REA 静态分析父 Evidence：`ev_09ccb061077f830fb7b92353fdfe3f55194b06b62710976269a305c0ff0e89e2`。
- REA 静态摘要 Evidence：`ev_d5d7a48f0acedfb1e53c83179c29cad2f14a9bc48690aeec2928035c55314a56`。只选择主入口脚本，解析无失败；语义图设置 2000 节点上限，覆盖为 partial，不代表完整调用链已还原。本文参数映射另由源码上下文逐项核对。
- 公开脚本：[index.a0b85e27.js](https://s1.hdslb.com/bfs/static/creator-monorepo/videoup/static/js/index.a0b85e27.js)、[607.f7ad1303.js](https://s1.hdslb.com/bfs/static/creator-monorepo/videoup/static/js/607.f7ad1303.js)。它们是本次获取的官网构建文件，不保证后续不变。
- 本地原始公开脚本：`D:\bilive\.runtime\bili-rea-20261017\videoup\`。
- 本地字段摘录与主脚本 SHA-256：`D:\bilive\.runtime\bili-rea-20261017\field-evidence.json`。偏移是零起始 UTF-16 单元位置，不是字节位置。

仍待验证：登录后该账号可用能力、定时发布的完整时间规则和服务端限制、选项互斥规则、真实请求与回读结果。没有为了验证而提交测试稿件，也没有改变现有队列或服务状态。
