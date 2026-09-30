/* Bilive Workbench SPA — 对齐设计稿六页面 */
const $main = document.getElementById("main");
const $nav = document.getElementById("nav");

const DEMO = {
  recordings: [
    { id: "s1", title: "第一次上台的经历", time: "09月30日 21:10", crumb: "阿橙 › 聊天直播", dur: "01:24:16", status: "待发布确认", tone: "orange", progress: 0.55, stage: "审核成片", selected: false },
    { id: "s2", title: "老电影闲聊", time: "09月30日 19:35", crumb: "阿橙 › 聊天直播", dur: "02:16:05", status: "处理中", tone: "blue", progress: 0.62, stage: "上传中 62%" },
    { id: "s3", title: "旅行趣事", time: "09月30日 16:20", crumb: "阿橙 › 聊天直播", dur: "01:38:27", status: "投稿重试中", tone: "orange", progress: 0.85, stage: "媒体已上传" },
    { id: "s4", title: "直播里的小插曲", time: "09月30日 14:08", crumb: "阿橙 › 聊天直播", dur: "02:05:12", status: "需要人工核对", tone: "orange", progress: 0.85, stage: "媒体已上传", selected: true },
    { id: "s5", title: "难忘的演出", time: "09月30日 11:22", crumb: "阿橙 › 聊天直播", dur: "01:57:43", status: "已发布", tone: "green", progress: 0, stage: "-", action: "查看结果" },
  ],
  lives: [
    { name: "阿橙", topic: "夜间杂谈", crumb: "阿橙 › 聊天直播", live: "直播中", liveTone: "red", rec: "录制中", recTone: "blue", dur: "01:26:18", selected: true },
    { name: "林间", topic: "游戏杂谈与日常", crumb: "林间 › 游戏直播", live: "未开播", liveTone: "gray", rec: "等待开播", recTone: "gray", dur: "--" },
    { name: "小鱼", topic: "我的小小歌回", crumb: "小鱼 › 音乐直播", live: "直播中", liveTone: "green", rec: "已暂停", recTone: "gray", dur: "00:43:21" },
    { name: "鹿眠", topic: "杂谈小屋", crumb: "鹿眠 › 聊天直播", live: "连接异常", liveTone: "red", rec: "未录制", recTone: "gray", dur: "--" },
  ],
  recycle: [
    { session: "阿橙 09月23日夜间杂谈", due: "09月30日 14:00", review: "仍有 2 个待审核", reviewTone: "orange", trash: "已排队 · 等待处理", trashTone: "blue", selected: true },
    { session: "林间 09月23日聊天", due: "09月30日 12:00", review: "已复核", reviewTone: "green", trash: "回收完成", trashTone: "green" },
    { session: "小鱼 09月22日", due: "09月29日 20:00", review: "未复核", reviewTone: "red", trash: "部分失败 · 查看原因", trashTone: "red" },
  ],
  segments: [
    { id: 1, title: "1. 开场和今天的状态", range: "00:12:03 – 00:13:18", tag: "待判断", tone: "gray" },
    { id: 2, title: "2. 最近的创作灵感", range: "00:28:40 – 00:30:11", tag: "待生成", tone: "blue" },
    { id: 3, title: "3. 第一次上台的经历", range: "00:42:10 – 00:43:25", tag: "字幕待修改", tone: "orange", active: true },
    { id: 4, title: "4. 关于粉丝的支持", range: "00:55:02 – 00:56:20", tag: "待发布确认", tone: "purple" },
    { id: 5, title: "5. 接下来的直播计划", range: "01:08:17 – 01:09:46", tag: "待判断", tone: "gray" },
  ],
  subtitles: [
    { i: 1, s: "00:03.2", e: "00:05.8", t: "那是我第一次站上舞台。" },
    { i: 2, s: "00:06.1", e: "00:08.9", t: "其实上台之前特别紧张。" },
    { i: 3, s: "00:09.4", e: "00:12.7", t: "后来发现大家都在鼓励我。" },
    { i: 4, s: "00:13.6", e: "00:16.8", t: "看到弹幕里有很多熟悉的名字。" },
    { i: 5, s: "00:17.1", e: "00:20.3", t: "那一刻我觉得特别幸福。" },
    { i: 6, s: "00:21.0", e: "00:24.5", t: "也更有动力继续做下去。" },
  ],
  samples: [
    {
      title: "第一次上台的经历", date: "09月29日 20:15", dur: "12:34",
      tag: "内容保留", tone: "green", reason: "主题完整",
      desc: "讲述第一次参加线下活动的全过程，从准备、上台到结束的完整经历，主题清晰，有发展和落点。",
    },
    {
      title: "旅行中意外的小插曲", date: "09月29日 14:36", dur: "08:21",
      tag: "人工补漏", tone: "orange", reason: "开头被自动判断遗漏",
      desc: "开头一段旅行背景的自述被自动判断遗漏，实际为重要铺垫内容，与后续事件相关，建议保留。",
    },
    {
      title: "重复的直播开场", date: "09月28日 23:09", dur: "05:17",
      tag: "内容丢弃", tone: "red", reason: "重复信息",
      desc: "与近期多次直播的开场内容高度重复，主要为固定问候和设备调试，不具有剪辑价值。",
    },
    {
      title: "字幕生成失败", date: "09月28日 18:22", dur: "03:45",
      tag: "技术记录", tone: "gray", reason: "不计入内容负样本",
      desc: "由于语音识别异常导致字幕生成失败，非内容问题。该样本仅作技术记录，不计入内容负样本。",
    },
  ],
};

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content;
}

function thumb(dur, extra = "") {
  return `<div class="thumb ${extra}">
    <svg viewBox="0 0 160 90" width="100%" height="100%" aria-hidden="true">
      <rect width="160" height="90" fill="#c9d3e4"/>
      <rect x="20" y="30" width="120" height="50" rx="8" fill="#b7c4da"/>
      <circle cx="80" cy="28" r="14" fill="#a8b8d2"/>
    </svg>
    <span class="dur">${dur}</span>
    ${extra}
  </div>`;
}

function liveThumb(hasLive) {
  return `<div class="thumb">
    <svg viewBox="0 0 160 90" width="100%" height="100%" aria-hidden="true">
      <rect width="160" height="90" fill="#c9d3e4"/>
      <rect x="20" y="30" width="120" height="50" rx="8" fill="#b7c4da"/>
      <circle cx="80" cy="28" r="14" fill="#a8b8d2"/>
    </svg>
    ${hasLive ? '<span class="live-tag">● 直播中</span>' : ""}
  </div>`;
}

/* ---------- 发布队列 ---------- */
function renderPublish() {
  const selected = DEMO.recordings.find((r) => r.selected) || DEMO.recordings[0];
  $main.innerHTML = `
    <div class="page-head">
      <div class="page-title">
        <h1>发布队列</h1>
        <div class="sub">成片生成与允许发布是两个步骤</div>
      </div>
    </div>
    <div class="tabs" id="pub-tabs">
      <button class="tab active" data-f="all">全部</button>
      <button class="tab" data-f="wait">待确认</button>
      <button class="tab" data-f="doing">处理中</button>
      <button class="tab" data-f="ok">已发布</button>
      <button class="tab" data-f="fail">失败与需核对</button>
    </div>
    <div class="split">
      <div>
        <div class="card table-wrap">
          <table class="list">
            <thead>
              <tr><th style="width:38%">视频信息</th><th>状态</th><th style="width:22%">最新进度</th><th>操作</th></tr>
            </thead>
            <tbody>
              ${DEMO.recordings.map((r) => `
                <tr class="row-hover ${r.selected ? "selected" : ""}" data-id="${r.id}">
                  <td>
                    <div class="media-cell">
                      ${thumb(r.dur)}
                      <div class="media-meta">
                        <h3>${r.title}</h3>
                        <div class="time">${r.time}</div>
                        <div class="crumb"><a href="#/streamer">${r.crumb.split(" › ")[0]}</a> › ${r.crumb.split(" › ")[1]}</div>
                      </div>
                    </div>
                  </td>
                  <td><div class="status-text ${r.tone}">${r.status}</div></td>
                  <td>
                    <div class="num">${r.stage}</div>
                    ${r.progress ? `<div class="progress"><span style="width:${Math.round(r.progress * 100)}%"></span></div>` : ""}
                  </td>
                  <td><a href="#/library">${r.action || "查看详情"}</a></td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div class="page-foot">
          <div>已选择 1 / 共 5 个视频　|　共 5 条</div>
          <div class="pager">
            <button>‹</button><button class="on">1</button><button>›</button>
          </div>
        </div>
      </div>

      <div class="card detail-card">
        <div class="detail-section">
          <div class="thumb wide">
            <svg viewBox="0 0 320 180" width="100%" height="100%"><rect width="320" height="180" fill="#c9d3e4"/><circle cx="160" cy="70" r="36" fill="#a8b8d2"/><rect x="60" y="100" width="200" height="50" rx="12" fill="#b7c4da"/></svg>
            <span class="dur">02:05:12</span>
          </div>
          <div class="detail-title mt-12">直播里的小插曲</div>
          <div class="detail-sub">09月30日 14:08</div>
          <div class="tag-row">
            <span class="pill pill-gray">阿橙</span>
            <span class="pill pill-gray">聊天直播</span>
          </div>
        </div>
        <div class="detail-section">
          <div class="action-row">
            <div class="ico">▶</div>
            <div>
              <div style="font-weight:600">来源录制</div>
              <a href="#/library">查看录制源文件</a>
            </div>
          </div>
          <div class="action-row">
            <div class="ico">☑</div>
            <div>
              <div style="font-weight:600">投稿结果</div>
              <div class="status-text orange">未确认</div>
            </div>
          </div>
          <div class="action-row">
            <div class="ico">☁</div>
            <div>
              <div style="font-weight:600">媒体上传</div>
              <div class="status-text green">已完成</div>
            </div>
          </div>
          <div class="info-note mt-12">
            <span>ℹ</span>
            <span>请先核对B站投稿结果，视频无需重新上传。</span>
          </div>
        </div>
        <div class="detail-section">
          <button class="btn btn-muted btn-block mb-12">↻ 重试</button>
          <button class="btn btn-secondary btn-block">← 返回片段</button>
        </div>
      </div>
    </div>`;

  $main.querySelectorAll("#pub-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $main.querySelectorAll("#pub-tabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
    });
  });
  $main.querySelectorAll("tbody tr").forEach((row) => {
    row.addEventListener("click", () => {
      $main.querySelectorAll("tbody tr").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
    });
  });
}

/* ---------- 直播间 ---------- */
function renderLive() {
  const sel = DEMO.lives.find((l) => l.selected) || DEMO.lives[0];
  $main.innerHTML = `
    <div class="page-head">
      <div class="page-title">
        <h1>直播间</h1>
        <div class="sub">实时监控直播状态与录制任务</div>
      </div>
      <div class="head-actions">
        <button class="btn btn-primary">添加直播间</button>
      </div>
    </div>
    <div class="split">
      <div>
        <div class="card table-wrap">
          <table class="list">
            <thead>
              <tr><th style="width:36%">主播/直播间</th><th>直播状态</th><th>录制状态</th><th>已录时长</th><th>操作</th></tr>
            </thead>
            <tbody>
              ${DEMO.lives.map((l) => `
                <tr class="row-hover ${l.selected ? "selected" : ""}">
                  <td>
                    <div class="media-cell">
                      ${liveThumb(l.live === "直播中")}
                      <div class="media-meta">
                        <h3>${l.name}</h3>
                        <div class="time">${l.topic}</div>
                        <div class="crumb"><a href="#/streamer">${l.crumb.split(" › ")[0]}</a> › ${l.crumb.split(" › ")[1]}</div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span class="dot ${l.liveTone === "red" ? "red" : l.liveTone === "green" ? "green" : "gray"}"></span>
                    <span class="status-text ${l.liveTone}">${l.live}</span>
                  </td>
                  <td><span class="pill pill-${l.recTone === "blue" ? "blue" : "gray"}">${l.rec}</span></td>
                  <td class="num">${l.dur}</td>
                  <td><a href="#/library">查看</a></td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div class="info-note mt-16">
          <span>ℹ</span>
          <span>关闭网页不影响后台录制，录制任务将继续在后台进行。</span>
        </div>
      </div>

      <div class="card detail-card">
        <div class="detail-section">
          ${liveThumb(true)}
          <div class="detail-title mt-12">${sel.name}　·　${sel.topic}</div>
          <dl class="kv">
            <dt>当前文件</dt><dd>FLV · 弹幕同步保存</dd>
            <dt>已写入大小</dt><dd>3.2GB</dd>
            <dt>已录时长</dt><dd>01:26:18</dd>
            <dt>当前时间</dt><dd>2026-09-30 15:52</dd>
          </dl>
        </div>
        <div class="detail-section">
          <div class="action-row"><div class="ico">▶</div><div style="flex:1;font-weight:600">查看该主播录播</div><div>›</div></div>
          <div class="action-row"><div class="ico">👤</div><div style="flex:1;font-weight:600">主播档案</div><div>›</div></div>
        </div>
        <div class="detail-section">
          <h4>最近事件</h4>
          <div class="card table-wrap">
            <table class="list">
              <thead><tr><th>时间</th><th>事件</th></tr></thead>
              <tbody>
                <tr><td class="num">14:26</td><td>开始录制</td></tr>
                <tr><td class="num">14:26</td><td>弹幕连接成功</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;
}

/* ---------- 录播回收 ---------- */
function renderRecycle() {
  const sel = DEMO.recycle.find((r) => r.selected) || DEMO.recycle[0];
  $main.innerHTML = `
    <div class="page-head">
      <div class="page-title">
        <h1>录播回收</h1>
        <div class="sub">源录播按期限处理，成片单独保护。</div>
        <div class="sub-2">第5天提醒 · 第7天到期回收 · 移入Windows回收站。</div>
      </div>
      <div class="muted num">当前时间 2026-09-30 15:52（北京时间）</div>
    </div>
    <div class="tabs" id="rec-tabs">
      <button class="tab" data-f="soon">即将到期</button>
      <button class="tab active" data-f="due">已到期</button>
      <button class="tab" data-f="log">回收记录</button>
    </div>
    <div class="split-wide">
      <div class="card table-wrap">
        <table class="list">
          <thead>
            <tr><th style="width:34%">场次</th><th>到期时间</th><th>审核情况</th><th>回收状态</th></tr>
          </thead>
          <tbody>
            ${DEMO.recycle.map((r) => `
              <tr class="row-hover ${r.selected ? "selected" : ""}">
                <td style="font-weight:600">${r.session}</td>
                <td class="num">${r.due}</td>
                <td><div class="status-text ${r.reviewTone}">${r.review}</div></td>
                <td>
                  <div class="status-text ${r.trashTone}">${r.trash}</div>
                  ${r.trashTone === "red" ? '　<a href="#/recycle">查看原因</a>' : ""}
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>

      <div class="card detail-card">
        <div class="detail-section">
          <div class="detail-title">阿橙 09月23日夜间杂谈</div>
          <div class="detail-sub num">到期时间：2026-09-30 14:00</div>
          <div class="expired-banner">
            <span>⚠</span>
            <span>已到期，按既定规则回收，不等待剩余复核。</span>
          </div>
        </div>
        <div class="detail-section">
          <h4>回收状态</h4>
          <div class="blue-note">
            <strong>回收任务已排队，尚未执行。</strong><br/>
            系统将按规则处理，移入 Windows 回收站。
          </div>
        </div>
        <div class="detail-section">
          <h4>回收范围</h4>
          <div class="muted mb-8">将回收的内容（源录播相关）</div>
          <div class="file-item mb-8">
            <div>📄</div>
            <div><div class="name">源视频 MP4</div><div class="desc">本次直播的原始录制视频文件</div></div>
          </div>
          <div class="file-item mb-8">
            <div>📄</div>
            <div><div class="name">弹幕 XML</div><div class="desc">对应的弹幕文件</div></div>
          </div>
          <div class="file-item mb-12">
            <div>📄</div>
            <div><div class="name">关联候选与任务文件</div><div class="desc">与该场次相关的候选片段、任务等中间文件</div></div>
          </div>
          <div class="muted mb-8">保留的内容（不回收）</div>
          <div class="file-item mb-8">
            <div>📁</div>
            <div><div class="name">最终成片及投稿资料</div><div class="desc">已导出的成片、封面、文案等投稿所需资料</div></div>
          </div>
          <div class="file-item">
            <div>📁</div>
            <div><div class="name">独立审核记录</div><div class="desc">该场次的审核结果与历史记录将单独保留</div></div>
          </div>
          <div class="muted mt-12">移入回收站不代表磁盘空间已释放。</div>
        </div>
      </div>
    </div>`;
  $main.querySelectorAll("#rec-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $main.querySelectorAll("#rec-tabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
    });
  });
}

/* ---------- 录播库工作台 ---------- */
function renderLibrary() {
  const bars = Array.from({ length: 48 }, (_, i) => {
    const h = 12 + Math.round(Math.abs(Math.sin(i * 0.55)) * 50) + (i % 7);
    const hl = (i >= 18 && i <= 22) || (i >= 30 && i <= 33);
    return `<b class="${hl ? "hl" : ""}" style="height:${h}px"></b>`;
  }).join("");

  $main.innerHTML = `
    <div class="page-head">
      <div>
        <div class="crumb">
          <a href="#/library">录播库</a><span class="sep">/</span>
          <a href="#/streamer">阿橙</a><span class="sep">/</span>
          <strong>09月29日 夜间杂谈</strong>
        </div>
      </div>
      <div class="head-actions">
        <button class="btn btn-muted">整场复核</button>
        <span class="muted">ⓘ 未完成复核</span>
      </div>
    </div>

    <div class="workbench">
      <div>
        <div class="tabs" id="src-tabs">
          <button class="tab active">源录播</button>
          <button class="tab">当前成片</button>
        </div>
        <div class="player">
          <svg class="poster" viewBox="0 0 640 400"><rect width="640" height="400" fill="#2a3550"/><circle cx="320" cy="160" r="70" fill="#8fa0bf"/><rect x="180" y="220" width="280" height="90" rx="20" fill="#7d90b0"/></svg>
          <div class="bar">
            <div class="ctrls">
              <button class="play-btn">▶</button>
              <div class="times"><span>00:42:38</span><span>/</span><span>02:16:00</span></div>
              <div class="track"><span></span><i></i></div>
              <button class="play-btn">🔊</button>
              <button class="play-btn">⛶</button>
            </div>
          </div>
        </div>

        <div class="density">
          <div class="density-head">
            <div>弹幕密度（源录播）</div>
            <div class="marks"><span>00:42:10</span><span>00:43:25</span></div>
          </div>
          <div class="bars">${bars}</div>
        </div>

        <div class="range-row">
          <label>源入点</label>
          <div class="time-box"><span>00:42:10</span><span class="clock">🕐</span></div>
          <label>源出点</label>
          <div class="time-box"><span>00:43:25</span><span class="clock">🕐</span></div>
          <button class="btn btn-secondary btn-sm">⛶ 补全片段</button>
        </div>
      </div>

      <div>
        <div class="tabs" id="right-tabs">
          <button class="tab">内容</button>
          <button class="tab active">字幕</button>
        </div>
        <div class="row mb-12">
          <h2 style="font-size:22px;font-weight:700">第一次上台的经历</h2>
          <span class="pill pill-orange">草稿未保存</span>
          <div class="spacer"></div>
          <span class="muted">时间相对片段起点 ⓘ</span>
        </div>

        <div class="card table-wrap subtitle-table">
          <table class="list">
            <thead>
              <tr><th>#</th><th>开始时间</th><th>结束时间</th><th>字幕内容</th></tr>
            </thead>
            <tbody>
              ${DEMO.subtitles.map((s) => `
                <tr>
                  <td>${s.i}</td>
                  <td><input class="time" value="${s.s}" /></td>
                  <td><input class="time" value="${s.e}" /></td>
                  <td><input value="${s.t}" /></td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>

        <div class="right mt-16">
          <button class="btn btn-primary" id="btn-save-subs">保存字幕</button>
        </div>
        <div class="right muted mt-8">ⓘ 保存后需要重新生成成片</div>

        <div class="row mt-16">
          <div class="spacer"></div>
          <button class="btn btn-muted" id="btn-reburn">重新生成成片</button>
          <button class="btn btn-primary" id="btn-publish">允许发布</button>
        </div>
        <div class="right muted mt-8">ⓘ 先保存当前字幕</div>
      </div>
    </div>

    <div class="seg-strip">
      <div class="seg-strip-head">片段候选 <span>（共 8 个）</span></div>
      <div class="seg-row">
        ${DEMO.segments.map((s) => `
          <div class="seg-card ${s.active ? "active" : ""}">
            <div class="t">${s.title}</div>
            <div class="r">${s.range}</div>
            <span class="pill pill-${s.tone}">${s.tag}</span>
          </div>`).join("")}
      </div>
    </div>

    <div class="page-foot">
      <div class="info-note" style="background:transparent;padding:0"><span>ℹ</span><span>可在内容页查看 AI 判断与质量</span></div>
    </div>`;

  const toast = (msg) => {
    const n = document.createElement("div");
    n.textContent = msg;
    n.style.cssText = "position:fixed;right:24px;bottom:24px;background:#111827;color:#fff;padding:12px 16px;border-radius:8px;font-size:13px;z-index:99;opacity:0;transition:opacity .2s";
    document.body.appendChild(n);
    requestAnimationFrame(() => { n.style.opacity = "1"; });
    setTimeout(() => {
      n.style.opacity = "0";
      setTimeout(() => n.remove(), 220);
    }, 2200);
  };

  const api = async (path, body) => {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : res.statusText);
    return data;
  };

  const collectLines = () =>
    [...$main.querySelectorAll(".subtitle-table tbody tr")].map((tr, i) => {
      const inputs = tr.querySelectorAll("input");
      return {
        start: parseFloat(inputs[0].value.replace(":", "")) || 0,
        end: parseFloat(inputs[1].value.replace(":", "")) || 0,
        text: inputs[2].value,
      };
    });

  const SEGMENT_ID = "demo-seg-第一次上台";
  const SOURCE_REL = "demo/session.mp4";

  $main.querySelector("#btn-save-subs")?.addEventListener("click", async () => {
    try {
      const out = await api("/api/subtitles/save", {
        segment_id: SEGMENT_ID,
        duration: 24.5,
        revision: 1,
        lines: collectLines(),
      });
      toast(`字幕已保存 revision=${out.revision}，需重新生成成片`);
    } catch (e) {
      toast("保存失败：" + e.message);
    }
  });

  $main.querySelector("#btn-reburn")?.addEventListener("click", async () => {
    try {
      const out = await api("/api/actions", {
        action: "reburn",
        target: SEGMENT_ID,
        revision: 1,
        payload: {
          source_relpath: SOURCE_REL,
          source_start: 2530,
          source_end: 2605,
        },
      });
      toast(`已提交 reburn 任务 ${out.job_id}`);
    } catch (e) {
      toast("提交失败：" + e.message);
    }
  });

  $main.querySelector("#btn-publish")?.addEventListener("click", async () => {
    try {
      const out = await api("/api/actions", {
        action: "approve_publish",
        target: SEGMENT_ID,
        revision: 1,
        payload: {},
      });
      toast(`已提交 approve_publish 任务 ${out.job_id}`);
    } catch (e) {
      toast("提交失败：" + e.message);
    }
  });

  $main.querySelectorAll("#src-tabs .tab, #right-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      tab.parentElement.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
    });
  });
}

/* ---------- 主播档案 ---------- */
function renderStreamer() {
  $main.innerHTML = `
    <div class="page-head">
      <div class="page-title"><h1>主播档案</h1></div>
      <div class="head-actions"><a href="#/settings">✎ 编辑资料与默认样式</a></div>
    </div>
    <div class="card card-pad mb-16">
      <div class="profile-head" style="padding:0">
        <div class="avatar lg">
          <svg viewBox="0 0 72 72" width="72" height="72"><circle cx="36" cy="36" r="36" fill="#e8c4b8"/><circle cx="36" cy="28" r="14" fill="#f0d2c8"/><circle cx="48" cy="18" r="6" fill="#f7a07a"/></svg>
        </div>
        <div>
          <h2>阿橙</h2>
          <div class="muted">房间号 102468</div>
        </div>
        <div class="spacer"></div>
        <select class="btn btn-secondary"><option>切换主播</option><option>林间</option><option>小鱼</option><option>鹿眠</option></select>
      </div>
    </div>
    <div class="tabs" id="prof-tabs">
      <button class="tab">基本资料</button>
      <button class="tab active">审核经验</button>
    </div>

    <div class="split-wide">
      <div>
        <h2 style="font-size:22px;font-weight:700;margin-bottom:6px">审核经验</h2>
        <div class="muted mb-12">有效样本 6 条 · 正面 <span class="status-text blue">4</span> 条 · 负面 <span class="status-text red">2</span> 条</div>
        ${DEMO.samples.map((s) => `
          <div class="sample">
            <div class="thumb">
              <svg viewBox="0 0 160 96" width="100%" height="100%"><rect width="160" height="96" fill="#c9d3e4"/><circle cx="80" cy="36" r="18" fill="#a8b8d2"/><rect x="30" y="55" width="100" height="30" rx="8" fill="#b7c4da"/></svg>
              <span class="dur">${s.dur}</span>
            </div>
            <div style="flex:1">
              <div class="row">
                <div style="flex:1">
                  <h4>${s.title}</h4>
                  <div class="date">${s.date}</div>
                </div>
                <a href="#/library">查看来源 ↗</a>
              </div>
              <div class="tag-row">
                <span class="pill pill-${s.tone}">${s.tag}</span>
                <span class="muted">｜ ${s.reason}</span>
              </div>
              <div class="desc">${s.desc}</div>
            </div>
          </div>`).join("")}
      </div>

      <div class="card detail-card">
        <div class="detail-section">
          <h4>待应用建议</h4>
          <div class="ok-note mb-12"><span>✅</span><span>证据条件已满足</span></div>
          <div class="blue-note mb-12">
            <strong>人工样本提示：</strong> 重点检查故事开头、主题完整度和重复内容。
          </div>
          <div style="font-weight:700;margin-bottom:10px">依据 6 条人工内容样本</div>
          <ul class="bullets">
            <li>4 条正面样本：主题完整、内容有发展、具有明确落点</li>
            <li>1 条补漏样本：开头可能被自动判断遗漏</li>
            <li>1 条负面样本：重复的直播开场内容</li>
            <li>1 条技术样本：字幕生成失败（不计入内容负样本）</li>
          </ul>
          <div class="row mt-16">
            <button class="btn btn-primary" style="flex:1">应用到阿橙</button>
            <button class="btn btn-secondary" style="flex:1">查看依据</button>
          </div>
          <div class="muted mt-8">ⓘ 仅影响该主播，应用前不改变当前指导。</div>
        </div>
        <div class="detail-section">
          <h4>当前指导</h4>
          <div class="blue-note">优先保留有完整发展和落点的聊天内容。</div>
        </div>
      </div>
    </div>`;
  $main.querySelectorAll("#prof-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $main.querySelectorAll("#prof-tabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
    });
  });
}

/* ---------- 设置 ---------- */
function renderSettings() {
  $main.innerHTML = `
    <div class="page-head">
      <div class="page-title"><h1>设置与服务状态</h1></div>
    </div>
    <div class="tabs" id="set-tabs">
      <button class="tab active">服务状态</button>
      <button class="tab">处理参数</button>
      <button class="tab">路径与账号</button>
      <button class="tab">字幕默认样式</button>
    </div>

    <div class="split">
      <div>
        <div class="card card-pad mb-16">
          <h3 style="font-size:17px;margin-bottom:8px">服务状态</h3>
          <div class="svc-row">
            <div class="svc-ico">🎥</div>
            <div class="txt">
              <div class="name">录制服务 <span class="status-text green"><span class="dot green"></span>运行中</span></div>
              <div class="desc">当前 2 个直播间正在录制</div>
            </div>
          </div>
          <div class="svc-row">
            <div class="svc-ico">🖥</div>
            <div class="txt">
              <div class="name">工作台 <span class="status-text green"><span class="dot green"></span>正常</span></div>
              <div class="desc">界面服务运行正常</div>
            </div>
          </div>
          <div class="svc-row">
            <div class="svc-ico">⚙</div>
            <div class="txt">
              <div class="name">处理 Worker <span class="status-text" style="color:var(--ink-3)"><span class="dot gray"></span>空闲已退出</span></div>
              <div class="desc">有任务时按需启动</div>
            </div>
            <button class="btn btn-primary">启动处理服务</button>
          </div>
          <div class="svc-row">
            <div class="svc-ico">☁</div>
            <div class="txt">
              <div class="name">上传消费者 <span class="status-text" style="color:var(--ink-3)"><span class="dot gray"></span>未运行</span></div>
              <div class="desc">随处理服务启动，待确认成片不会自动发布</div>
            </div>
          </div>
        </div>

        <div class="card card-pad mb-16">
          <div class="row mb-12">
            <h3 style="font-size:17px;font-weight:700">当前处理配置（只读摘要）</h3>
            <div class="spacer"></div>
            <a href="#/settings">查看处理参数 ›</a>
          </div>
          <div class="summary-row"><div class="k">AI 判断</div><div class="v">MiMo（mimo-v2.5）</div></div>
          <div class="summary-row"><div class="k">语音转写</div><div class="v">faster-whisper（large-v3）</div></div>
          <div class="summary-row"><div class="k">质量门</div><div class="v">质量、完整度、置信度均 ≥ 0.80</div></div>
          <div class="summary-row"><div class="k">请求并发 · 编码并发</div><div class="v num">3 · 1</div></div>
        </div>

        <div class="card card-pad">
          <h3 style="font-size:17px;margin-bottom:12px">路径与账号摘要</h3>
          <div class="row mb-8">
            <div class="muted" style="width:120px">📁 本机媒体目录</div>
            <span class="status-text green"><span class="dot green"></span>已配置</span>
          </div>
          <div class="row mb-8" style="align-items:flex-start">
            <div class="muted" style="width:120px">👤 投稿账号</div>
            <div>
              <div class="status-text green"><span class="dot green"></span>凭据已配置，登录状态未检查</div>
              <div class="muted">已配置不代表已登录或可正常使用，请在发布时确认。</div>
            </div>
          </div>
          <div class="row" style="align-items:flex-start">
            <div class="muted" style="width:120px">🗄 索引接口</div>
            <div style="flex:1">
              <div class="status-text green"><span class="dot green"></span>可用 · 只读镜像</div>
              <div class="muted">用于检索和匹配历史素材，不会进行写入操作。</div>
            </div>
            <a href="#/settings">查看连接说明 ›</a>
          </div>
        </div>
      </div>

      <div class="card detail-card">
        <div class="detail-section">
          <h4>任务概况</h4>
          <div class="row mb-16">
            <div class="svc-ico">📄</div>
            <div style="flex:1;font-weight:600">待处理任务</div>
            <div class="big-num">2</div>
          </div>
          <div class="row mb-16">
            <div class="svc-ico">🕐</div>
            <div style="flex:1;font-weight:600">等待发布确认</div>
            <div class="big-num">3</div>
          </div>
          <div class="info-note">
            <span>ℹ</span>
            <span>网页关闭不影响后台任务，本机服务将继续在后台独立运行。</span>
          </div>
        </div>
      </div>
    </div>
    <div class="page-foot">
      <div class="spacer"></div>
      <div class="muted">本机部署 · 后台独立运行</div>
    </div>`;
  $main.querySelectorAll("#set-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $main.querySelectorAll("#set-tabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
    });
  });
}

const PAGES = {
  library: renderLibrary,
  live: renderLive,
  publish: renderPublish,
  streamer: renderStreamer,
  recycle: renderRecycle,
  settings: renderSettings,
};

function route() {
  const hash = (location.hash || "#/publish").replace(/^#\//, "");
  const page = PAGES[hash] ? hash : "publish";
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === page);
  });
  (PAGES[page] || renderPublish)();
  $main.scrollTop = 0;
}

window.addEventListener("hashchange", route);
route();
