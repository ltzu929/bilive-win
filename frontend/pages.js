/* Real-data list/detail pages; the existing backend remains authoritative. */
const uploadLabels = {
  staged: "待发布确认",
  queued: "已批准，等待上传",
  uploading: "上传中",
  uploaded: "媒体已上传",
  publishing: "投稿中",
  published: "已发布",
  failed: "失败 / 需核对",
};
const uploadTabs = [
  ["all", "当前任务"],
  ["failed", "失败与需核对"],
  ["processing", "待执行 / 发布中"],
  ["waiting", "返回成片确认"],
  ["published", "发布历史"],
];
const uploadGroups = {
  all: [
    "staged",
    "queued",
    "uploading",
    "uploaded",
    "publishing",
    "failed",
  ],
  waiting: ["staged"],
  processing: ["queued", "uploading", "uploaded", "publishing"],
  published: ["published"],
  failed: ["failed"],
};
const publicationView = { tab: "all", offset: 0, selected: null, items: [] };
const fileName = (value) =>
  String(value || "")
    .split(/[\\/]/)
    .pop();
const sourceTitle = (value) => fileName(value).replace(/\.mp4$/i, "");
const uploadTone = (r) =>
  r.stage === "published"
    ? "green"
    : ["staged", "failed"].includes(r.stage)
      ? "orange"
      : "blue";
const detailRow = (label, value) =>
  `<div class="detail-row"><span>${label}</span><strong>${htmlEscape(value ?? "—")}</strong></div>`;
const pager = (offset, total, prefix) =>
  `<div class="pagination"><span>共 ${total} 条</span><div class="pager-buttons"><button class="icon-button" id="${prefix}-prev" aria-label="上一页" ${offset === 0 ? "disabled" : ""}>${icon("chevron-left")}</button><span class="page-number">${Math.floor(offset / 20) + 1}</span><button class="icon-button" id="${prefix}-next" aria-label="下一页" ${offset + 20 >= total ? "disabled" : ""}>${icon("chevron-right")}</button></div></div>`;
function bindPager(prefix, view, total, draw) {
  document.getElementById(`${prefix}-prev`).onclick = () => {
    view.offset = Math.max(0, view.offset - 20);
    draw();
  };
  document.getElementById(`${prefix}-next`).onclick = () => {
    if (view.offset + 20 < total) view.offset += 20;
    draw();
  };
}
async function renderPublish() {
  clearTimeout(reviewTimer);
  const current = beginPage("publish");
  document.getElementById("main").innerHTML =
    pageHead("发布队列", "批准后自动执行 · 异常优先处理") +
    emptyState("正在读取完整队列…");
  try {
    const lists = await Promise.all(
      uploadGroups[publicationView.tab].map((stage) =>
        readAllPages(`/api/uploads?stage=${stage}`, current),
      ),
    );
    if (!current()) return;
    publicationView.items = lists
      .flat()
      .sort((a, b) => (a.stage === "failed" ? 0 : a.stage === "staged" ? 2 : 1) - (b.stage === "failed" ? 0 : b.stage === "staged" ? 2 : 1) || Number(b.id) - Number(a.id));
    publicationView.offset = Math.min(
      publicationView.offset,
      Math.max(0, Math.ceil(publicationView.items.length / 20) - 1) * 20,
    );
    if (!publicationView.items.some((r) => r.id === publicationView.selected))
      publicationView.selected = publicationView.items[0]?.id ?? null;
    drawPublish();
  } catch (e) {
    if (current()) pageError("发布队列", e, renderPublish);
  }
}
function drawPublish() {
  const view = publicationView,
    rows = view.items.slice(view.offset, view.offset + 20),
    row = view.items.find((r) => r.id === view.selected);
  const progress = (r) =>
    r.stage === "failed"
      ? r.remote_filename
        ? "媒体已上传 · 投稿需核对"
        : "上传失败"
      : r.stage === "staged"
        ? "审核成片"
        : uploadLabels[r.stage];
  document.getElementById("main").innerHTML =
    pageHead(
      "发布队列",
      "批准后自动执行 · 异常优先处理",
      `<button class="btn btn-secondary" id="publish-refresh">${icon("refresh")}刷新</button>`,
    ) +
    `<div class="master-detail publish-layout"><section class="master-column">${tabsHTML(uploadTabs, view.tab, "data-publish-tab")}<div class="master-scroll card table-wrap"><table class="list publish-table"><thead><tr><th>视频信息</th><th>状态</th><th>最新进度</th><th>操作</th></tr></thead><tbody>${rows.map((r) => `<tr data-publish-row="${r.id}" class="${r.id === view.selected ? "selected" : ""}"><td><div class="video-cell"><div class="thumb" ${r.segment_id ? `data-media-url="${htmlEscape(apiURL("/api/media/final/" + encodeURIComponent(r.segment_id)))}"` : ""}>${mediaPlaceholder("预览不可用")}</div><div><h3>${htmlEscape(r.title || fileName(r.video_path))}</h3><p>${htmlEscape(beijingTime(r.created_at))}</p><small>${r.source_task_id ? "已关联源录播" : "来源未关联"}</small></div></div></td><td><span class="text-${uploadTone(r)}">${htmlEscape(uploadLabels[r.stage] || r.stage)}</span></td><td><div class="stage-progress"><span>${htmlEscape(progress(r))}</span><div class="stage-track" aria-label="${htmlEscape(progress(r))}">${["媒体上传", "投稿", "发布"].map((label, i) => `<span title="${label}" class="${(i === 0 && !!r.remote_filename) || (i === 1 && !!r.bvid) || (i === 2 && r.stage === "published") ? "complete" : ""}"></span>`).join("")}</div></div></td><td><button class="text-button">${r.stage === "published" ? "查看结果" : "查看详情"}</button></td></tr>`).join("") || '<tr><td colspan="4">当前筛选没有记录。</td></tr>'}</tbody></table></div>${pager(view.offset, view.items.length, "publish")}</section><aside class="detail-panel">${row ? publishDetail(row) : emptyState("选择视频查看详情")}</aside></div>`;
  document.getElementById("publish-refresh").onclick = renderPublish;
  document.querySelectorAll("[data-publish-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        view.tab = b.dataset.publishTab;
        view.offset = 0;
        renderPublish();
      }),
  );
  document.querySelectorAll("[data-publish-row]").forEach(
    (tr) =>
      (tr.onclick = () => {
        view.selected = Number(tr.dataset.publishRow);
        drawPublish();
      }),
  );
  bindPager("publish", view, view.items.length, drawPublish);
  bindPublishDetail(row);
  restorePageScroll("publish");
  loadVideoThumbnails();
}
function publishDetail(r) {
  const link = r.source_task_id
    ? pageLink("library", {
        source_task_id: r.source_task_id,
        ...(r.segment_id ? { segment_id: r.segment_id } : {}),
      })
    : null;
  return `<div class="detail-preview" ${r.segment_id ? `data-media-url="${htmlEscape(apiURL("/api/media/final/" + encodeURIComponent(r.segment_id)))}"` : ""}>${mediaPlaceholder("当前成片预览不可用")}</div><h2>${htmlEscape(r.title || fileName(r.video_path))}</h2><p class="muted">${htmlEscape(beijingTime(r.created_at))}</p><p>${statusBadge(uploadLabels[r.stage] || r.stage, uploadTone(r))}</p>
    <section class="detail-section"><h3>${icon("player-play")}来源录制</h3>${link ? `<a href="${link}">查看录制源文件</a>` : '<p class="muted">暂无来源关联</p>'}</section>
    <section class="detail-section"><h3>${icon("file-text")}投稿结果</h3>${r.stage === "published" && r.bvid ? `<a class="text-green" target="_blank" rel="noopener" href="https://www.bilibili.com/video/${encodeURIComponent(r.bvid)}">${htmlEscape(r.bvid)} · 查看结果</a>` : `<p class="text-orange">${r.stage === "failed" ? "投稿失败 / 结果需核对" : "尚未确认"}</p>`}${r.last_error ? notice(htmlEscape(uploadErrorText(r)), "warning") : ""}<p class="muted">已尝试 ${Number(r.attempts) || 0} 次${r.next_attempt_at ? ` · 下次尝试 ${beijingTime(r.next_attempt_at)}` : r.stage === "failed" ? " · 自动重试已停止" : ""}</p></section>
    <section class="detail-section"><h3>${icon("cloud-upload")}媒体上传</h3><p class="${r.remote_filename ? "text-green" : "muted"}">${r.remote_filename ? "已完成，上传结果已保留" : "尚未完成"}</p></section>
    <details class="publish-metadata"><summary>投稿资料 · 标题 / 简介 / TAG</summary><p><strong>${htmlEscape(r.title)}</strong></p><p class="publish-description">${htmlEscape(r.description || "暂无简介")}</p><p>${htmlEscape(uploadTags(r).join(" · ") || "暂无 TAG")}</p></details>
    <p class="hint">${icon("info-circle")}${r.remote_filename && r.stage !== "published" ? "请先到 B 站创作中心核对是否已生成稿件，避免重复投稿。视频无需重新上传。" : "成片须经人工允许发布后才进入上传流程。"}</p><div class="detail-actions">${r.remote_filename && r.stage !== "published" ? '<a class="btn btn-primary btn-block" href="https://member.bilibili.com/platform/upload-manager/article" target="_blank" rel="noopener">核对 B 站投稿结果</a>' : ""}<button id="upload-retry" class="btn btn-muted btn-block" ${r.stage === "failed" && !r.remote_filename ? "" : "disabled"}>${icon("refresh")}${r.remote_filename ? "核对投稿结果后处理" : "重试上传"}</button>${link ? `<a class="btn btn-outline btn-block" href="${link}">${icon("arrow-left")}返回片段</a>` : ""}</div><p id="publish-message" role="status"></p>`;
}
function uploadTags(row) {
  try { const tags = Array.isArray(row.tags) ? row.tags : JSON.parse(row.tags || "[]"); return Array.isArray(tags) ? tags : []; } catch { return []; }
}
function uploadErrorText(row) {
  if (row.last_error === "submission returned no valid success") return "B 站未确认投稿成功。旧记录没有保存具体错误码和说明，无法还原拒绝原因；后续失败会显示 B 站返回的具体信息。";
  if (row.last_error === "submission success has no bvid") return "B 站返回成功但缺少 BV 号，请先核对创作中心投稿记录。";
  if (row.last_error === "authentication unavailable") return "B 站登录状态不可用，已暂停处理，请检查设置中的登录状态。";
  return row.last_error;
}
function bindPublishDetail(r) {
  if (!r) return;
  const b = document.getElementById("upload-retry");
  b.onclick = async () => {
    b.disabled = true;
    try {
      const out = await reviewAPI("/api/uploads/retry", {
        video_path: r.video_path,
        stage: r.stage,
        remote_filename: r.remote_filename,
      });
      if (pageKey() === "publish" && publicationView.selected === r.id)
        setMessage("publish-message", `重试已排队：${out.job_id}`);
    } catch (e) {
      if (b.isConnected) {
        setMessage("publish-message", `重试未排入：${e.message}`);
        b.disabled = false;
      }
    }
  };
}

const liveRooms = { page: 1, size: 20, selected: null, items: [], busy: false };
let roomTimer;
const roomId = (r) => r.room_info?.room_id;
const roomName = (r) =>
  r.user_info?.name || r.user_info?.uname || `房间 ${roomId(r)}`;
const recordedTime = (value) =>
  timecode(Number.isFinite(value) ? Math.floor(value) : value);
const recordingLabels = {
  stopped: "已停止",
  waiting: "等待开播",
  recording: "录制中",
  remuxing: "转封装中",
  injecting: "处理元数据",
};
async function renderNativeRooms({ background = false } = {}) {
  clearTimeout(roomTimer);
  clearTimeout(reviewTimer);
  if (
    background &&
    (liveRooms.busy || document.querySelector("dialog[open]"))
  ) {
    scheduleRoomRefresh();
    return;
  }
  const current = beginPage("live");
  const controls = document.querySelector(".room-controls");
  if (controls) liveRooms.controlsOpen = controls.open;
  if (!background) {
    document.getElementById("main").innerHTML =
      pageHead("直播间", "实时监控直播状态与录制任务") +
      emptyState("正在读取录制器房间…");
  }
  try {
    const data = await reviewAPI(
      `/api/rooms?page=${liveRooms.page}&size=${liveRooms.size}`,
    );
    if (!current()) return;
    const changed =
      JSON.stringify(liveRooms.items) !== JSON.stringify(data.items);
    liveRooms.items = data.items;
    const link = pageParams().get("room");
    if (link !== liveRooms.link) {
      liveRooms.link = link;
      if (link && data.items.some((r) => String(roomId(r)) === link))
        liveRooms.selected = Number(link);
    }
    if (!data.items.some((r) => roomId(r) === liveRooms.selected))
      liveRooms.selected = roomId(data.items[0] || {});
    if (!background || changed) drawNativeRooms();
    else pageScroll.delete("live"); // Unchanged DOM already has the latest scroll position.
    document
      .querySelector("[data-live-clock]")
      ?.replaceChildren(beijingTime(Date.now() / 1000));
    updateServiceIndicator();
    scheduleRoomRefresh();
  } catch (e) {
    if (current()) {
      if (background) {
        setMessage("room-message", `读取失败：${e.message}，稍后重试。`);
        scheduleRoomRefresh();
      } else pageError("直播间", e, renderNativeRooms);
    }
  }
}
function scheduleRoomRefresh() {
  clearTimeout(roomTimer);
  const epoch = pageEpoch;
  roomTimer = setTimeout(() => {
    if (pageKey() === "live" && pageEpoch === epoch)
      renderNativeRooms({ background: true });
  }, 5000);
}
function roomCoverHTML(r) {
  const cover = r.room_info?.cover;
  if (!cover) return mediaPlaceholder("暂无直播封面");
  const source = apiURL(`/api/rooms/${roomId(r)}/cover?v=${encodeURIComponent(cover)}`);
  return `${mediaPlaceholder("封面加载中")}<img class="live-room-cover" src="${htmlEscape(source)}" alt="${htmlEscape(roomName(r))}的直播封面" loading="lazy">`;
}
function bindRoomCovers() {
  document.querySelectorAll(".live-room-cover").forEach((img) => {
    const show = () => {
      img.previousElementSibling?.remove();
      img.classList.add("loaded");
    };
    const fail = () => {
      img.previousElementSibling?.querySelector("span")?.replaceChildren("封面暂不可用");
      img.remove();
    };
    img.addEventListener("load", show, { once: true });
    img.addEventListener("error", fail, { once: true });
    if (img.complete) img.naturalWidth ? show() : fail();
  });
}
function drawNativeRooms() {
  const view = liveRooms,
    row = view.items.find((r) => roomId(r) === view.selected);
  // Row selection has no API read; capture the panes before replacing their DOM.
  if (document.getElementById("main").querySelector(".master-scroll"))
    savePageScroll("live");
  document.getElementById("main").innerHTML =
    pageHead(
      "直播间",
      `本页 ${view.items.length} 个直播间 · ${view.items.filter(r => r.room_info?.live_status === 1).length} 个直播中 · ${view.items.filter(r => r.task_status?.running_status === "recording").length} 个正在录制`,
      `<button id="rooms-refresh" class="icon-button" aria-label="刷新">${icon("refresh")}</button><button class="btn btn-primary" data-dialog="room-dialog">添加直播间</button>`,
    ) +
    `<div class="master-detail live-layout"><section class="master-column"><div class="master-scroll card table-wrap"><table class="list live-table"><colgroup><col><col class="room-state-col"><col class="record-state-col"><col class="record-duration-col"><col class="room-action-col"></colgroup><thead><tr><th>主播/直播间</th><th>直播状态</th><th>录制状态</th><th>已录时长</th><th>操作</th></tr></thead><tbody>${
      view.items
        .map((r) => {
          const info = r.room_info || {},
            s = r.task_status || {},
            live = info.live_status === 1;
          return `<tr data-room-select="${roomId(r)}" class="${roomId(r) === view.selected ? "selected" : ""}"><td><div class="video-cell"><div class="thumb">${roomCoverHTML(r)}</div><div><h3 title="${htmlEscape(roomName(r))}">${htmlEscape(roomName(r))}</h3><p title="${htmlEscape(info.title || "暂无直播标题")}">${htmlEscape(info.title || "暂无直播标题")}</p><small title="房间 ${roomId(r)}">房间 ${roomId(r)}</small></div></div></td><td><span class="live-state ${live ? "text-red" : "muted"}"><i></i>${live ? "直播中" : info.live_status == null ? "状态未知" : "未开播"}</span></td><td>${statusBadge(!s.recorder_enabled ? "自动录制关闭" : recordingLabels[s.running_status] || "状态未知", s.running_status === "recording" && s.recorder_enabled ? "blue" : "muted")}</td><td class="num">${recordedTime(s.rec_elapsed)}</td><td><button class="text-button">查看</button></td></tr>`;
        })
        .join("") ||
      '<tr><td colspan="5">没有配置直播间，请添加房间。</td></tr>'
    }</tbody></table></div><div class="pagination"><span>第 ${view.page} 页</span><div class="pager-buttons"><button id="rooms-prev" class="icon-button" aria-label="上一页" ${view.page === 1 ? "disabled" : ""}>${icon("chevron-left")}</button><button id="rooms-next" class="icon-button" aria-label="下一页" ${view.items.length < view.size ? "disabled" : ""}>${icon("chevron-right")}</button></div></div><p class="hint bottom-hint">${icon("info-circle")}关闭网页不影响后台录制，录制任务将继续在后台进行。</p></section><aside class="detail-panel">${row ? liveDetail(row) : emptyState("选择直播间查看详情")}<p id="room-message" role="status"></p></aside></div>` +
    dialogHTML(
      "room-dialog",
      "添加直播间",
      `<label class="field">房间号<input id="room-add-id" type="number" min="1" step="1" aria-label="新增房间号" placeholder="输入 B 站直播间号"></label><p id="room-dialog-message" role="status"></p><div class="dialog-actions"><button class="btn btn-secondary" data-close-dialog>取消</button><button id="room-add" class="btn btn-primary">添加直播间</button></div>`,
    );
  restorePageScroll("live");
  bindRoomCovers();
  bindDialogs();
  document.querySelector(".room-controls")?.addEventListener("toggle", (e) => {
    view.controlsOpen = e.currentTarget.open;
  });
  document.getElementById("rooms-refresh").onclick = renderNativeRooms;
  document.getElementById("rooms-prev").onclick = () => {
    view.page--;
    renderNativeRooms();
  };
  document.getElementById("rooms-next").onclick = () => {
    view.page++;
    renderNativeRooms();
  };
  document.querySelectorAll("[data-room-select]").forEach(
    (tr) =>
      (tr.onclick = () => {
        view.selected = Number(tr.dataset.roomSelect);
        drawNativeRooms();
      }),
  );
  const control = async (id, action, b) => {
    if (liveRooms.busy) return;
    liveRooms.busy = true;
    setMessage(
      action === "add" ? "room-dialog-message" : "room-message",
      "正在提交…",
    );
    clearTimeout(roomTimer);
    ++pageEpoch;
    if (b) b.disabled = true;
    try {
      if (!Number.isInteger(Number(id)) || Number(id) <= 0)
        throw new Error("请输入有效的数字房间号");
      await reviewAPI(`/api/rooms/${id}/${action}`, {});
      if (pageKey() !== "live") return;
      await renderNativeRooms();
      setMessage("room-message", "操作已提交，状态以录制器返回结果为准。");
    } catch (e) {
      if (b?.isConnected) b.disabled = false;
      setMessage(
        action === "add" ? "room-dialog-message" : "room-message",
        `操作失败：${e.message}`,
      );
    } finally {
      liveRooms.busy = false;
      if (pageKey() === "live") scheduleRoomRefresh();
    }
  };
  document.getElementById("room-add").onclick = (e) =>
    control(
      document.getElementById("room-add-id").value,
      "add",
      e.currentTarget,
    );
  document.querySelectorAll("[data-room-action]").forEach(
    (b) =>
      (b.onclick = () => {
        if (
          b.dataset.roomAction === "remove" &&
          !confirm("移除这个房间的录制配置？历史录播文件会保留。")
        )
          return;
        control(view.selected, b.dataset.roomAction, b);
      }),
  );
}
function liveDetail(r) {
  const s = r.task_status || {},
    id = roomId(r);
  return `<div class="detail-preview">${roomCoverHTML(r)}${r.room_info?.live_status === 1 ? '<span class="live-overlay">直播中</span>' : ""}</div><h2>${htmlEscape(roomName(r))} · ${htmlEscape(r.room_info?.title || "暂无标题")}</h2><div class="detail-summary">${detailRow("当前文件", "暂无文件名数据")}${detailRow("已写入大小", bytesLabel(s.rec_total))}${detailRow("已录时长", recordedTime(s.rec_elapsed))}${detailRow("已收弹幕", s.danmu_total)}<div class="detail-row"><span>当前时间</span><strong data-live-clock>${htmlEscape(beijingTime(Date.now() / 1000))}</strong></div></div><section class="detail-section"><a class="detail-link" href="${pageLink("library", { room: id })}">${icon("player-play")}查看该主播录播${icon("chevron-right")}</a><a class="detail-link" href="${pageLink("streamer", { room: id })}">${icon("user")}主播档案${icon("chevron-right")}</a></section><section class="detail-section"><h3>最近事件</h3><p class="muted">暂无事件数据</p></section><details class="room-controls" ${liveRooms.controlsOpen ? "open" : ""}><summary>录制控制</summary><div class="action-row">${[
    ["monitor_start", "启动监测"],
    ["monitor_stop", "停止监测"],
    ["enable", "启用录制"],
    ["disable", "暂停录制"],
    ["cut", "分割当前录制"],
    ["remove", "移除房间配置"],
  ]
    .map(
      ([action, label]) =>
        `<button class="btn btn-secondary btn-sm" data-room-action="${action}">${label}</button>`,
    )
    .join("")}</div></details>`;
}

const recycleView = { tab: "expired", offset: 0, selected: null, items: [] };
const trashLabels = {
  pending: "已排队 · 等待处理",
  processing: "正在回收",
  done: "回收完成",
  failed: "部分失败",
  blocked: "回收受阻",
};
const reviewLabels = {
  unprocessed: "未复核",
  review_complete: "已复核",
  trash_pending: "复核记录已保存 / 回收已排队",
};
async function renderSourceRecycle() {
  clearTimeout(reviewTimer);
  const current = beginPage("recycle");
  document.getElementById("main").innerHTML =
    pageHead("录播回收") + emptyState("正在读取完整回收记录…");
  try {
    const items = await readAllPages("/api/lifecycle", current);
    if (!current()) return;
    recycleView.items = items;
    drawRecycle();
  } catch (e) {
    if (current()) pageError("录播回收", e, renderSourceRecycle);
  }
}
function drawRecycle() {
  const view = recycleView,
    now = Date.now() / 1000;
  const items = view.items.filter((r) =>
    view.tab === "history"
      ? !!r.trash_status
      : r.trash_status !== "done" &&
        (view.tab === "expired"
          ? r.deadline != null && now >= r.deadline
          : r.retention_started_at != null &&
            now >= r.retention_started_at + 5 * 86400 &&
            now < r.deadline),
  );
  view.offset = Math.min(
    view.offset,
    Math.max(0, Math.ceil(items.length / 20) - 1) * 20,
  );
  if (!items.some((r) => r.task_id === view.selected))
    view.selected = items[0]?.task_id;
  const row = items.find((r) => r.task_id === view.selected);
  document.getElementById("main").innerHTML =
    pageHead(
      "录播回收",
      "源录播按期限处理，成片单独保护。",
      `<span class="muted">当前时间 ${beijingTime(now)}（北京时间）</span><button id="recycle-refresh" class="icon-button" aria-label="刷新">${icon("refresh")}</button>`,
    ) +
    '<p class="retention-subtitle">第5天提醒 · 第7天到期回收 · 移入 Windows 回收站。</p>' +
    tabsHTML(
      [
        ["upcoming", "即将到期"],
        ["expired", "已到期"],
        ["history", "回收记录"],
      ],
      view.tab,
      "data-recycle-tab",
    ) +
    `<div class="master-detail recycle-layout"><section class="master-column"><div class="master-scroll card table-wrap"><table class="list"><thead><tr><th>场次</th><th>到期时间</th><th>审核情况</th><th>回收状态</th></tr></thead><tbody>${
      items
        .slice(view.offset, view.offset + 20)
        .map(
          (r) =>
            `<tr data-lifecycle-row="${htmlEscape(r.task_id)}" class="${view.selected === r.task_id ? "selected" : ""}"><td><button class="row-title text-button">${htmlEscape(sourceTitle(r.source_relpath))}</button></td><td>${beijingTime(r.deadline)}</td><td><span class="${r.review_result ? "text-green" : "text-orange"}">${htmlEscape(r.review_result ? "已复核" : reviewLabels[r.review_state] || r.review_state)}</span></td><td><span class="${r.trash_status === "done" ? "text-green" : ["failed", "blocked"].includes(r.trash_status) ? "text-red" : "text-blue"}">${htmlEscape(trashLabels[r.trash_status] || "未排队")}</span></td></tr>`,
        )
        .join("") || '<tr><td colspan="4">当前筛选没有记录。</td></tr>'
    }</tbody></table></div>${pager(view.offset, items.length, "recycle")}</section><aside class="detail-panel card recycle-detail">${row ? recycleDetail(row, now) : emptyState("选择场次查看回收范围")}</aside></div>`;
  restorePageScroll("recycle");
  document.getElementById("recycle-refresh").onclick = renderSourceRecycle;
  document.querySelectorAll("[data-recycle-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        view.tab = b.dataset.recycleTab;
        view.offset = 0;
        drawRecycle();
      }),
  );
  document.querySelectorAll("[data-lifecycle-row]").forEach(
    (tr) =>
      (tr.onclick = () => {
        view.selected = tr.dataset.lifecycleRow;
        drawRecycle();
      }),
  );
  bindPager("recycle", view, items.length, drawRecycle);
  const b = document.getElementById("recycle-retry");
  if (b)
    b.onclick = async () => {
      b.disabled = true;
      try {
        const out = await reviewAPI(
          `/api/recordings/${row.task_id}/recycle`,
          {},
        );
        if (b.isConnected)
          setMessage(
            "recycle-message",
            out.queued ? `回收已排队：${out.job_id}` : out.reason,
          );
      } catch (e) {
        if (b.isConnected) {
          setMessage("recycle-message", `回收未排入：${e.message}`);
          b.disabled = false;
        }
      }
    };
}
function recycleDetail(r, now) {
  const item = (name, desc, ico = "file-text") =>
    `<div class="file-item">${icon(ico)}<div><strong>${name}</strong><p>${desc}</p></div></div>`;
  return `<h2>${htmlEscape(sourceTitle(r.source_relpath))}</h2><p class="muted">到期时间：${beijingTime(r.deadline)}</p>${r.deadline != null && now >= r.deadline ? notice("已到期，按既定规则回收，不等待剩余复核。", "warning") : notice("源录播尚未到期，回收仍需满足整场审核规则。")}<section class="detail-section"><h3>回收状态</h3>${notice(`${htmlEscape(trashLabels[r.trash_status] || "尚未排队")}<p class="muted">${htmlEscape(r.trash_error || "后台按规则执行源包检查与移动。")}</p>`, ["failed", "blocked"].includes(r.trash_status) ? "warning" : "info")}<p class="muted">审核：${htmlEscape(r.review_result ? "已复核" : reviewLabels[r.review_state] || r.review_state)}</p></section><section class="detail-section"><h3>回收范围</h3><p>将回收的内容（源录播相关）</p><div class="file-group">${item("源视频 MP4 / FLV", "该场次原始录制视频及关联文件")}${item("弹幕 XML", "对应弹幕文件")}${item("关联候选与任务文件", "关联候选、原始裁剪和任务中间文件")}</div><p>保留的内容（不回收）</p><div class="file-group">${item("最终成片及投稿资料", "成片历史、独立字幕与投稿所需资料", "folder")}${item("独立审核记录", "该场次的审核结果与历史记录", "folder")}</div></section><details><summary>冻结清单及逐项结果</summary><pre>${htmlEscape(JSON.stringify({ files: r.frozen_manifest, results: r.trash_results }, null, 2))}</pre></details><p class="hint">${icon("info-circle")}移入回收站不代表磁盘空间已释放。</p><button id="recycle-retry" class="btn btn-secondary" ${["done", "processing", "pending"].includes(r.trash_status) ? "disabled" : ""}>重新排回收</button><p id="recycle-message" role="status"></p>`;
}

const settingsView = {
  tab: "processing",
  services: null,
  config: null,
  waiting: null,
  index: null,
  subtitleDraft: null,
  automationDraft: null,
  saving: false,
};
const settingsTabs = [
  ["processing", "自动化"],
  ["subtitles", "字幕"],
  ["publish", "发布"],
  ["paths", "存储与清理"],
  ["services", "系统"],
  ["advanced", "高级"],
];
async function renderRuntimeSettings() {
  clearTimeout(reviewTimer);
  const requestedTab = pageParams().get('tab');
  if (settingsTabs.some(([key]) => key === requestedTab)) settingsView.tab = requestedTab;
  const current = beginPage("settings");
  document.getElementById("main").innerHTML =
    pageHead("设置与服务状态") + emptyState("正在探测本机服务…");
  try {
    const [services, config, waiting, index] = await Promise.all([
      reviewAPI("/api/services"),
      reviewAPI("/api/config/summary"),
      reviewAPI("/api/uploads?stage=staged&limit=1").catch(() => null),
      reviewAPI("/api/eagle/source-recordings").catch(() => null),
    ]);
    if (!current()) return;
    Object.assign(settingsView, { services, config, waiting, index });
    drawSettings();
    updateServiceIndicator();
  } catch (e) {
    if (current()) pageError("设置与服务状态", e, renderRuntimeSettings);
  }
}
const definitionRows = (rows) =>
  `<dl class="definition-list">${rows.map(([label, value]) => `<dt>${htmlEscape(label)}</dt><dd>${htmlEscape(value ?? "—")}</dd>`).join("")}</dl>`;
const configNames = {
  auto_start: "自动启动",
  model: "模型",
  base_url: "服务地址",
  fps: "视频采样帧率",
  timeout: "请求超时（秒）",
  request_concurrency: "请求并发",
  encode_concurrency: "编码并发",
  quality: "质量",
  completeness: "完整度",
  confidence: "置信度",
  asr_model: "语音转写模型",
  asr_device: "转写设备",
  asr_compute_type: "转写计算类型",
  asr_batch: "转写批大小",
  asr_cpu_threads: "CPU 线程",
  asr_vad: "VAD",
  padding: "字幕窗口补边（秒）",
  snap_tolerance: "句边界容差（秒）",
  snap_enabled: "句边界吸附",
  light_correct: "轻量字幕校正",
  font_family: "字体",
  font_size: "字号",
  margin_v: "垂直边距",
  alignment: "对齐",
  outline: "描边宽度",
  primary_colour: "文字颜色",
  outline_colour: "描边颜色",
  ratio: "突增倍数",
  window: "窗口（秒）",
  context: "候选上下文（秒）",
  merge_gap: "合并间隔（秒）",
  top_n: "候选上限",
  lag: "弹幕偏移（秒）",
  idle_timeout: "空闲退出（秒）",
  idle_check_interval: "空闲检查间隔（秒）",
  videos_dir: "媒体目录",
  log_dir: "日志目录",
  db_path: "上传库路径",
  cookie_file: "凭据文件路径",
  config_path: "配置路径",
};
function configSection(title, data) {
  return `<section class="card card-pad"><h2>${title}<span class="readonly">只读</span></h2>${definitionRows(Object.entries(data || {}).map(([key, value]) => [configNames[key] || key, typeof value === "object" ? JSON.stringify(value) : String(value)]))}</section>`;
}
function subtitleStyleFromSettings(edit) {
  return {font_name: edit?.font_family || "Noto Sans SC", font_size: edit?.font_size ?? 20,
    margin_v: edit?.margin_v ?? 60, alignment: edit?.alignment ?? 2, outline: edit?.outline ?? 1,
    primary_colour: edit?.primary_colour || "&H00FFFFFF", outline_colour: edit?.outline_colour || "&H00000000"};
}
function assColorHex(value) {
  const match = /^&H([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value || "");
  return match ? `#${match[4]}${match[3]}${match[2]}` : "#ffffff";
}
function colorHexAss(hex, alpha = "00") {
  return `&H${alpha}${hex.slice(5,7)}${hex.slice(3,5)}${hex.slice(1,3)}`.toUpperCase();
}
function subtitleStyleSettingsHTML(edit) {
  const style = settingsView.subtitleDraft || subtitleStyleFromSettings(edit);
  return `<section class="card card-pad subtitle-settings"><div class="section-heading"><h2>字幕外观</h2><button class="btn btn-secondary btn-sm" id="subtitle-preset">使用清晰预设</button></div><p class="muted">按 1920 × 1080 画面设置。清晰预设：字号 48、底部距离 110、描边 2。</p><div class="subtitle-settings-layout"><form id="subtitle-settings-form"><div class="subtitle-settings-fields"><label class="field">字体<input id="subtitle-font" required maxlength="100" list="subtitle-font-list" value="${htmlEscape(style.font_name)}"><datalist id="subtitle-font-list"><option value="Noto Sans SC"><option value="Microsoft YaHei"><option value="SimHei"></datalist></label><label class="field">字号（像素）<input id="subtitle-font-size" type="number" min="8" max="120" step="1" required value="${style.font_size}"></label><label class="field">上下边距（像素）<input id="subtitle-margin" type="number" min="0" max="1080" step="1" required value="${style.margin_v}"></label><label class="field">位置<select id="subtitle-alignment">${[[1,"左下"],[2,"底部居中"],[3,"右下"],[4,"左侧居中"],[5,"画面居中"],[6,"右侧居中"],[7,"左上"],[8,"顶部居中"],[9,"右上"]].map(([value,label]) => `<option value="${value}" ${value === style.alignment ? "selected" : ""}>${label}</option>`).join("")}</select></label><label class="field">描边宽度（像素）<input id="subtitle-outline" type="number" min="0" max="10" step="0.5" required value="${style.outline}"></label><label class="field">文字颜色<input id="subtitle-color" type="color" value="${assColorHex(style.primary_colour)}"></label><label class="field">描边颜色<input id="subtitle-outline-color" type="color" value="${assColorHex(style.outline_colour)}"></label></div><div class="action-row"><button class="btn btn-primary" id="subtitle-settings-save" type="submit">保存字幕设置</button><button class="btn btn-secondary" id="subtitle-settings-reset" type="button">恢复已保存设置</button></div><p id="subtitle-settings-message" role="status" class="muted">${settingsView.subtitleDraft ? "有未保存的字幕设置" : "当前已保存设置"}</p></form><div class="subtitle-preview-panel"><canvas id="subtitle-style-preview" width="1920" height="1080" aria-label="字幕外观预览"></canvas><p class="hint">缩放预览 · 实际效果以生成的成片为准</p><p class="hint">底部字幕适当上移，可以避免播放控制条遮挡。</p></div></div><p class="subtitle-settings-scope">保存后用于新建片段和新分析片段，主播档案中的单独样式优先。已有片段可在录播库应用默认样式，再重新生成成片。</p></section>`;
}
function bindSubtitleSettings() {
  const form = document.getElementById("subtitle-settings-form");
  if (!form) return;
  const original = subtitleStyleFromSettings(settingsView.config.settings.edit);
  function readStyle() {
    const prior = settingsView.subtitleDraft || original;
    return {font_name: document.getElementById("subtitle-font").value,
      font_size: Number(document.getElementById("subtitle-font-size").value),
      margin_v: Number(document.getElementById("subtitle-margin").value),
      alignment: Number(document.getElementById("subtitle-alignment").value),
      outline: Number(document.getElementById("subtitle-outline").value),
      primary_colour: colorHexAss(document.getElementById("subtitle-color").value, prior.primary_colour.slice(2,4)),
      outline_colour: colorHexAss(document.getElementById("subtitle-outline-color").value, prior.outline_colour.slice(2,4))};
  }
  function preview(style) {
    const canvas = document.getElementById("subtitle-style-preview"), ctx = canvas.getContext("2d");
    const background = ctx.createLinearGradient(0,0,1920,1080);
    background.addColorStop(0,"#546f8b"); background.addColorStop(1,"#c5d7e5");
    ctx.fillStyle = background; ctx.fillRect(0,0,1920,1080);
    ctx.fillStyle = "rgba(255,255,255,.15)"; ctx.fillRect(200,160,700,600); ctx.fillRect(1020,260,700,380);
    ctx.fillStyle = "rgba(255,255,255,.65)"; ctx.font = "36px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("视频画面",960,480);
    const column = (style.alignment-1)%3, row = Math.floor((style.alignment-1)/3);
    ctx.textAlign = ["left","center","right"][column];
    ctx.textBaseline = row === 0 ? "bottom" : row === 2 ? "top" : "middle";
    ctx.font = `${Math.max(8,Math.min(120,style.font_size))}px ${JSON.stringify(style.font_name)}, sans-serif`;
    ctx.lineJoin = "round"; ctx.lineWidth = Math.max(0,Math.min(10,style.outline))*2;
    ctx.strokeStyle = assColorHex(style.outline_colour); ctx.fillStyle = assColorHex(style.primary_colour);
    ctx.globalAlpha = 1-parseInt(style.primary_colour.slice(2,4),16)/255;
    const x = [20,960,1900][column], y = row === 0 ? 1080-style.margin_v : row === 2 ? style.margin_v : 540;
    const text = "雪狐的C盘现在还剩下216";
    if (style.outline > 0) ctx.strokeText(text,x,y); ctx.fillText(text,x,y); ctx.globalAlpha = 1;
  }
  form.oninput = () => {
    const style = readStyle();
    settingsView.subtitleDraft = JSON.stringify(style) === JSON.stringify(original) ? null : style; preview(style);
    updateSettingsSaveBar();
    setMessage("subtitle-settings-message", "字幕设置未保存");
  };
  document.getElementById("subtitle-preset").onclick = () => {
    settingsView.subtitleDraft = {...readStyle(), font_size:48, margin_v:110, alignment:2, outline:2, primary_colour:"&H00FFFFFF", outline_colour:"&H00000000"};
    drawSettings();
  };
  document.getElementById("subtitle-settings-reset").onclick = () => { settingsView.subtitleDraft = null; drawSettings(); };
  form.onsubmit = event => { event.preventDefault(); saveSettingsChanges(); };
  preview(settingsView.subtitleDraft || original);
}
function settingsDirtyCount() {
  const original = subtitleStyleFromSettings(settingsView.config?.settings?.edit);
  const style = settingsView.subtitleDraft;
  return (style ? Object.keys(original).filter(key => original[key] !== style[key]).length : 0) +
    (settingsView.automationDraft == null ? 0 : 1);
}
function updateSettingsSaveBar() {
  const count = settingsDirtyCount(), bar = document.getElementById("settings-save-bar");
  if (!bar) return;
  bar.hidden = count === 0;
  setMessage("settings-dirty-count", `${count} 项未保存修改 · 保存后立即用于新任务`);
  document.getElementById("settings-save-all").disabled = settingsView.saving;
  document.querySelectorAll("#subtitle-settings-save, #analysis-automation-save, #subtitle-settings-reset").forEach(button => button.hidden = true);
}
async function saveSettingsChanges() {
  if (settingsView.saving || !settingsDirtyCount()) return;
  const view = settingsView;
  view.saving = true;
  const style = view.subtitleDraft, automation = view.automationDraft;
  document.querySelectorAll('.settings-layout input, .settings-layout select, .settings-layout button, #settings-discard').forEach(control => control.disabled = true);
  updateSettingsSaveBar();
  try {
    if (style) {
      const result = await reviewAPI("/api/config/subtitle-style", {style});
      view.config.settings.edit = {font_family: result.style.font_name, ...result.style};
      view.subtitleDraft = null;
    }
    if (automation != null) {
      const result = await reviewAPI("/api/config/analysis-automation", {auto_start: automation});
      view.config.settings.analysis.auto_start = result.auto_start;
      view.automationDraft = null;
    }
    if (pageKey() === "settings") { drawSettings(); setMessage("runtime-message", "更改已保存，立即用于新任务和新片段。已有片段与已开始任务保持当前配置。"); }
  } catch (error) {
    if (pageKey() === "settings") {
      drawSettings();
      setMessage("runtime-message", `保存未全部完成：${error.message}。未成功保存的修改仍保留；已保存的分区已生效。`);
    }
  } finally {
    view.saving = false;
    updateSettingsSaveBar();
  }
}
function drawSettings() {
  const view = settingsView,
    c = view.config,
    s = c.settings || {},
    services = view.services;
  let body;
  if (view.tab === "services")
    body = `<div class="settings-top"><section class="card card-pad"><h2>本机服务</h2><p>${[services.recorder?.state, services.workbench?.state].every(state => ["ok", "running", "idle"].includes(state)) ? "录制服务与工作台正常" : "部分服务不可用，请展开检查"} · ${services.worker?.busy ? "后台有任务正在处理" : "处理服务按需启动"}</p><details><summary>服务详情与手动控制</summary>${[
      ["recorder", "录制服务", "video", "录制任务与浏览器独立运行"],
      ["workbench", "工作台", "device-desktop", "界面服务"],
      ["worker", "处理 Worker", "settings", "有任务时按需启动"],
      ["uploader", "上传消费者", "cloud-upload", "只消费已人工允许发布的成片"],
    ]
      .map(([key, label, ico, desc]) => {
        const v = services[key] || {},
          healthy = ["ok", "running", "idle"].includes(v.state),
          names = {
            ok: "正常",
            running: "运行中",
            idle: "空闲",
            offline: "未运行",
            unavailable: "不可用",
            starting: "启动中",
            stopping: "正在停止",
            unknown: "未知",
          };
        return `<div class="svc-row"><div class="svc-ico">${icon(ico)}</div><div class="txt"><div class="name">${label}<span class="service-state ${healthy ? "text-green" : "muted"}"><i></i>${names[v.state] || htmlEscape(v.state || "未知")}</span></div><p class="desc">${desc}</p></div>${key === "worker" ? `<button id="runtime-primary" class="btn ${v.state === "offline" ? "btn-primary" : "btn-secondary"}" data-runtime-path="${v.state === "offline" ? "/api/worker/wake" : "/api/worker/stop"}" ${!["offline", "ok", "running", "idle"].includes(v.state) ? "disabled" : ""}>${v.state === "offline" ? "启动处理服务" : "停止处理服务"}</button>` : ""}</div>`;
      })
      .join(
        "",
      )}<details class="secondary-controls"><summary>其他服务控制 / 依赖诊断</summary><div class="action-row"><button class="btn btn-secondary" data-runtime-path="/api/uploader/start">启动上传消费者</button><button class="btn btn-secondary" data-runtime-path="/api/uploader/stop">停止上传消费者</button></div><p class="muted">停止会等待当前操作完成，剩余任务留在队列。</p><h3>ASR 本地依赖</h3><p>设备可见和模型缓存不等于推理成功。</p><pre>${htmlEscape(JSON.stringify({ asr: c.asr, diagnostics: c.diagnostics }, null, 2))}</pre></details></details></section><section class="card card-pad task-overview"><h2>任务概况</h2><div class="overview-row">${icon("file-text")}<span>待处理任务</span><strong class="big-num">—</strong></div><p class="muted">${services.worker?.busy ? "当前有持久任务" : "当前未检测到持久任务"} · 总数未提供</p><div class="overview-row">${icon("clock")}<span>等待发布确认</span><strong class="big-num">${view.waiting?.total ?? "—"}</strong></div><hr><p class="hint">${icon("info-circle")}网页关闭不影响后台任务，本机服务将继续在后台独立运行。</p></section></div>`;
  else if (view.tab === "processing")
    body =
      `<section class="card card-pad"><h2>录播切片方式</h2><form id="analysis-automation-form"><label><input id="analysis-auto-start" type="checkbox" ${(view.automationDraft ?? s.analysis?.auto_start) === true ? "checked" : ""}> 录制结束后自动 AI 切片</label><p class="muted">默认关闭。开发期间由你在录播库选择场次，再点击“AI 分析本场录播”。</p><p class="hint">开启并保存后，新完成的录播会自动将候选视频发送给 AI 分析并生成待审核片段。发布仍需人工确认。</p><div class="action-row"><button id="analysis-automation-save" class="btn btn-primary" type="submit">保存切片设置</button><span id="analysis-automation-message" role="status">${s.analysis?.auto_start === true ? "当前已开启自动切片" : "当前已关闭自动切片"}</span></div><p class="hint">关闭会取消尚未开始的自动分析；已经开始的分析完成当前任务。保存立即生效，已有录播不会补发。</p></form></section>`;
  else if (view.tab === "paths")
    body = configSection("媒体目录", {videos_dir: s.paths?.videos_dir}) +
      notice("当前生效策略：第 5 天提醒，第 7 天到期排回收，移入 Windows 回收站。正在执行的任务受保护。") +
      '<p class="muted">5 天清理、发布后 24 小时清理和手动保留尚未接入当前服务。</p><a class="btn btn-secondary" href="#/recycle">查看到期与回收记录</a>';
  else if (view.tab === "publish")
    body = `<section class="card card-pad"><h2>投稿账号</h2><p>${c.diagnostics.cookie_configured ? "凭据已配置 · 登录有效性尚未检查" : "尚未配置投稿凭据"}</p><p class="muted">成片阶段确认标题、简介与标签，批准后进入发布队列。</p></section>` +
      configSection("全局发布配置", s.upload) +
      '<a class="btn btn-secondary" href="#/streamer">编辑主播默认简介与标签</a>';
  else if (view.tab === "advanced")
    body = notice("以下参数由当前服务提供只读摘要；修改配置文件后重启相关服务生效。网页编辑接口尚未接入。") +
      `<div class="config-grid">${configSection("AI 分析", s.mimo)}${configSection("候选召回", s.burst)}${configSection("质量门", s.quality)}${configSection("转写与对齐", s.subtitle)}${configSection("分析参数", s.analysis)}${configSection("Worker", s.worker)}${configSection("配置路径", s.paths)}</div>` +
      `<details class="diagnostics"><summary>本地依赖与模型诊断</summary><p>设备可见和模型缓存不等于推理成功。</p><pre>${htmlEscape(JSON.stringify({asr: c.asr, diagnostics: c.diagnostics}, null, 2))}</pre></details>`;
  else
    body = subtitleStyleSettingsHTML(s.edit) + notice("保存立即用于新片段。已有片段需应用默认样式并重新生成成片；已批准成片受保护。");
  document.getElementById("main").innerHTML =
    pageHead(
      "设置",
      "分区调整 · 统一保存",
      `<button class="icon-button" id="runtime-refresh" aria-label="刷新">${icon("refresh")}</button>`,
    ) +
    '<div class="settings-layout">' + tabsHTML(settingsTabs, view.tab, "data-settings-tab") +
    `<div class="settings-body">${body}<p id="runtime-message" role="status"></p><footer class="page-foot">本机部署 · 后台独立运行</footer></div></div><div id="settings-save-bar" class="settings-save-bar" hidden><span id="settings-dirty-count" role="status"></span><button id="settings-discard" class="btn btn-secondary">放弃修改</button><button id="settings-save-all" class="btn btn-primary">保存更改</button></div>`;
  restorePageScroll("settings");
  bindSubtitleSettings();
  const analysisForm = document.getElementById("analysis-automation-form");
  if (analysisForm) {
    document.getElementById("analysis-auto-start").onchange = event => {
      view.automationDraft = event.target.checked === view.config.settings.analysis.auto_start ? null : event.target.checked;
      updateSettingsSaveBar();
    };
    analysisForm.onsubmit = event => { event.preventDefault(); saveSettingsChanges(); };
  }
  document.getElementById("settings-discard").onclick = () => { view.subtitleDraft = view.automationDraft = null; drawSettings(); };
  document.getElementById("settings-save-all").onclick = saveSettingsChanges;
  updateSettingsSaveBar();
  document.getElementById("runtime-refresh").onclick = renderRuntimeSettings;
  document.querySelectorAll("[data-settings-tab],[data-settings-jump]").forEach(
    (b) =>
      (b.onclick = () => {
        view.tab = b.dataset.settingsTab || b.dataset.settingsJump;
        drawSettings();
      }),
  );
  document.querySelectorAll("[data-runtime-path]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        try {
          await reviewAPI(b.dataset.runtimePath, {});
          if (pageKey() === "settings") {
            await renderRuntimeSettings();
            setMessage("runtime-message", "操作已提交，服务状态已重新探测。");
          }
        } catch (e) {
          if (b.isConnected) {
            setMessage("runtime-message", `操作未完成：${e.message}`);
            b.disabled = false;
          }
        }
      }),
  );
}
