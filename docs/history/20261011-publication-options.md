# 投稿话题与合集接口接入（2026-10-11）

## 实现

成片页的投稿资料增加话题搜索、话题选择、合集/小节选择。片段 JSON、暂存队列、新成片暂存及上传消费者完整传递选择。浏览器恢复未提交选择，API 保留省略的选择字段、允许明确清空，并沿用版本、后台任务、已批准和暂存队列保护。

主播档案增加 `default_season` 与“默认加入合集”选择器，保存时从当前账号合集列表核对合集/小节并更新显示名称。新建手动片段和自动分析片段按源录播房间号复制选择，成片队列沿用片段选择，投稿成功后走独立合集关联。单片段可覆盖或明确清空；修改档案不会追写旧片段或线上稿件。档案清空不依赖网络，省略该字段保留已有默认。话题仍按单片段选择。

保存和投稿前读取当前话题详情及账号合集，验证话题有效、活动 ID/名称未变化、活动状态仍可用以及小节归属。付费、禁止和未通过状态的合集不提供选择。话题名称加入投稿标签计数，提交 `topic_id`、非零 `mission_id`、`topic_detail`。

当前 B 站网页在免费合集的首次投稿中，先投递稿件，再用 `sectionId`、`episodes` 加入合集。项目按此执行；旧资料中的 `section_id`、`episode` 未沿用。队列先持久化已投稿状态、BV 和 aid，再保存独立的 `season_status`/`season_error`。合集失败不进入投稿重试。发布历史允许单独重试加入合集；使用每行进程锁，与上传消费者常驻锁独立。

关联前核对稿件 aid/BV，读取 cid；查询当前合集，同一合集视为已加入，其他合集停止。写后再次查询合集 ID。归属接口不提供小节 ID，因此确认范围为合集 ID；选择的小节通过当前协议传入，不声称读回验证了小节。

## 当前接口证据

2026-10-11 使用项目已有 Cookie 加载器认证，不输出 Cookie、CSRF 或完整服务端结构。查询快照在忽略目录 `artifacts/research/publication-options-20261011/probe.json`。

| 接口 | 实网结果 | 验证边界 |
|---|---|---|
| `GET /x/vupre/web/topic/search` | HTTP 200、code 0，查到“搞笑研究所”等话题 | 搜索字段可读取，不证明有奖资格 |
| `GET /x/vupre/web/topic/info` | code 0；`is_valid=true`、当前活动 `mission_state=1` | 只验证查询与当前状态，不固定旧活动 ID |
| `GET /x2/creative/web/seasons` | code 0，MeUmy、李豆沙、雪狐桑三个合集及正片小节 | 账号可选列表 |
| `GET /x/vupre/web/archive/view` | code 0，aid/BV 和视频 cid 可读取 | 稿件与视频身份核对 |
| `GET /x2/creative/web/season/aid` | 已在合集的稿件 code 0，返回 `data.id`；未加入的稿件 code -404 | -404 是已观察到的无合集结果；其他失败保持报错 |
| `POST /x2/creative/web/season/section/episodes/add` | `sectionId=0, episodes=[]` 返回 HTTP 200、code 0 | 端点响应验证；没有真实稿件关联，不证明真实写入成功 |
| `POST /x/vu/web/add/v3` | 当前 B 站网页仍调用；新字段经过模拟请求验证 | 未真实投递测试视频或修改现有稿件 |

当前官方网页入口为 `https://member.bilibili.com/york/videoup?new`，加载的静态代码：
`https://s1.hdslb.com/bfs/static/creator-monorepo/videoup/static/js/index.9033cd2f.js`。
其中 `getSubmitTags`/提交构造使用话题字段，`addSeasonInfo` 使用 `sectionId` 和 `episodes`，`addVideoToSeason` 调用上述写接口。静态代码只读保存在忽略的研究目录，版本可能后续变化。

## 验证与运行状态

- 定向协议/队列/API 验证：80 项通过；另一次包含修正后的完整录制—审核—发布—回收用例的定向组合，80 项通过。
- 浏览器隔离验收通过：搜索/选择、草稿刷新恢复、保存到队列、明确清空、批准锁定、发布历史单独重试合集、无页面异常。截图和结果在 `output/playwright/publication-options/`。所有 B 站调用均为模拟，没有启动上传消费者。
- 全量首次运行 397 项通过、1 项失败：已有完整录播测试使用不足 30 秒样本，被短录播过滤规则排除。只修正该测试为 32 秒录播，并将模拟语音结束时间设为真实语句范围，避免用容器总时长模拟语句边界；该用例单独重测通过。
- 最终全量回归：399 项通过，耗时 230.95 秒；1 项现有 `zhconv/pkg_resources` 弃用警告。
- 工作台计划任务已重载，运行中 `/api/publication/topics`、`/api/publication/seasons` 查询成功，三个合集名称确认。录制器进程保持运行；当时 Worker/上传消费者未运行。
- 主播默认合集追加验收：97 项相关测试通过（136.84 秒），覆盖档案实时校验/省略保留/清空、房间隔离、自动成片带入队列、手动成片清空后重新生成。隔离浏览器验证档案保存、刷新恢复、新片段继承、清空默认不改变旧片段；无页面异常，截图 `output/playwright/publication-options/streamer-default.png`。本轮没有配置真实主播与合集的对应关系。
- 2026-10-12 提交前复验：使用项目 `.venv-win`、工作区隔离临时目录及 `pytest tests -q -ra -p no:cacheprovider`，全量 405 项通过，耗时 297.43 秒；1 项已有 `zhconv/pkg_resources` 弃用警告。前端 `pages.js`、`workbench.js` 和隔离浏览器验证脚本均通过 `node --check`，Git 差异空白检查通过。此次未重新执行浏览器或实网投稿验收；运行时 `.season-locks/` 已加入忽略规则，数据库与锁文件不提交。

复验只读接口：`.venv-win/Scripts/python.exe scripts/probe_publication_options.py`。
加 `--probe-write-route` 仅发送空 episodes，不传任何真实稿件 ID。隔离页面验收使用 `scripts/verify_publication_options_ui.py`，依赖本机已安装的 Node/Playwright/Chromium；Windows 沙盒不能启动浏览器时需在宿主环境运行。

下一条由用户人工批准的真实投稿仍需核对创作中心话题/活动关联和合集结果，才能补充端到端实网写入证据。当前没有替旧稿改话题、加入合集、报名或重新投稿。
