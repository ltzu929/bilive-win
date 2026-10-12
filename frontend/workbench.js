/* Real manual workbench. Drafts stay local until explicitly saved. */
const review = {
  recordings: [],
  task: null,
  segments: [],
  segment: null,
  offset: 0,
  evidenceOffset: 0,
  tab: "content",
  media: "source",
  unsaved: false,
  styleUnsaved: false,
  metadataUnsaved: false,
  rangeUnsaved: false,
  largePlayer: false,
  pickerCollapsed: false,
};
const recordingPicker = { query: "", up: "", from: "", to: "", group: "date", sort: "newest", offset: 0, scrollTop: 0 };
function captureRecordingPickerScroll() {
  const list = document.getElementById("recording-results");
  if (list) recordingPicker.scrollTop = list.scrollTop;
}
function recordingMeta(recording) {
  const path = recording.source_relpath.replaceAll("\\", "/");
  const parts = path.split("/"), name = parts.at(-1);
  const folder = parts.length > 1 ? parts.at(-2) : "";
  const room = folder.match(/^(\d+)(?:\s*-|_|$)/)?.[1] ||
    name.match(/^(?:blive_)?(\d+)[_-]/)?.[1] || "";
  const upName = folder.match(/^\d+\s*-\s*(.+)$/)?.[1] || (room ? `房间 ${room}` : folder || "未知 UP");
  const match = name.match(/(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_](\d{2})[-_]?(\d{2})[-_]?(\d{2})(?=[_.-]|$)/);
  let date = "", time = "", epoch = null;
  if (match) {
    date = `${match[1]}-${match[2]}-${match[3]}`;
    time = `${match[4]}:${match[5]}:${match[6]}`;
    const parsed = Date.parse(`${date}T${time}+08:00`);
    if (Number.isFinite(parsed) && new Date(parsed + 8*3600000).toISOString().slice(0, 19) === `${date}T${time}`) epoch = parsed;
    else { date = ""; time = ""; }
  }
  return { ...recording, name, room, upName, upKey: room || folder || "unknown", date, time, epoch };
}
function sortedRecordings(items, sort = recordingPicker.sort) {
  return [...items].sort((a, b) => {
    if (sort.startsWith("up")) {
      const up = a.upName.localeCompare(b.upName, "zh-CN") || a.upKey.localeCompare(b.upKey);
      if (up) return sort === "up-desc" ? -up : up;
    }
    if (a.epoch == null && b.epoch != null) return 1;
    if (b.epoch == null && a.epoch != null) return -1;
    const delta = (a.epoch ?? 0) - (b.epoch ?? 0);
    return (sort === "oldest" ? delta : -delta) || a.source_relpath.localeCompare(b.source_relpath, "zh-CN");
  });
}
function recordingPickerHTML() {
  const ups = new Map(review.recordings.map(recordingMeta).map(r => [r.upKey, r.upName]));
  return `<div class="recording-filters"><label class="field recording-search">搜索<input id="recording-search" type="search" aria-label="搜索场次" placeholder="搜索 UP、房间号或文件名" value="${htmlEscape(recordingPicker.query)}"></label>
    <label class="field">UP<select id="recording-up" aria-label="按 UP 筛选"><option value="">全部 UP</option>${[...ups].sort((a,b)=>a[1].localeCompare(b[1],"zh-CN")).map(([key,name])=>`<option value="${htmlEscape(key)}" ${key===recordingPicker.up?"selected":""}>${htmlEscape(name)}</option>`).join("")}</select></label>
    <label class="field">分类<select id="recording-group" aria-label="场次分类"><option value="date">按录制日期</option><option value="up">按 UP</option><option value="none">不分组</option></select></label>
    <label class="field">排序<select id="recording-sort" aria-label="场次排序"><option value="newest">时间：最新在前</option><option value="oldest">时间：最早在前</option><option value="up-asc">UP：名称正序</option><option value="up-desc">UP：名称倒序</option></select></label>
    <label class="field">开始日期<input id="recording-from" type="date" aria-label="开始日期" value="${recordingPicker.from}"></label><label class="field">结束日期<input id="recording-to" type="date" aria-label="结束日期" value="${recordingPicker.to}"></label>
    <button class="btn btn-secondary btn-sm" id="recording-reset">清除筛选</button></div>
    <p class="hint">录制时间来自文件名；无法识别的时间单独显示为未知。</p><div id="recording-results" class="recording-results"></div><div class="recording-pagination"><button id="recording-prev" class="btn btn-secondary btn-sm">上一页场次</button><span id="recording-count" role="status"></span><button id="recording-next" class="btn btn-secondary btn-sm">下一页场次</button></div>`;
}
function drawRecordingResults(scrollTop = 0) {
  const host = document.getElementById("recording-results");
  if (!host) return;
  recordingPicker.scrollTop = scrollTop;
  if (recordingPicker.from && recordingPicker.to && recordingPicker.from > recordingPicker.to) {
    host.innerHTML = emptyState("开始日期不能晚于结束日期");
    setMessage("recording-count", "请调整日期范围");
    for (const id of ["recording-prev", "recording-next"]) document.getElementById(id).disabled = true;
    return;
  }
  const query = recordingPicker.query.trim().toLocaleLowerCase();
  let items = sortedRecordings(review.recordings.map(recordingMeta).filter(r =>
    (!recordingPicker.up || r.upKey === recordingPicker.up) &&
    (!recordingPicker.from || r.date && r.date >= recordingPicker.from) &&
    (!recordingPicker.to || r.date && r.date <= recordingPicker.to) &&
    (!query || `${r.upName} ${r.room} ${r.source_relpath}`.toLocaleLowerCase().includes(query))));
  const groups = new Map();
  for (const r of items) {
    const key = recordingPicker.group === "up" ? r.upKey : recordingPicker.group === "date" ? r.date || "unknown" : "all";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  const entries = [...groups].sort(([a, left], [b, right]) => {
    if (recordingPicker.group === "date") {
      if (a === "unknown") return 1;
      if (b === "unknown") return -1;
      return recordingPicker.sort === "oldest" ? a.localeCompare(b) : b.localeCompare(a);
    }
    if (recordingPicker.group === "up") {
      const delta = left[0].upName.localeCompare(right[0].upName, "zh-CN") || a.localeCompare(b);
      return recordingPicker.sort === "up-desc" ? -delta : delta;
    }
    return 0;
  });
  items = entries.flatMap(([,rows])=>rows);
  recordingPicker.offset = Math.min(recordingPicker.offset, Math.max(0, Math.floor((items.length-1)/20)*20));
  const visible = items.slice(recordingPicker.offset, recordingPicker.offset+20);
  let previous = null;
  host.innerHTML = visible.map(r => {
    const group = recordingPicker.group === "up" ? r.upKey : r.date || "unknown";
    const heading = recordingPicker.group !== "none" && group !== previous ?
      `<h3 class="recording-group-title">${htmlEscape(recordingPicker.group === "up" ? r.upName : r.date || "录制时间未知")}</h3>` : "";
    previous = group;
    const selected = r.task_id === review.task?.task_id;
    return `${heading}<button class="recording-option ${selected?"selected":""}" data-recording="${htmlEscape(r.task_id)}" aria-pressed="${selected}"><span class="recording-option-title"><strong>${htmlEscape(r.upName)}</strong><span>${htmlEscape(r.date ? `${r.date} ${r.time}` : "录制时间未知")}</span>${selected?statusBadge("当前场次","blue"):""}</span><span class="recording-file" title="${htmlEscape(r.source_relpath)}">${htmlEscape(r.name)}</span><span class="recording-meta">${htmlEscape(r.room?`房间 ${r.room} · `:"")}${bytesLabel(r.size)}</span></button>`;
  }).join("") || emptyState("没有符合条件的场次，请调整或清除筛选");
  host.scrollTop = scrollTop;
  setMessage("recording-count", `筛选 ${items.length} / 全部 ${review.recordings.length} 场${items.length?` · ${recordingPicker.offset+1}–${recordingPicker.offset+visible.length}`:""}`);
  document.getElementById("recording-prev").disabled = recordingPicker.offset === 0;
  document.getElementById("recording-next").disabled = recordingPicker.offset+20 >= items.length;
  host.querySelectorAll("[data-recording]").forEach(button => button.onclick = () => {
    if (button.dataset.recording === review.task?.task_id) {
      document.getElementById("source-dialog")?.close();
      return;
    }
    location.hash = pageLink("library", { source_task_id: button.dataset.recording });
  });
}
function bindRecordingPicker() {
  for (const [id,key] of [["recording-search","query"],["recording-up","up"],["recording-from","from"],["recording-to","to"],["recording-group","group"],["recording-sort","sort"]]) {
    const control = document.getElementById(id);
    control.value = recordingPicker[key];
    control.addEventListener(id === "recording-search" ? "input" : "change", () => {
      recordingPicker[key] = control.value;
      recordingPicker.offset = 0;
      drawRecordingResults();
    });
  }
  document.getElementById("recording-reset").onclick = () => {
    for (const [id,key] of [["recording-search","query"],["recording-up","up"],["recording-from","from"],["recording-to","to"]]) {
      recordingPicker[key] = "";
      document.getElementById(id).value = "";
    }
    recordingPicker.offset = 0;
    drawRecordingResults();
  };
  document.getElementById("recording-prev").onclick = () => { recordingPicker.offset -= 20; drawRecordingResults(); };
  document.getElementById("recording-next").onclick = () => { recordingPicker.offset += 20; drawRecordingResults(); };
  drawRecordingResults(recordingPicker.scrollTop);
}
const bytesLabel = (n) =>
  Number.isFinite(n)
    ? n >= 1073741824
      ? `${(n / 1073741824).toFixed(2)} GiB`
      : `${(n / 1048576).toFixed(2)} MiB`
    : "未知";
const reviewStates = {
  needs_review: "待人工审核",
  needs_finalize: "需要生成成片",
  staging_failed: "成片暂存失败",
  awaiting_publish: "待发布确认",
  queued: "已批准，等待上传",
  uploading: "上传中",
  uploaded: "已上传，等待投稿",
  publishing: "投稿中",
  published: "已发布",
  failed: "发布失败",
  dropped: "已废弃",
};
const reviewJobStates = {
  pending: "等待处理",
  processing: "正在处理",
  done: "已完成",
  failed: "处理失败",
};
const actionLabels = {
  transcribe_subtitles: "字幕识别",
  finalize: "成片生成",
  reburn: "成片重烧",
  render: "成片生成",
  generate_metadata: "文案生成",
  retry_judge: "重新判断",
  approve_publish: "发布批准",
};
let reviewTimer;
const stageLabels = {
  candidate_cut: "候选裁剪",
  auxiliary_asr: "辅助转写",
  judgment: "内容判断",
  formal_asr: "正式转写",
  correction: "校正尝试",
  subtitles_save: "字幕保存",
  finalize: "成片生成",
  manual_finalize: "人工成片生成",
  stage: "暂存",
};
function stageTimings(timings) {
  return (
    Object.entries(timings || {})
      .map(
        ([name, v]) =>
          `${htmlEscape(stageLabels[name] || name)}：${Number.isFinite(v.elapsed_seconds) ? v.elapsed_seconds.toFixed(2) + " 秒" : "耗时未知"}${v.status === "failed" ? "（失败）" : v.status === "processing" ? "（处理中）" : ""}`,
      )
      .join(" · ") || "暂无阶段耗时记录"
  );
}
const apiURL = (path) =>
  location.pathname.startsWith("/workbench/") && path.startsWith("/api/")
    ? "/studio-api/" + path.slice(5)
    : path;
async function reviewAPI(path, body) {
  const r = await fetch(
    apiURL(path),
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
const draftKey = () =>
  `bilive:subtitle:${review.task?.task_id}:${review.segment?.segment_id}`;
const styleDraftKey = () =>
  `bilive:style:${review.task?.task_id}:${review.segment?.segment_id}`;
const metadataDraftKey = () => `bilive:metadata:${review.task?.task_id}:${review.segment?.segment_id}`;
const rangeDrafts = new Map();
function captureRangeDraft() {
  const s = review.segment;
  const start = document.getElementById("review-start"), end = document.getElementById("review-end");
  if (!s || !start || !end || document.getElementById("review-source")?.dataset.segment !== s.segment_id) return;
  if (start.value === timecode(s.source_start) && end.value === timecode(s.source_end))
    rangeDrafts.delete(s.segment_id);
  else rangeDrafts.set(s.segment_id, {start: start.value, end: end.value});
}
function rangePoint(id, saved) {
  const input = document.getElementById(id).value.trim();
  return input === timecode(saved) ? saved : parseTimecode(input);
}
function reviewMessage(text) {
  const e = document.getElementById("review-message");
  if (e) e.textContent = text;
  setMessage("context-feedback", text);
}
function collectReviewLines(raw = false) {
  return [...document.querySelectorAll("#review-lines tr")].map((tr) => ({
    start: raw ? 0 : parseTimecode(tr.querySelector("[data-start]").value),
    end: raw ? 0 : parseTimecode(tr.querySelector("[data-end]").value),
    text: tr.querySelector("[data-text]").value,
    ...(raw
      ? {
          startText: tr.querySelector("[data-start]").value,
          endText: tr.querySelector("[data-end]").value,
        }
      : {}),
  }));
}
function writeReviewDraft() {
  if (!review.segment) return;
  document.querySelectorAll("#review-lines tr").forEach((row, index) => {
    const seek = row.querySelector("[data-subtitle-seek]");
    seek.textContent = index + 1;
    seek.setAttribute("aria-label", `定位第${index + 1}行字幕`);
    row
      .querySelector("[data-start]")
      .setAttribute("aria-label", `第${index + 1}行开始时间`);
    row
      .querySelector("[data-end]")
      .setAttribute("aria-label", `第${index + 1}行结束时间`);
    row
      .querySelector("[data-text]")
      .setAttribute("aria-label", `第${index + 1}行字幕`);
  });
  review.unsaved = true;
  updateSubtitleEmptyState();
  updateReviewActions();
  try {
    localStorage.setItem(
      draftKey(),
      JSON.stringify({
        revision: review.segment.revision,
        lines: collectReviewLines(true),
      }),
    );
    reviewMessage("有未保存字幕 · 草稿保存在此浏览器");
  } catch {
    reviewMessage("有未保存字幕 · 浏览器草稿存储不可用，请保留文本");
  }
}
function updateSubtitleEmptyState() {
  const empty = document.getElementById("subtitle-empty");
  const hasLines = !!document.querySelector("#review-lines tr");
  if (empty) empty.hidden = hasLines;
  const table = document.querySelector(".subtitle-scroll");
  if (table) table.hidden = !hasLines;
}
function subtitleRows(lines) {
  return lines
    .map(
      (l, i) =>
        `<tr><td><button class="subtitle-seek text-button" data-subtitle-seek aria-label="定位第${i + 1}行字幕" title="定位到此行字幕">${i + 1}</button></td><td><input aria-label="第${i + 1}行开始时间" data-start value="${htmlEscape(l.startText ?? timecode(l.start, true, true))}"></td><td><input aria-label="第${i + 1}行结束时间" data-end value="${htmlEscape(l.endText ?? timecode(l.end, true, true))}"></td><td><textarea aria-label="第${i + 1}行字幕" data-text rows="2">${htmlEscape(l.text)}</textarea></td><td><button class="icon-button" data-delete-line aria-label="删除字幕行">${icon("trash")}</button></td></tr>`,
    )
    .join("");
}
function capturePlayback() {
  const source = document.getElementById("review-source");
  const final = document.getElementById("review-final");
  if (source)
    review.playback = {
      task: source.dataset.task,
      segment: source.dataset.segment,
      time: source.currentTime,
      paused: source.paused,
      finalTime: final?.currentTime,
      finalPaused: final?.paused,
    };
}
function metadataSuggestionHTML(s) {
  const job = s?.metadata_job;
  if (!job) return "";
  if (job.status === "failed")
    return `<p role="alert">文案生成失败：${htmlEscape(job.result?.error || "请重试")}。原投稿资料保留。</p>`;
  if (["pending", "processing"].includes(job.status))
    return '<p role="status">正在根据当前已保存字幕生成文案建议…</p>';
  if (job.payload?.revision !== s.revision || job.result?.revision !== s.revision)
    return '<p class="hint">片段已修改，旧文案建议不再适用，请重新生成。</p>';
  const metadata = job.result?.metadata;
  if (!metadata) return "";
  return `<h4>文案建议</h4><p><strong>${htmlEscape(metadata.title)}</strong></p><p>${htmlEscape(metadata.description)}</p><p class="hint">TAG：${htmlEscape(metadata.tags.join("，"))}</p><button id="review-metadata-apply" class="btn btn-secondary" type="button">采用建议</button><p class="hint">建议尚未保存，请对照片段核对；采用后仍可修改。</p>`;
}

async function renderManualWorkbench() {
  captureRangeDraft();
  clearTimeout(reviewTimer);
  clearTimeout(analysisTimer);
  capturePlayback();
  captureRecordingPickerScroll();
  const current = beginPage("library");
  document.getElementById("main").innerHTML =
    pageHead("录播库") + emptyState("正在读取本机录播…");
  try {
    const params = pageParams(),
      sourceLink = params.get("source_task_id"),
      segmentLink = params.get("segment_id"),
      room = params.get("room");
    let selected = review.task;
    const signature = params.toString(),
      newLink = review.linkSignature !== signature;
    if (newLink && ["content", "subtitles", "final"].includes(params.get("mode"))) review.tab = params.get("mode");
    const all = await readAllPages("/api/recordings", current);
    if (!current()) return;
    const ordered = sortedRecordings(all.map(recordingMeta), "newest");
    const scoped = room ? ordered.filter(r => r.room === room) : ordered;
    if (newLink && room) {
      Object.assign(recordingPicker, { query: "", up: room, from: "", to: "", offset: 0, scrollTop: 0 });
    }
    if (sourceLink && newLink) {
      selected = ordered.find(r => r.task_id === sourceLink);
      if (!selected) throw new Error("该场次已不在录播库中，请从场次列表重新选择");
    } else if (!selected || !scoped.some(r => r.task_id === selected.task_id)) {
      selected = scoped[0] || null;
      review.evidenceOffset = 0;
    }
    if (selected?.task_id !== review.task?.task_id) review.evidenceOffset = 0;
    let segments = [],
      analysisJob = null,
      evidence = null,
      lifecycle = null,
      segment = null;
    if (selected) {
      const [data, ev, life] = await Promise.all([
        reviewAPI(`/api/recordings/${selected.task_id}/segments`),
        reviewAPI(
          `/api/recordings/${selected.task_id}/evidence?limit=20&offset=${review.evidenceOffset}`,
        ),
        reviewAPI(`/api/recordings/${selected.task_id}/lifecycle`),
      ]);
      segments = data.items.sort((a, b) => a.source_start - b.source_start);
      analysisJob = data.analysis_job;
      review.analysisWorker = data.worker;
      review.autoAnalysis = data.auto_analysis_enabled === true;
      evidence = ev;
      lifecycle = life.record;
      const chosen =
        segments.find(
          (r) =>
            r.segment_id ===
            (newLink && segmentLink ? segmentLink : review.segment?.segment_id),
        ) || segments[0];
      if (chosen)
        segment = await reviewAPI(`/api/segments/${chosen.segment_id}`);
    }
    if (!current()) return;
    Object.assign(review, {
      task: selected,
      recordings: all,
      total: all.length,
      segments,
      analysisSegments: segments,
      analysisJob,
      evidence,
      lifecycle,
      segment,
      linkSignature: signature,
    });
    if (selected) {
      params.set("source_task_id", selected.task_id);
      if (segment) params.set("segment_id", segment.segment_id);
      else params.delete("segment_id");
      params.set("mode", review.tab);
      history.replaceState(null, "", `#/library?${params}`);
      review.linkSignature = params.toString();
    }
    drawManualWorkbench();
  } catch (e) {
    if (current()) pageError("录播库", e, renderManualWorkbench);
  }
}
function updateReviewActions() {
  const s = review.segment;
  if (!s) {
    const keep = document.getElementById("review-keep");
    if (keep) { keep.disabled = true; keep.closest(".context-decisions").hidden = true; }
    const next = document.getElementById("review-next-step");
    if (next) { next.disabled = true; next.hidden = true; }
    const evidence = review.evidence;
    const analysis = recordingAnalysisState();
    setMessage("context-state", analysis.title);
    setMessage("context-save", analysis.detail);
    const supplement = document.querySelector(".context-range .btn");
    if (supplement) supplement.className = "btn btn-primary";
    const badge = document.getElementById("review-draft-badge");
    if (badge) {
      badge.textContent = "未选择片段";
      badge.className = "badge muted";
    }
    setMessage("review-message", "");
    return;
  }
  const dirty = review.unsaved || review.styleUnsaved || review.metadataUnsaved || review.rangeUnsaved;
  const locked = s.publish_approval || s.active_job || s.state === "dropped";
  const subtitleReady = s.subtitles_revision === s.revision && s.subtitles?.length > 0;
  const finalize = document.getElementById("review-finalize"),
    approve = document.getElementById("review-approve");
  if (finalize) {
    finalize.disabled = !!(dirty || locked || !subtitleReady);
    finalize.textContent = s.final_valid ? "重新生成成片" : "生成成片";
    finalize.className = `btn ${!dirty && !locked && subtitleReady && !s.final_valid ? "btn-primary" : "btn-secondary"}`;
  }
  if (approve) approve.disabled = !!(dirty || locked || !s.final_valid);
  const keep = document.getElementById("review-keep");
  if (keep) {
    keep.disabled = !!(dirty || locked || s.human_reviewed);
    keep.textContent = s.human_reviewed ? "已保留" : "保留并继续";
    keep.setAttribute("aria-describedby", "context-save");
  }
  const rangeSave = document.getElementById("review-range-save");
  if (rangeSave) {
    rangeSave.disabled = !!(locked || !review.rangeUnsaved || review.unsaved || review.styleUnsaved || review.metadataUnsaved);
    rangeSave.className = `btn ${!rangeSave.disabled ? "btn-primary" : "btn-secondary"}`;
  }
  for (const id of ["review-start", "review-end", "review-in", "review-out"])
    document.getElementById(id).disabled = !!locked;
  const save = document.getElementById("review-save");
  const metadataSave = document.getElementById("review-metadata-save");
  if (metadataSave) metadataSave.className = `btn ${review.metadataUnsaved ? "btn-primary" : "btn-secondary"}`;
  const metadataGenerate = document.getElementById("review-metadata-generate");
  if (metadataGenerate) {
    metadataGenerate.disabled = !!(dirty || locked || !subtitleReady);
    metadataGenerate.textContent = s.active_job?.action === "generate_metadata" ? "正在生成文案…" : "根据当前片段生成文案";
  }
  const metadataApply = document.getElementById("review-metadata-apply");
  if (metadataApply) {
    metadataApply.disabled = !!(locked || review.unsaved || review.styleUnsaved ||
      s.metadata_job?.result?.revision !== s.revision);
    metadataApply.textContent = review.metadataUnsaved ? "采用建议，替换当前文案草稿" : "采用建议";
  }
  if (save) save.disabled = !!(locked || !document.querySelector("#review-lines tr"));
  const transcribe = document.getElementById("review-transcribe");
  if (transcribe) {
    transcribe.disabled = !!(dirty || locked);
    transcribe.textContent = s.active_job?.action === "transcribe_subtitles"
      ? "正在识别字幕…" : "本地模型识别字幕";
    transcribe.className = `btn btn-sm ${!subtitleReady && !document.querySelector("#review-lines tr") ? "btn-primary" : "btn-secondary"}`;
  }
  document.querySelectorAll(
    '#review-lines input, #review-lines textarea, #review-lines [data-delete-line], #review-style, #review-style-save, #review-style-default, #review-drop, #review-drop-open, #review-rejudge, #review-range, #review-add-line, #review-meta-title, #review-meta-description, #review-meta-tags, #review-meta-tid, #review-meta-topic, #review-meta-season, #review-topic-keywords, #review-topic-search, #review-season-load, #review-metadata-save',
  ).forEach((element) => { element.disabled = !!locked; });
  const badge = document.getElementById("review-draft-badge");
  if (badge) {
    badge.textContent = s.state === "dropped" ? "已废弃" : dirty ? "草稿未保存" : subtitleReady ? "已保存" : "字幕待补充 / 确认";
    badge.className = `badge ${s.state === "dropped" ? "muted" : dirty || !subtitleReady ? "orange" : "muted"}`;
  }
  setMessage(
    "review-save-hint",
    s.state === "dropped"
      ? `已废弃：${s.drop_reason || "人工判断无需切片"}`
      : s.publish_approval
      ? "已允许发布，当前成片受保护"
      : s.active_job
        ? `后台任务${reviewJobStates[s.active_job.status] || "处理中"}，结束后可继续审核`
        : review.rangeUnsaved
          ? "入出点尚未保存，请先保存当前范围"
        : dirty
          ? "先保存当前字幕、样式和投稿资料，再生成成片"
          : !subtitleReady
            ? "先补充并保存当前片段字幕，再生成成片"
            : !s.final_valid
              ? "核对内容与字幕后，生成成片预览"
              : "先播放当前成片，确认后允许发布",
  );
  for (const button of [finalize, approve])
    if (button) button.setAttribute("aria-describedby", "review-save-hint");
  setMessage("context-state", s.active_job ? `${actionLabels[s.active_job.action] || "后台任务"} · ${reviewJobStates[s.active_job.status] || s.active_job.status}`
    : !locked && !subtitleReady ? "等待补充 / 确认字幕" : reviewStates[s.state] || s.state);
  setMessage("context-version", `已保存范围 ${timecode(s.source_start)} – ${timecode(s.source_end)} · 版本 ${s.revision} · ${timecode(s.source_end - s.source_start, true)}`);
  setMessage("context-save", s.state === "dropped" ? "当前片段已废弃"
    : s.publish_approval ? "已批准发布，当前成片受保护"
    : s.active_job ? `${actionLabels[s.active_job.action] || "后台任务"}：${reviewJobStates[s.active_job.status] || s.active_job.status}。完成后可继续。`
    : review.rangeUnsaved ? `新范围 ${document.getElementById("review-start").value} – ${document.getElementById("review-end").value} 尚未保存，请点击“保存当前范围”。`
    : dirty ? "有未保存草稿，请先保存。当前没有后台任务。"
    : !subtitleReady ? `${s.human_reviewed ? "已保留。" : ""}等待补充字幕：${review.tab === "subtitles" ? "识别或手动填写并保存" : "点击“检查字幕”，识别或手动填写并保存"}。当前没有后台任务。`
    : !s.final_valid ? "字幕已保存，等待生成成片：点击“生成成片”。当前没有后台任务。"
    : "成片已生成，预览确认后可批准发布。当前没有后台任务。");
  const next = document.getElementById("review-next-step");
  if (next) {
    if (review.tab === "subtitles" && !subtitleReady) {
      const action = review.unsaved ? save : transcribe;
      next.textContent = review.unsaved ? "保存字幕" : "识别字幕";
      next.disabled = !action || action.disabled || review.rangeUnsaved;
      next.onclick = () => action?.click();
    } else {
      const target = review.tab === "subtitles" ? "final" : "subtitles";
      next.disabled = false;
      next.textContent = target === "subtitles" ? "检查字幕" : "进入成片确认";
      next.onclick = () => document.querySelector(`[data-review-tab="${target}"]`)?.click();
    }
  }
  if (!s.active_job) {
    const last = s.last_job;
    const error = last?.status === "failed" && last.revision === s.revision
      ? `${actionLabels[last.action] || "后台任务"}失败：${last.error || "请查看处理记录"}。可检查后重试。` : "";
    setMessage("context-job", error);
  }
  if (approve) {
    approve.textContent = "批准并发布";
    approve.className = `btn ${!dirty && !locked && s.final_valid ? "btn-primary" : "btn-secondary"}`;
  }
  if (save) save.className = `btn ${review.tab === "subtitles" && !locked ? "btn-primary" : "btn-secondary"}`;
}

function candidateNeedsReview(s) {
  return !s.human_reviewed && !s.publish_approval && s.state !== "dropped";
}

let analysisTimer;
function recordingAnalysisState(job = review.analysisJob, evidence = review.evidence, segments = review.analysisSegments || review.segments, worker = review.analysisWorker) {
  const e = evidence || {}, total = e.total || 0;
  // Older running API processes do not yet return summary. Only count a complete page.
  const summary = e.summary || (e.candidates?.length === total ? {
    completed: e.candidates.filter(c => c.state !== "processing").length,
    dropped: e.candidates.filter(c => c.state === "drop").length,
    failed: e.candidates.filter(c => c.state === "failed" || c.judgments?.some(j => j.judge_status === "judge_failed")).length,
    needs_review: e.candidates.filter(c => c.state === "review").length,
  } : {});
  const status = job?.status || e.status;
  const count = segments.filter(s => s.state !== "dropped").length;
  const stageFailures = (e.segments || []).filter(s => s.error || Object.values(s.timings || {}).some(t => t.status === "failed")).length;
  const elapsed = status === "processing" && job?.progress?.started_at
    ? Math.max(0, Date.now() / 1000 - job.progress.started_at) : job?.progress?.elapsed_seconds;
  const timing = Number.isFinite(elapsed) ? ` · 已用 ${timecode(Math.floor(elapsed))}` : "";
  if (review.analysisSubmitting) return {title: "正在提交 AI 分析…", detail: "正在为本场录播创建分析任务。", tone: "busy", empty: "分析请求正在提交，请稍候。"};
  if (["pending", "processing"].includes(status)) {
    if (["paused", "offline"].includes(worker?.state)) return {
      title: worker.state === "paused" ? "AI 分析已暂停" : "AI 分析中断：后台未运行",
      detail: "当前没有后台处理服务执行本场分析。任务和已生成文件已保留；点击“恢复分析”启动后台并继续处理。",
      tone: "warning", empty: "分析尚未完成，点击“恢复分析”继续处理。", resume: true,
    };
    if (worker?.state === "stopping") return {title: "AI 分析正在停止", detail: "后台正在结束当前操作，剩余任务将保留。停止后可点击“恢复分析”。", tone: "warning", empty: "处理服务正在停止，分析尚未完成。"};
    if (worker?.state === "unavailable") return {title: "分析服务不可用", detail: `无法确认后台处理状态：${worker.detail || "请检查设置中的处理服务"}。任务仍保留。`, tone: "error", empty: "后台状态无法确认，分析尚未完成。"};
    if (status === "pending" && job?.progress?.reason) return {title: "AI 分析暂时无法开始", detail: `环境检查未通过：${job.progress.reason}。请检查设置中的模型与显卡环境，修复后后台会重新检查。`, tone: "warning", empty: "任务因环境问题等待处理，尚未生成分析结果。"};
  }
  if (status === "pending") return {title: "AI 分析已排队", detail: `等待后台处理服务开始分析${job?.progress?.reason ? "：" + job.progress.reason : "。准备分析环境或处理其他任务可能需要等待。"}`, tone: "busy", empty: "分析任务已提交，尚未开始处理；这里还不是分析结果。"};
  const stages = {candidate_cut: "裁剪候选视频", auxiliary_asr: "识别参考字幕 / 等待识别", judgment: "准备 AI 视频 / 等待判断"};
  const currentStages = (e.candidates || []).map((c, i) => {
    const active = Object.entries(c.timings || {}).find(([, t]) => t.status === "processing");
    const label = active ? stages[active[0]] || active[0]
      : c.state === "failed" ? "处理失败"
      : c.state !== "processing" ? "已处理"
      : c.timings?.judgment?.status === "done" ? "判断已结束，等待汇总"
      : Object.values(c.timings || {}).some(t => t.status === "failed") ? "准备失败，等待汇总"
      : "等待处理";
    return `候选 ${(e.offset || 0) + i + 1}：${label}`;
  }).join("；");
  if (status === "processing") return {title: `AI 正在分析${timing}`, detail: total
    ? `已召回 ${total} 个候选区间${Number.isInteger(summary.completed) ? `，已处理 ${summary.completed} / ${total} 个` : ""}。${currentStages || "正在进行视频准备、AI 判断或字幕成片"}。完成后会自动更新。`
    : "正在读取录播、召回候选或准备分析。完成后会自动更新。", tone: "busy", empty: "AI 分析尚未结束，暂未生成待审核片段，请等待结果。"};
  if (job?.result?.cancelled || e.skipped) return {title: job?.result?.cancelled ? "AI 分析已取消" : "本场分析已跳过", detail: e.skip_reason === "below min_source_size_mb" ? "源文件小于配置的分析门槛，未进行 AI 判断。可手动补选片段。" : "本场未完成 AI 分析，可重新提交或手动补选片段。", tone: "muted", empty: "尚无待审核片段，可重新分析或手动补选。"};
  if (status === "failed") return {title: "AI 分析失败", detail: `${job?.result?.error || e.error || "处理未能完成，请查看分析证据。"} 可重试或手动补选片段。`, tone: "error", empty: "分析失败，不能据此判断本场没有值得切的片段。"};
  if (status === "done") {
    if (summary.failed || summary.needs_review || stageFailures) return {title: "AI 分析结束，仍需人工检查", detail: `当前 ${count} 个待审核片段；${summary.failed || 0} 个候选处理失败，${summary.needs_review || 0} 个候选需人工检查${stageFailures ? `，${stageFailures} 个片段在字幕 / 成片阶段失败` : ""}${timing}。查看分析证据或手动补选。`, tone: "warning", empty: "部分候选或片段未能完成处理，不能据此判断本场没有值得切的片段。"};
    if (!total) return {title: "分析完成，未召回候选区间", detail: `弹幕召回没有找到候选区间${timing}。可手动补选片段；这不代表 AI 看完了整场并判定无可保留内容。`, tone: "muted", empty: "本次未召回候选区间，可手动补选源区间创建片段。"};
    if (summary.dropped === total) return {title: "分析完成，AI 未建议保留片段", detail: `已检查 ${total} 个召回区间，AI 均建议不保留${timing}。仍可查看分析证据并手动补选。`, tone: "muted", empty: "本次召回的候选均被 AI 建议不保留，可人工检查或补选。"};
    return {title: `AI 分析完成 · ${count} 个片段可审核`, detail: `已处理 ${total} 个召回区间${timing}。请选择片段检查内容、字幕和成片。`, tone: "success", empty: "分析已结束，当前没有可审核片段。可查看分析证据或手动补选。"};
  }
  return {title: "本场尚未进行 AI 分析", detail: "点击“AI 分析本场录播”开始，也可手动补选源区间创建片段。", tone: "muted", empty: "尚未分析。可点击“AI 分析本场录播”，或手动补选片段。"};
}
function updateRecordingAnalysis(message = "") {
  const state = recordingAnalysisState();
  const banner = document.getElementById("recording-analysis");
  if (!banner) return;
  banner.className = `recording-analysis analysis-${state.tone}`;
  banner.setAttribute("aria-busy", String(state.tone === "busy"));
  setMessage("analysis-title", state.title);
  setMessage("analysis-detail", message || state.detail);
  const resume = document.getElementById("analysis-resume");
  if (resume) { resume.hidden = !state.resume; resume.disabled = !!(review.analysisSubmitting || review.lifecycle?.review_result); }
  setMessage("candidate-empty", state.empty);
  if (!review.segment) {
    setMessage("context-state", state.title);
    setMessage("context-save", message || state.detail);
  }
  for (const id of ["review-analyze", "review-analyze-now"]) {
    const button = document.getElementById(id);
    if (!button) continue;
    button.disabled = !!(review.analysisSubmitting || review.lifecycle?.review_result || (!state.resume && ["pending", "processing"].includes(review.analysisJob?.status)));
    button.textContent = review.analysisSubmitting ? "正在提交…" : state.resume ? "恢复分析" : review.analysisJob?.status === "pending" ? "AI 分析已排队" : review.analysisJob?.status === "processing" ? "AI 正在分析…" : review.analysisJob?.status === "failed" ? "重试 AI 分析" : "AI 分析本场录播";
  }
}
function analysisInputSnapshot() {
  return ["review-start", "review-end", "review-title", "review-note"].map(id => document.getElementById(id)?.value || "").join("\n");
}
async function watchRecordingAnalysis(taskId, epoch) {
  const current = () => pageKey() === "library" && pageEpoch === epoch && review.task?.task_id === taskId;
  if (!current()) return;
  try {
    const [data, evidence] = await Promise.all([
      reviewAPI(`/api/recordings/${taskId}/segments`),
      reviewAPI(`/api/recordings/${taskId}/evidence?limit=20&offset=${review.evidenceOffset}`),
    ]);
    if (!current()) return;
    review.analysisJob = data.analysis_job;
    review.analysisWorker = data.worker;
    review.analysisSegments = data.items;
    review.evidence = evidence;
    const ended = !["pending", "processing"].includes(data.analysis_job?.status);
    const editing = review.unsaved || review.styleUnsaved || review.metadataUnsaved || analysisInputSnapshot() !== review.analysisInputs || document.querySelector("dialog[open]");
    // Poll only status while working. Rebuild at completion only when no draft is being edited.
    if (ended && !editing && !review.segment?.active_job) {
      review.analysisPlaybackTask = taskId;
      await renderManualWorkbench();
      return;
    }
    updateRecordingAnalysis(ended ? "分析已结束。当前播放和编辑已保留；保存草稿后点击“刷新场次”查看最新片段。" : "");
    if (!ended) analysisTimer = setTimeout(() => watchRecordingAnalysis(taskId, epoch), 2000);
  } catch (e) {
    if (!current()) return;
    updateRecordingAnalysis(`状态暂时无法更新：${e.message}。已提交任务仍保留，正在重新查询。`);
    analysisTimer = setTimeout(() => watchRecordingAnalysis(taskId, epoch), 4000);
  }
}

/* Move existing controls into the shared-player layout without duplicating editors. */
function arrangeReviewWorkbench() {
  const main = document.getElementById("main");
  const layout = document.createElement("div");
  layout.className = `library-layout ${review.pickerCollapsed ? "picker-collapsed" : ""}`;
  const rail = document.createElement("aside");
  rail.className = "recording-rail";
  rail.setAttribute("aria-label", "录播选择");
  rail.innerHTML = '<div class="rail-heading"><h2>场次</h2><button class="icon-button" id="recording-collapse" aria-label="收起场次列表">' + icon("chevron-left") + '</button></div>';
  const filterButton = document.createElement("button");
  filterButton.className = "text-button recording-filter-toggle";
  filterButton.textContent = "筛选与排序";
  filterButton.setAttribute("aria-expanded", "false");
  filterButton.onclick = () => { const open = rail.classList.toggle("filters-open"); filterButton.setAttribute("aria-expanded", String(open)); };
  rail.append(filterButton);
  const picker = document.getElementById("source-dialog");
  const pickerParts = [".recording-filters", ".recording-filters + .hint", "#recording-results", ".recording-pagination"].map(selector => picker?.querySelector(selector));
  for (const element of pickerParts) {
    if (element) rail.append(element);
  }
  const workspace = document.createElement("div");
  workspace.className = "recording-workspace";
  const dialogs = [...main.querySelectorAll("dialog")];
  [...main.children].filter(node => !dialogs.includes(node)).forEach(node => workspace.append(node));
  layout.append(rail, workspace);
  main.prepend(layout);
  document.getElementById("recording-collapse").onclick = () => {
    review.pickerCollapsed = true;
    layout.classList.add("picker-collapsed");
  };
  const manage = workspace.querySelector('.head-actions [data-dialog="source-dialog"]');
  if (manage) {
    manage.removeAttribute("data-dialog");
    manage.className = "btn btn-secondary btn-sm";
    manage.textContent = "场次列表";
    manage.onclick = () => { review.pickerCollapsed = !review.pickerCollapsed; layout.classList.toggle("picker-collapsed", review.pickerCollapsed); };
  }
  if (!review.task) return;
  const grid = workspace.querySelector(".workbench-grid");
  const editor = workspace.querySelector(".editor-column");
  const candidates = workspace.querySelector(".candidate-panel");
  const footer = candidates.querySelector(".workbench-actions");
  const job = document.getElementById("review-job");
  const content = document.getElementById("content-panel");
  const metadata = content.querySelector(".metadata-form");
  const finalPanel = document.createElement("div");
  finalPanel.id = "final-panel";
  finalPanel.hidden = review.tab !== "final";
  finalPanel.innerHTML = '<h3>成片确认</h3><p class="muted">在上方切换到当前成片预览，核对投稿资料后批准并发布。</p>';
  if (review.segment) {
    const s = review.segment;
    finalPanel.insertAdjacentHTML('beforeend', `<p id="final-preview-status" role="status">${s.preview_complete ? "已完整预览" : "尚未完整预览"} · 预览记录不影响人工批准</p>${s.cover_path ? `<figure><img src="${apiURL(`/api/segments/${s.segment_id}/cover`)}?revision=${s.revision}" alt="系统生成的默认封面" style="max-width:240px"><figcaption>系统默认封面</figcaption></figure>` : '<p class="muted">生成成片时自动生成默认封面。</p>'}`);
  }
  if (metadata) finalPanel.append(metadata);
  finalPanel.append(footer);
  footer.querySelector(".hint")?.remove();
  const drop = finalPanel.querySelector("#review-drop-open");
  editor.querySelector(".editor-body").append(finalPanel);
  workspace.append(editor, job);
  content.prepend(candidates);
  candidates.querySelector("h3").textContent = `片段候选 · ${review.segments.length} 个 · ${review.segments.filter(candidateNeedsReview).length} 个待审核`;
  content.querySelectorAll(".definition-list dt").forEach(dt => {
    if (["质量", "完整度", "置信度"].includes(dt.textContent)) { dt.nextElementSibling.remove(); dt.remove(); }
  });
  const context = document.createElement("aside");
  context.className = "review-context";
  context.innerHTML = `<h2>${htmlEscape(review.segment?.title || "当前录播")}</h2><p id="context-state" class="muted"></p><p id="context-version" class="num muted"></p><p id="context-save" class="hint" role="status"></p><div class="context-range"></div><p class="context-reason">${htmlEscape(review.segment?.quality_reason || review.segment?.note || "请选择候选，结合视频作出人工判断。")}</p><div class="context-decisions"><button id="review-keep" class="btn btn-primary">保留并继续</button></div><p id="context-job" role="status"></p><p id="context-feedback" role="status"></p><p>播放位置 <span id="context-position" class="num">00:00:00</span></p><button id="review-next-step" class="btn btn-secondary">检查字幕</button>`;
  const range = grid.querySelector(".range-controls");
  context.querySelector(".context-range").append(range);
  if (drop) context.querySelector(".context-decisions").append(drop);
  grid.append(context);
  const tabs = editor.querySelector(".tabs");
  tabs.outerHTML = tabsHTML([["content", "候选"], ["subtitles", "字幕"], ["final", "成片"]], review.tab, "data-review-tab");
  const evidenceButton = document.getElementById("show-evidence");
  grid.querySelector(".density-empty").remove();
  content.append(evidenceButton);
  const finalActions = document.createElement("div");
  finalActions.className = "context-final-actions";
  finalActions.append(footer);
  context.append(finalActions);
  editor.querySelector(".section-heading > .hint")?.remove();
  const details = content.querySelectorAll("details");
  const style = [...details].find(node => node.querySelector("#review-style"));
  if (style) document.getElementById("subtitle-panel").append(style);
  if (review.segment) {
    setMessage("review-save-hint", "");
    setMessage("candidate-progress", "");
  }
}
function reviewEvidenceHTML() {
  const e = review.evidence;
  if (!e) return '<p class="muted">暂无分析证据</p>';
  return `<details id="recording-evidence" ${review.evidenceOpen ? "open" : ""}><summary>分析证据与占用 · 源 ${bytesLabel(e.source_size)} · 本场 ${bytesLabel(e.related_files_size)}</summary><p>分析记录：${htmlEscape(e.history_available ? e.status : "尚无")} ${htmlEscape(e.error || e.skip_reason || "")}</p><table class="list"><thead><tr><th>候选源区间</th><th>弹幕数 / 峰值 / 突增倍数</th><th>阶段 / 失败</th></tr></thead><tbody>${e.candidates.map((c, i) => `<tr><td><button class="text-button" data-candidate-seek="${i}">${timecode(c.start)}–${timecode(c.end)}</button></td><td>${htmlEscape(c.danmaku_count ?? "—")} / ${htmlEscape(c.peak_density ?? "—")} / ${htmlEscape(c.burst_ratio ?? "—")}</td><td>${htmlEscape(c.state)} ${htmlEscape(c.reason || c.error || "")}<p>${stageTimings(c.timings)}</p></td></tr>`).join("") || '<tr><td colspan="3">没有已保存候选</td></tr>'}</tbody></table><div class="action-row"><button class="btn btn-secondary btn-sm" id="evidence-prev" ${review.evidenceOffset === 0 ? "disabled" : ""}>上一页候选</button><span>共 ${e.total} 个</span><button class="btn btn-secondary btn-sm" id="evidence-next" ${review.evidenceOffset + 20 >= e.total ? "disabled" : ""}>下一页候选</button></div>${e.segments.map((v) => `<details><summary>${htmlEscape(v.title)} · ${htmlEscape(v.stage || v.state)} ${htmlEscape(v.error_detail || v.error || "")}</summary><p>${stageTimings(v.timings)}</p><ul>${v.artifacts.map((a) => `<li>${htmlEscape(a.name)} · ${bytesLabel(a.size)}</li>`).join("") || "<li>尚无片段工件</li>"}</ul></details>`).join("")}</details>`;
}
function reviewMarkup(s, task, lines, initial) {
  const name = task ? sourceTitle(task.source_relpath) : "选择场次";
  const lifecycle = review.lifecycle,
    complete = !!lifecycle?.review_result;
  const sourceTools = recordingPickerHTML();
  const meta = task ? recordingMeta(task) : null;
  const header = `<header class="workbench-head"><div class="recording-heading"><div class="breadcrumb"><a href="#/library">录播库</a><span>/</span><button class="text-button" data-dialog="source-dialog">${htmlEscape(meta?.upName || name)}</button></div>${meta ? `<h1>${htmlEscape(meta.date ? `${meta.date} · ${meta.time}` : "录制时间未知")}</h1><p class="recording-summary"><span>房间 ${htmlEscape(meta.room || "未知")}</span><span id="recording-duration">时长读取中</span><span>${bytesLabel(meta.size)}</span><span>${review.segments.length} 个片段</span>${statusBadge(complete ? "已完成复核" : "未完成复核", complete ? "green" : "orange")}</p><details class="recording-file-details"><summary>源文件信息</summary><p>${htmlEscape(task.source_relpath)}</p></details>` : ""}</div><div class="head-actions"><button class="btn btn-primary" data-dialog="source-dialog">切换场次 / 管理</button>${meta?.room ? `<a class="btn btn-secondary" href="${pageLink("streamer", {room: meta.room})}">主播档案</a>` : ""}${task ? '<button id="review-analyze-now" class="btn btn-secondary">AI 分析本场录播</button><button class="btn btn-secondary" data-dialog="finish-dialog">整场复核</button>' : ""}<button id="review-refresh" class="icon-button" aria-label="刷新场次">${icon("refresh")}</button></div></header>`;
  if (!task)
    return (
      header +
      emptyState(
        pageParams().get("room")
          ? "没有找到该房间的源录播"
          : "暂无源 MP4，可在直播间配置录制",
      ) +
      dialogHTML("source-dialog", "选择场次", sourceTools)
    );
  const content = s
    ? `<form id="review-metadata-form" class="metadata-form"><h3>投稿资料</h3><label class="field">标题<input id="review-meta-title" maxlength="80" required value="${htmlEscape(s.title || "")}"></label><label class="field">简介<textarea id="review-meta-description" rows="4" maxlength="2000">${htmlEscape(s.description || "")}</textarea></label><label class="field">TAG<input id="review-meta-tags" value="${htmlEscape((s.tags || []).join("，"))}" placeholder="用逗号分隔，例如：直播切片，雪狐桑"></label><p class="hint">最多 10 个 TAG，每个不超过 20 字。保存投稿资料会同步待确认队列。</p><div class="action-row"><button id="review-metadata-generate" class="btn btn-secondary" type="button">根据当前片段生成文案</button><button id="review-metadata-save" class="btn btn-primary" type="submit">保存投稿资料</button><span id="review-metadata-status" role="status" class="muted">已保存的投稿资料</span></div><p class="hint">调用模型，仅根据当前已保存字幕生成建议；请先保存字幕与草稿。</p><div id="review-metadata-suggestion" aria-live="polite">${metadataSuggestionHTML(s)}</div><p class="hint">${s.publish_approval ? "已允许发布，投稿资料受保护。" : "只修改投稿资料，无需重新生成视频。"}</p></form><h3>内容与判断</h3>${definitionRows([
        ["片段状态", reviewStates[s.state] || s.state],
        ["人工依据", s.note || "暂无"],
        ["质量", s.quality ?? s.quality_score ?? "—"],
        ["完整度", s.completeness ?? s.completeness_score ?? "—"],
        ["置信度", s.confidence ?? "—"],
      ])}<div class="action-row"><button class="btn btn-secondary" data-dialog="points-dialog">编辑片段范围</button>${s.candidate_path ? '<button id="review-rejudge" class="btn btn-secondary">重新判断原候选</button>' : ""}</div>${s.latest_rejudgment ? `<details open><summary>最近重新判断结果</summary>${s.latest_rejudgment.results.map((r, i) => `<p>${htmlEscape(r.title)} · ${htmlEscape(r.judge_status)} · ${htmlEscape(r.quality_reason || r.judge_error)} ${r.suggested_trim ? `<button class="text-button" data-rejudge-range="${i}">将建议填入范围草稿</button>` : ""}</p>`).join("") || "<p>没有返回片段，仍需人工复核。</p>"}</details>` : ""}<details><summary>当前片段字幕样式</summary><div class="action-row"><a class="btn btn-secondary" href="#/settings?tab=subtitles">调整全局字幕设置</a><button id="review-style-default" class="btn btn-secondary">应用默认样式并使旧成片失效</button></div><p class="hint">应用后需要重新生成成片；也可在下方单独覆盖本片段样式。</p><label class="field">字幕样式 JSON<textarea id="review-style">${htmlEscape(JSON.stringify(s.subtitle_style || {}, null, 2))}</textarea></label><button id="review-style-save" class="btn btn-secondary">保存样式并使旧成片失效</button></details>`
    : "<p>先选定源范围并创建人工片段。</p>";
  return (
    header + '<section id="recording-analysis" class="recording-analysis" role="status" aria-live="polite"><strong id="analysis-title"></strong><p id="analysis-detail"></p><button id="analysis-resume" class="btn btn-primary btn-sm" hidden>恢复分析</button></section>' +
    `<div class="workbench-grid card ${review.largePlayer ? "large-player" : ""}"><section class="player-column"><div class="player-toolbar">${tabsHTML(
      [
        ["source", "源录播"],
        ["final", "当前成片"],
      ],
      review.media,
      "data-media-tab",
    )}<button id="review-large-player" class="btn btn-secondary btn-sm" aria-pressed="${review.largePlayer}">${review.largePlayer ? "返回并排" : "大画面"}</button></div><div class="player-body"><div id="source-media" ${review.media === "source" ? "" : "hidden"}><video id="review-source" data-task="${task.task_id}" data-segment="${s?.segment_id || ""}" controls preload="metadata" src="${htmlEscape(apiURL("/api/media/source/" + task.task_id))}"></video></div><div id="final-media" ${review.media === "final" ? "" : "hidden"}>${s?.final_valid ? `<video id="review-final" controls preload="metadata" src="${htmlEscape(apiURL("/api/media/final/" + s.segment_id + "?revision=" + s.revision))}"></video>` : emptyState("当前无有效成片，保存后需要重新生成")}</div><div class="density-empty"><span class="muted">暂无连续密度数据</span><button class="text-button" id="show-evidence">查看候选统计</button><div class="range-summary"><span id="source-range-start">${timecode(s?.source_start ?? 0)}</span><span>所选源区间</span><span id="source-range-end">${timecode(s?.source_end ?? 1)}</span></div></div><div class="range-controls"><label>源入点<input id="review-start" aria-label="源入点" value="${timecode(s?.source_start ?? 0)}"></label><button class="icon-button" id="review-in" aria-label="当前位置设为入点">${icon("clock")}</button><label>源出点<input id="review-end" aria-label="源出点" value="${timecode(s?.source_end ?? 1)}"></label><button class="icon-button" id="review-out" aria-label="当前位置设为出点">${icon("clock")}</button>${s ? '<button id="review-range-save" class="btn btn-secondary" aria-describedby="context-save">保存当前范围</button>' : ""}<button class="btn btn-ghost" data-dialog="points-dialog">${icon("scissors")}新建补选片段</button></div></div></section>
    <section class="editor-column">${tabsHTML(
      [
        ["content", "内容 / 投稿资料"],
        ["subtitles", "字幕"],
      ],
      review.tab,
      "data-review-tab",
    )}<div class="editor-body"><div class="section-heading"><h2>${htmlEscape(s?.title || "片段编辑")} <span id="review-draft-badge" class="badge orange">草稿未保存</span></h2><span class="hint">时间相对片段起点 ${icon("info-circle")}</span></div><div id="subtitle-panel" ${review.tab === "subtitles" ? "" : "hidden"}>${s ? `<div class="subtitle-empty" id="subtitle-empty" ${lines.length ? "hidden" : ""}>${s.state === "dropped" ? `<strong>当前片段已废弃</strong><p>${htmlEscape(s.drop_reason || "人工判断无需切片")}。源录播保留，本片段不再生成成片或发布。</p>` : `<strong>当前片段还没有字幕</strong><p>点击“本地模型识别字幕”自动转写当前片段，也可试听后手工添加。核对并保存字幕后才能生成成片。</p>`}</div><div class="subtitle-scroll" ${lines.length ? "" : "hidden"}><table class="list subtitle-table"><thead><tr><th>定位</th><th>开始时间</th><th>结束时间</th><th>字幕内容</th><th></th></tr></thead><tbody id="review-lines">${subtitleRows(lines)}</tbody></table></div><div class="subtitle-actions"><button id="review-transcribe" class="btn btn-primary btn-sm">本地模型识别字幕</button><button id="review-add-line" class="btn btn-secondary btn-sm">${icon("plus")}添加字幕行</button><button id="review-save" class="btn btn-primary">保存字幕</button></div><p class="hint subtitle-help">识别当前已保存的片段范围 · 结果填入草稿，核对后保存</p><p class="hint subtitle-help">点击行号定位字幕 · 时间从片段起点 00:00 计时</p><p class="hint save-note">${icon("info-circle")}保存字幕会使旧成片失效，需要重新生成</p>` : emptyState("选择或创建片段后编辑字幕")}</div><div id="content-panel" ${review.tab === "content" ? "" : "hidden"}>${content}<div class="evidence-section">${reviewEvidenceHTML()}</div></div><p id="review-message" role="status">${initial}</p></div></section></div>
    <section class="candidate-panel card"><h3>片段候选 <span class="muted">（共 ${review.segments.length} 个）</span></h3><div class="candidate-navigation"><button class="icon-button" id="candidate-prev" aria-label="向前滚动候选">${icon("chevron-left")}</button><div class="candidate-strip">${review.segments.map((x, i) => `<button class="candidate-card ${x.segment_id === s?.segment_id ? "selected" : ""}" data-segment="${x.segment_id}" aria-pressed="${x.segment_id === s?.segment_id}" title="${htmlEscape(x.title || "人工片段")}"><strong>${i + 1}. ${htmlEscape(x.title || "人工片段")}</strong><span class="num">${timecode(x.source_start)} – ${timecode(x.source_end)}</span>${statusBadge(x.publish_approval ? "已允许发布" : reviewStates[x.state] || x.state, x.publish_approval ? "green" : x.state === "dropped" ? "muted" : x.state === "awaiting_publish" ? "purple" : "blue")}</button>`).join("") || '<p id="candidate-empty" class="muted"></p>'}</div><button class="icon-button" id="candidate-next" aria-label="向后滚动候选">${icon("chevron-right")}</button></div><footer class="workbench-actions"><p class="hint">${icon("info-circle")}可在内容页查看 AI 判断与质量</p><div class="action-row"><span id="review-save-hint" class="muted" role="status"></span>${s ? '<button id="review-drop-open" class="btn btn-secondary">废弃当前片段</button><button id="review-finalize" class="btn btn-muted">重新生成成片</button><button id="review-approve" class="btn btn-primary">允许发布</button>' : ""}</div></footer><p id="review-job" role="status"></p></section>` +
    dialogHTML(
      "source-dialog",
      "场次与分析管理",
      sourceTools +
        `<div class="action-row"><button id="review-analyze" class="btn btn-secondary">AI 分析当前选中的录播</button><button id="review-cancel" class="btn btn-secondary">取消等待分析</button><button id="review-skip" class="btn btn-secondary">跳过本场分析</button></div><p class="hint">${review.autoAnalysis ? "自动 AI 切片已开启，新完成的录播会自动分析。" : "自动 AI 切片已关闭，录制结束只转为 MP4。点击分析按钮后才会将本场候选视频发送给 AI。"} 选择场次、打开页面均不会启动分析。<a href="#/settings?tab=processing">修改自动切片设置</a></p><p class="muted">${htmlEscape(review.analysisJob ? `当前场次分析：${reviewJobStates[review.analysisJob.status] || review.analysisJob.status}` : "当前场次暂无分析任务")}</p>`,
    ) +
    (s ? dialogHTML(
      "drop-dialog",
      "废弃当前片段",
      `<p>废弃“${htmlEscape(s.title || "当前片段")}”后，本片段停止成片和发布，并撤回尚未批准的成片暂存。源录播保留。</p><label class="field">废弃原因（选填）<input id="review-drop-reason" placeholder="例如：没有必要切片"></label><div class="dialog-actions"><button id="review-drop" class="btn btn-primary">确认废弃</button></div>`,
    ) : "") +
    dialogHTML(
      "points-dialog",
      "人工补选 / 编辑片段",
      `<label class="field">标题<input id="review-title" value="${htmlEscape(s?.title || "")}"></label><label class="field">人工补片依据<input id="review-note" value="${htmlEscape(s?.note || "")}" placeholder="为什么保留此区间"></label><p class="muted">入出点使用右侧填写的源时间。“新建人工片段”会增加一条候选；修改当前片段请使用“保存当前片段范围”。</p><div class="dialog-actions"><button id="review-create" class="btn btn-primary">新建人工片段</button>${s ? '<button id="review-range" class="btn btn-secondary">保存当前片段范围</button>' : ""}</div>`,
    ) +
    dialogHTML(
      "finish-dialog",
      "整场审核与回收",
      `<label><input type="checkbox" id="review-no-content">确认本场无可保留内容</label><div class="action-row"><button id="review-finish" class="btn btn-primary">完成整场审核</button></div><p id="source-review-message" role="status">${complete ? "审核记录已保存" : "整场尚未终审"}</p><div class="action-row"><span id="source-drag" draggable="true" tabindex="0">拖动当前源场次到回收区</span><button id="source-recycle-zone" class="btn btn-secondary">源包回收区（也可点击）</button></div><p class="muted">回收沿用整场审核与源包检查，后台会显示阻塞原因。</p>`,
    )
  );
}
function drawManualWorkbench() {
  captureRecordingPickerScroll();
  const main = document.getElementById("main");
  const s = review.segment,
    task = review.task;
  const preserveAnalysisPlayback = review.analysisPlaybackTask === task?.task_id;
  review.analysisPlaybackTask = null;
  const subtitleKey = draftKey(),
    styleKey = styleDraftKey(),
    viewEpoch = pageEpoch;
  const stillHere = () =>
    pageEpoch === viewEpoch &&
    pageKey() === "library" &&
    review.task?.task_id === task?.task_id &&
    review.segment?.segment_id === s?.segment_id;
  const mutateReview = async (path, body) => {
    const result = await reviewAPI(path, body);
    if (!stillHere()) throw new Error("View changed");
    return result;
  };
  let lines = s?.subtitles || [],
    draft = null;
  try {
    draft = s && JSON.parse(localStorage.getItem(subtitleKey));
  } catch {
    /* unavailable draft storage */
  }
  if (draft) lines = draft.lines;
  const transcript = !draft && s?.transcription_job?.status === "done" &&
    s.transcription_job.payload.revision === s.revision &&
    s.transcription_job.result?.lines?.length ? s.transcription_job.result.lines : null;
  if (transcript) lines = transcript;
  const initial = s?.state === "dropped" ? `已废弃：${s.drop_reason || "人工判断无需切片"}` : draft
    ? draft.revision === s.revision
      ? "已恢复未保存草稿"
      : "草稿版本与服务端不同，请核对后保存；原草稿已保留"
    : s && s.subtitles_revision === s.revision && lines.length
      ? "服务端已保存字幕版本"
      : s ? "等待补充 / 确认当前片段字幕" : "";
  review.unsaved = !!draft;
  review.styleUnsaved = false;
  review.metadataUnsaved = false;
  review.rangeUnsaved = false;
  main.innerHTML = reviewMarkup(s, task, lines, initial);
  arrangeReviewWorkbench();
  updateRecordingAnalysis();
  if (!s) updateReviewActions();
  restorePageScroll("library");
  bindDialogs();
  bindTabKeyboard(main);
  const largePlayer = document.getElementById("review-large-player");
  if (largePlayer) largePlayer.onclick = () => {
    review.largePlayer = !review.largePlayer;
    document.querySelector(".workbench-grid").classList.toggle("large-player", review.largePlayer);
    largePlayer.textContent = review.largePlayer ? "返回并排" : "大画面";
    largePlayer.setAttribute("aria-pressed", review.largePlayer);
  };
  const switchTab = (tab) => {
    review.tab = tab;
    const params = pageParams();
    params.set("mode", tab);
    history.replaceState(null, "", `#/library?${params}`);
    review.linkSignature = params.toString();
    document.getElementById("subtitle-panel").hidden = tab !== "subtitles";
    document.getElementById("content-panel").hidden = tab !== "content";
    document.getElementById("final-panel").hidden = tab !== "final";
    document.querySelector(".recording-workspace").dataset.mode = tab;
    document.querySelectorAll("[data-review-tab]").forEach((b) => {
      b.classList.toggle("active", b.dataset.reviewTab === tab);
      b.setAttribute("aria-selected", b.dataset.reviewTab === tab);
      b.tabIndex = b.dataset.reviewTab === tab ? 0 : -1;
    });
    if (tab === "final") document.querySelector('[data-media-tab="final"]')?.click();
    else document.querySelector('[data-media-tab="source"]')?.click();
    updateReviewActions();
  };
  document
    .querySelectorAll("[data-review-tab]")
    .forEach((b) => (b.onclick = () => switchTab(b.dataset.reviewTab)));
  document.querySelectorAll("[data-media-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        review.media = b.dataset.mediaTab;
        document.getElementById("source-media").hidden =
          review.media !== "source";
        document.getElementById("final-media").hidden =
          review.media !== "final";
        document.querySelectorAll("[data-media-tab]").forEach((t) => {
          t.classList.toggle("active", t === b);
          t.setAttribute("aria-selected", t === b);
        });
        document
          .querySelectorAll(".player-body video")
          .forEach((v) => v.pause());
      }),
  );
  if (task) {
    document.getElementById("show-evidence").onclick = () => {
      switchTab("content");
      document.getElementById("recording-evidence").open = true;
    };
    const strip = document.querySelector(".candidate-strip");
    document.getElementById("candidate-prev").onclick = () =>
      strip.scrollBy({ left: -300, behavior: "smooth" });
    document.getElementById("candidate-next").onclick = () =>
      strip.scrollBy({ left: 300, behavior: "smooth" });
    const selectedCard = strip.querySelector('.selected');
    if (selectedCard) strip.scrollLeft = Math.max(0,
      selectedCard.offsetLeft - strip.offsetLeft - (strip.clientWidth - selectedCard.clientWidth) / 2);
    for (const id of ["review-start", "review-end"])
      document.getElementById(id).oninput = () => {
        setMessage(
          id === "review-start" ? "source-range-start" : "source-range-end",
          document.getElementById(id).value,
        );
      };
  }
  document.getElementById("review-refresh").onclick = renderManualWorkbench;
  bindRecordingPicker();
  if (!task) return;
  document.getElementById("recording-evidence").ontoggle = (e) => {
    review.evidenceOpen = e.target.open;
  };
  document.getElementById("evidence-prev").onclick = () => {
    review.evidenceOffset = Math.max(0, review.evidenceOffset - 20);
    renderManualWorkbench();
  };
  document.getElementById("evidence-next").onclick = () => {
    review.evidenceOffset += 20;
    renderManualWorkbench();
  };
  document.querySelectorAll("[data-candidate-seek]").forEach(
    (button) =>
      (button.onclick = () => {
        const candidate =
          review.evidence.candidates[Number(button.dataset.candidateSeek)];
        if (
          typeof candidate.start === "number" &&
          Number.isFinite(candidate.start)
        )
          document.getElementById("review-source").currentTime =
            candidate.start;
      }),
  );
  const recycleCurrent = async () => {
    try {
      const out = await mutateReview(
        `/api/recordings/${task.task_id}/recycle`,
        {},
      );
      document.getElementById("source-review-message").textContent = out.queued
        ? `回收已排队：${out.job_id}`
        : out.reason;
    } catch (e) {
      if (!stillHere()) return;
      document.getElementById("source-review-message").textContent =
        `回收未排入：${e.message}`;
    }
  };
  document.getElementById("source-drag").ondragstart = (e) =>
    e.dataTransfer.setData("text/plain", `bilive-source:${task.task_id}`);
  const recycleZone = document.getElementById("source-recycle-zone");
  recycleZone.onclick = recycleCurrent;
  recycleZone.ondragover = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };
  recycleZone.ondrop = (e) => {
    e.preventDefault();
    if (
      e.dataTransfer.getData("text/plain") === `bilive-source:${task.task_id}`
    )
      recycleCurrent();
  };
  document.getElementById("review-finish").onclick = async () => {
    const e = document.getElementById("source-review-message");
    try {
      if (review.metadataUnsaved) throw new Error("请先保存投稿资料草稿");
      if (s && (review.unsaved || localStorage.getItem(subtitleKey)))
        throw new Error("请先保存当前字幕草稿");
      if (s && (review.styleUnsaved || localStorage.getItem(styleKey)))
        throw new Error("请先保存当前样式草稿");
      const out = await mutateReview(
        `/api/recordings/${task.task_id}/finish-review`,
        { no_content: document.getElementById("review-no-content").checked },
      );
      e.textContent = `审核已保存 · ${out.recycle_queued ? "回收已排队" : out.reason === 'retained_or_waiting_deadline' ? "按保留标记和清理期限处理，暂不自动回收" : "回收入队失败，可在回收页重试"}${out.detail ? " · " + out.detail : ""}`;
      document.getElementById("review-finish").disabled = true;
    } catch (err) {
      if (!stillHere()) return;
      const reasons = {
        'no-content conflicts with retained segments': '本场仍有保留的片段，不能确认“本场无可保留内容”。请取消勾选；如果确实不保留任何内容，请先废弃所有片段。',
        'all retained segments need a current approved final': '还有保留的片段未完成审核。请逐个检查，生成当前版本的成片并允许发布，或废弃不保留的片段。',
        'explicit no-content confirmation required': '本场没有片段。请确认确实没有可保留的内容后，勾选“确认本场无可保留内容”。',
        'unresolved candidates require explicit no-content confirmation': '还有未处理的候选。请先检查候选并补选需要保留的片段；确认没有可保留内容后，再勾选“确认本场无可保留内容”。',
        'source analysis is still active': '本场 AI 分析尚未结束，请等待分析完成后再审核。',
        'source has an active task': '本场还有排队中或正在执行的任务，请等待任务结束后再审核。',
      };
      e.textContent = `终审未完成：${reasons[err.message] || err.message}`;
    }
  };
  for (const id of ["review-analyze", "review-analyze-now", "analysis-resume"]) {
    const analyze = document.getElementById(id);
    analyze.disabled = !!(review.lifecycle?.review_result || (!recordingAnalysisState().resume && ["pending", "processing"].includes(review.analysisJob?.status)));
    analyze.onclick = async () => {
      const resuming = recordingAnalysisState().resume;
      review.analysisSubmitting = true;
      updateRecordingAnalysis();
      try {
        if (resuming) {
          const result = await mutateReview("/api/worker/wake", {});
          if (["paused", "unavailable", "stopping"].includes(result.state)) throw new Error(result.detail || "后台尚未启动，请稍后重试");
        } else {
          await mutateReview("/api/actions", {action: "start_analysis", target: task.task_id});
        }
        review.analysisSubmitting = false;
        await renderManualWorkbench();
      } catch (e) {
        review.analysisSubmitting = false;
        if (!stillHere()) return;
        updateRecordingAnalysis(`分析未能提交：${e.message}。请重试。`);
      }
    };
  }
  for (const [id, action] of [
    ["review-cancel", "cancel"],
    ["review-skip", "skip"],
  ]) {
    document.getElementById(id).onclick = async () => {
      try {
        await mutateReview(
          `/api/recordings/${task.task_id}/task/${action}`,
          {},
        );
        await renderManualWorkbench();
        document.getElementById("source-review-message").textContent =
          action === "skip"
            ? "已跳过分析，仍需整场人工审核。"
            : "等待分析已取消，源媒体保留。";
      } catch (e) {
        if (!stillHere()) return;
        document.getElementById("source-review-message").textContent =
          `操作未完成：${e.message}`;
      }
    };
  }
  const source = document.getElementById("review-source");
  source.onloadedmetadata = () => {
    setMessage("recording-duration", Number.isFinite(source.duration) ? `时长 ${timecode(Math.floor(source.duration))}` : "时长未知");
    const saved = review.playback,
      same =
        saved?.task === task.task_id && (preserveAnalysisPlayback || saved.segment === (s?.segment_id || ""));
    source.currentTime = same ? saved.time : s?.source_start || 0;
    if (same && !saved.paused && review.media === "source")
      source.play().catch(() => {});
  };
  const final = document.getElementById("review-final");
  if (final)
    final.onloadedmetadata = () => {
      const saved = review.playback;
      if (
        saved?.task === task.task_id &&
        saved.segment === s.segment_id &&
        saved.finalTime != null
      ) {
        final.currentTime = saved.finalTime;
        if (!saved.finalPaused && review.media === "final")
          final.play().catch(() => {});
      }
    };
  const setRangePoint = (id, value) => {
    const input = document.getElementById(id);
    input.value = timecode(value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  };
  document.getElementById("review-in").onclick = () =>
    setRangePoint("review-start", source.currentTime);
  document.getElementById("review-out").onclick = () =>
    setRangePoint("review-end", source.currentTime);
  const points = async (update) => {
    try {
      if (update && (review.unsaved || review.styleUnsaved || review.metadataUnsaved))
        throw new Error("请先保存字幕、样式和投稿资料草稿，再修改范围");
      const requested = {
        source_relpath: task.source_relpath,
        source_start: rangePoint("review-start", s?.source_start),
        source_end: rangePoint("review-end", s?.source_end),
        title: document.getElementById("review-title").value,
        note: document.getElementById("review-note").value,
        ...(update
          ? { segment_id: s.segment_id, expected_revision: s.revision }
          : {}),
      };
      const out = await mutateReview("/api/segments/point", requested);
      review.segment = out;
      await renderManualWorkbench();
      const saved = review.segment;
      const matches = saved?.segment_id === out.segment_id &&
        Math.abs(saved.source_start - requested.source_start) < .005 &&
        Math.abs(saved.source_end - requested.source_end) < .005;
      if (!matches) {
        reviewMessage(`范围未按输入保存。请求范围 ${timecode(requested.source_start)} – ${timecode(requested.source_end)}；请核对上方已保存范围后重试。`);
        return;
      }
      reviewMessage(`${update ? "当前片段范围已保存" : "补选片段已保存"}：${timecode(saved.source_start)} – ${timecode(saved.source_end)}。请检查内容，补充当前范围的字幕并生成成片。`);
    } catch (e) {
      if (!stillHere()) return;
      alert(e.message);
    }
  };
  document.getElementById("review-create").onclick = () => points(false);
  if (s) {
    const rangeChanged = () => {
      try {
        review.rangeUnsaved = Math.abs(rangePoint("review-start", s.source_start) - s.source_start) > .005 ||
          Math.abs(rangePoint("review-end", s.source_end) - s.source_end) > .005;
      } catch { review.rangeUnsaved = true; }
      updateReviewActions();
    };
    for (const id of ["review-start", "review-end"]) document.getElementById(id).oninput = rangeChanged;
    const rangeDraft = rangeDrafts.get(s.segment_id);
    if (rangeDraft) {
      document.getElementById("review-start").value = rangeDraft.start;
      document.getElementById("review-end").value = rangeDraft.end;
      rangeChanged();
    }
    document.getElementById("review-range-save").onclick = () => {
      const note = document.getElementById("review-note");
      if (!note.value.trim()) note.value = s.quality_reason || "人工调整当前片段范围";
      points(true);
    };
  }
  document.querySelectorAll("button.candidate-card[data-segment]").forEach(
    (b) =>
      (b.onclick = () => {
        captureRangeDraft();
        capturePlayback();
        review.segment = { segment_id: b.dataset.segment };
        renderManualWorkbench();
      }),
  );
  review.analysisInputs = analysisInputSnapshot();
  if (["pending", "processing"].includes(review.analysisJob?.status))
    analysisTimer = setTimeout(() => watchRecordingAnalysis(task.task_id, viewEpoch), 2000);
  if (!s) return;
  const advanceCandidate = async () => {
    const refreshed = await mutateReview(`/api/recordings/${task.task_id}/segments`);
    const ordered = refreshed.items.sort((a, b) => a.source_start - b.source_start);
    const index = ordered.findIndex(x => x.segment_id === s.segment_id);
    const next = [...ordered.slice(index + 1), ...ordered.slice(0, index)].find(candidateNeedsReview);
    review.segment = next || ordered.find(x => x.segment_id === s.segment_id && x.state !== "dropped") || ordered.find(x => x.state !== "dropped") || null;
    review.tab = next ? "content" : "subtitles";
    review.media = "source";
    await renderManualWorkbench();
    reviewMessage(next ? "决定已保存，已选择下一个未审核候选。" : "候选已处理完成，可以检查保留片段的字幕。");
  };
  document.getElementById("review-keep").onclick = async event => {
    event.currentTarget.disabled = true;
    reviewMessage("正在保存保留决定…");
    try {
      await mutateReview(`/api/segments/${s.segment_id}/keep`, {expected_revision: s.revision});
      await advanceCandidate();
    } catch (error) { if (stillHere()) { updateReviewActions(); reviewMessage(`保留未保存：${error.message}`); } }
  };
  const metadataKey = metadataDraftKey();
  document.getElementById('review-meta-tags').closest('label').insertAdjacentHTML('afterend', `<label class="field">投稿分区<input id="review-meta-tid" type="number" min="1" max="10000" value="${s.tid || ''}"></label>`);
  const selectionOption = (value, label) => `<option value="${htmlEscape(JSON.stringify(value))}">${htmlEscape(label)}</option>`;
  const topicLabel = t => `${t.name}${t.mission_id ? ' · 活动' : ''}`;
  const seasonLabel = t => `${t.title} / ${t.section_title}`;
  document.getElementById('review-meta-tid').closest('label').insertAdjacentHTML('afterend', `
    <div class="field"><label for="review-topic-keywords">搜索话题 / 活动</label><div class="action-row"><input id="review-topic-keywords" maxlength="100" placeholder="例如：搞笑研究所"><button id="review-topic-search" class="btn btn-secondary" type="button">搜索话题</button></div></div>
    <label class="field">参与话题<select id="review-meta-topic"><option value="">不参与话题</option>${s.topic ? selectionOption(s.topic, topicLabel(s.topic)) : ''}</select></label>
    <p id="review-topic-info" role="status" class="hint">话题占一个标签；活动收录和奖励以活动规则为准。</p>
    <label class="field">加入合集<select id="review-meta-season"><option value="">不加入合集</option>${s.season ? selectionOption(s.season, seasonLabel(s.season)) : ''}</select></label>
    <div class="action-row"><button id="review-season-load" class="btn btn-secondary" type="button">读取我的合集</button><span id="review-season-info" role="status" class="hint">投稿成功后加入所选合集。</span></div>`);
  const metadataControls = ["title", "description", "tags", "tid", "topic", "season"].map(key => document.getElementById(`review-meta-${key}`));
  if (s.topic) metadataControls[4].value = JSON.stringify(s.topic);
  if (s.season) metadataControls[5].value = JSON.stringify(s.season);
  let metadataRevision = s.revision;
  try {
    const draft = JSON.parse(localStorage.getItem(metadataKey));
    if (draft) {
      metadataControls.forEach((control, i) => {
        if (draft.values[i] == null) return;
        if (i >= 4 && draft.values[i] && ![...control.options].some(o => o.value === draft.values[i])) {
          const value = JSON.parse(draft.values[i]);
          control.insertAdjacentHTML('beforeend', selectionOption(value, i === 4 ? topicLabel(value) : seasonLabel(value)));
        }
        control.value = draft.values[i];
      });
      metadataRevision = draft.revision;
      review.metadataUnsaved = true;
      setMessage("review-metadata-status", draft.revision === s.revision ? "已恢复投稿资料草稿" : "草稿版本已变化，请核对并重新编辑后保存");
    }
  } catch {}
  metadataControls.forEach(control => control.oninput = () => {
    review.metadataUnsaved = true;
    metadataRevision = s.revision;
    try {
      localStorage.setItem(metadataKey, JSON.stringify({revision: s.revision, values: metadataControls.map(c => c.value)}));
      setMessage("review-metadata-status", "投稿资料未保存 · 草稿保存在此浏览器");
    } catch { setMessage("review-metadata-status", "投稿资料未保存 · 草稿存储不可用，请保留文本"); }
    updateReviewActions();
  });
  const topicInfo = async () => {
    const value = metadataControls[4].value;
    if (!value) return setMessage('review-topic-info', '不参与话题。');
    const topic = JSON.parse(value);
    setMessage('review-topic-info', '正在核对话题当前状态…');
    try {
      const info = await reviewAPI(`/api/publication/topics/${topic.id}?name=${encodeURIComponent(topic.name)}&mission_id=${topic.mission_id}`);
      if (!stillHere() || metadataControls[4].value !== value) return;
      setMessage('review-topic-info', [info.description, info.activity_description, info.tips,
        info.mission_id ? '活动关联不代表已获奖，请核对活动规则。' : '普通话题，无关联活动。'].filter(Boolean).join(' '));
    } catch (error) { if (stillHere() && metadataControls[4].value === value) setMessage('review-topic-info', `话题核对失败：${error.message}`); }
  };
  metadataControls[4].onchange = topicInfo;
  if (metadataControls[4].value && !s.publish_approval) topicInfo();
  const updateChoices = (control, items, label, key) => {
    const selected = control.value;
    const values = new Map(items.map(item => [key(item), item]));
    if (selected) { const item = JSON.parse(selected); values.set(key(item), item); }
    control.innerHTML = `<option value="">${control === metadataControls[4] ? '不参与话题' : '不加入合集'}</option>` + [...values.values()].map(item => selectionOption(item, label(item))).join('');
    control.value = selected;
  };
  document.getElementById('review-topic-search').onclick = async event => {
    const keywords = document.getElementById('review-topic-keywords').value.trim();
    if (!keywords) return setMessage('review-topic-info', '请输入话题关键词。');
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await reviewAPI(`/api/publication/topics?keywords=${encodeURIComponent(keywords)}`);
      if (!stillHere()) return;
      updateChoices(metadataControls[4], result.items, topicLabel, t => `${t.id}:${t.mission_id}:${t.name}`);
      setMessage('review-topic-info', `找到 ${result.items.length} 个话题，请从“参与话题”选择。${result.has_more ? '可输入更准确的关键词缩小结果。' : ''}`);
    } catch (error) { if (stillHere()) setMessage('review-topic-info', `搜索失败：${error.message}`); }
    finally { if (stillHere()) updateReviewActions(); }
  };
  document.getElementById('review-season-load').onclick = async event => {
    event.currentTarget.disabled = true;
    try {
      const result = await reviewAPI('/api/publication/seasons');
      if (!stillHere()) return;
      updateChoices(metadataControls[5], result.items, seasonLabel, t => `${t.id}:${t.section_id}`);
      setMessage('review-season-info', result.items.length ? '已读取，请选择合集和小节。' : '当前账号没有可用合集。');
    } catch (error) { if (stillHere()) setMessage('review-season-info', `读取失败：${error.message}`); }
    finally { if (stillHere()) updateReviewActions(); }
  };
  document.getElementById("review-metadata-suggestion").onclick = event => {
    if (!event.target.closest("#review-metadata-apply")) return;
    const job = s.metadata_job;
    if (s.active_job || s.publish_approval || s.state === "dropped" || review.unsaved || review.styleUnsaved ||
        job?.result?.revision !== s.revision || job?.payload?.revision !== s.revision) return;
    const metadata = job.result.metadata;
    metadataControls[0].value = metadata.title;
    metadataControls[1].value = metadata.description;
    metadataControls[2].value = metadata.tags.join("，");
    metadataControls[0].oninput();
    setMessage("review-metadata-status", "已采用文案建议 · 请核对修改后保存");
  };
  document.getElementById("review-metadata-form").onsubmit = async event => {
    event.preventDefault();
    const button = document.getElementById("review-metadata-save");
    try {
      if (review.unsaved || review.styleUnsaved) throw new Error("请先保存字幕和样式草稿，再保存投稿资料");
      if (metadataRevision !== s.revision) throw new Error("投稿资料草稿版本已变化，请核对并重新编辑后保存");
      button.disabled = true;
      await mutateReview(`/api/segments/${s.segment_id}/metadata`, {
        expected_revision: s.revision,
        title: metadataControls[0].value,
        description: metadataControls[1].value,
        tags: metadataControls[2].value.split(/[,，\n]/).map(t => t.trim()).filter(Boolean),
        tid: metadataControls[3].value ? Number(metadataControls[3].value) : null,
        topic: metadataControls[4].value ? JSON.parse(metadataControls[4].value) : null,
        season: metadataControls[5].value ? JSON.parse(metadataControls[5].value) : null,
      });
      localStorage.removeItem(metadataKey);
      await renderManualWorkbench();
      setMessage("review-metadata-status", "投稿资料已保存");
    } catch (error) {
      if (!stillHere()) return;
      updateReviewActions();
      setMessage("review-metadata-status", `保存失败：${error.message}。草稿保留。`);
    }
  };
  const styleEditor = document.getElementById("review-style");
  document.getElementById('review-style-default').onclick = async () => {
    try {
      if (review.unsaved || review.styleUnsaved || review.metadataUnsaved) throw new Error('请先保存字幕、样式和投稿资料草稿');
      const config = await reviewAPI('/api/config/summary');
      await mutateReview(`/api/segments/${s.segment_id}/style`, {
        expected_revision: s.revision, style: subtitleStyleFromSettings(config.settings.edit),
      });
      await renderManualWorkbench();
      reviewMessage('已应用全局字幕设置，请重新生成成片以更新字幕外观。');
    } catch (error) { if (stillHere()) reviewMessage(`应用失败：${error.message}`); }
  };
  try {
    const cached = JSON.parse(localStorage.getItem(styleKey));
    if (cached) {
      review.styleUnsaved = true;
      styleEditor.value = cached.style;
      reviewMessage(
        cached.revision === s.revision
          ? "已恢复未保存样式草稿"
          : "样式草稿版本冲突，请核对后保存",
      );
    }
  } catch {}
  styleEditor.oninput = () => {
    review.styleUnsaved = true;
    updateReviewActions();
    try {
      localStorage.setItem(
        styleKey,
        JSON.stringify({ revision: s.revision, style: styleEditor.value }),
      );
      reviewMessage("有未保存样式草稿");
    } catch {
      reviewMessage("草稿缓存不可用，请保留样式文本");
    }
  };
  document.getElementById("review-style-save").onclick = async () => {
    try {
      if (localStorage.getItem(subtitleKey))
        throw new Error("请先保存字幕草稿");
      await mutateReview(`/api/segments/${s.segment_id}/style`, {
        expected_revision: s.revision,
        style: JSON.parse(styleEditor.value),
      });
      localStorage.removeItem(styleKey);
      await renderManualWorkbench();
      reviewMessage("样式已保存，需要重烧成片");
    } catch (e) {
      if (!stillHere()) return;
      reviewMessage(`样式保存失败：${e.message}。草稿保留。`);
    }
  };
  document.querySelectorAll("[data-rejudge-range]").forEach(
    (b) =>
      (b.onclick = () => {
        const r = s.latest_rejudgment.results[Number(b.dataset.rejudgeRange)],
          trim = r.suggested_trim;
        if (
          typeof trim.trim_start !== "number" ||
          typeof trim.trim_end !== "number"
        ) {
          reviewMessage("判断区间无效，原编辑保留");
          return;
        }
        const start =
            Number(s.latest_rejudgment.candidate_start) +
            Number(trim.trim_start),
          end =
            Number(s.latest_rejudgment.candidate_start) + Number(trim.trim_end);
        if (
          !Number.isFinite(start) ||
          !Number.isFinite(end) ||
          start < s.latest_rejudgment.candidate_start ||
          end <= start ||
          end > s.latest_rejudgment.candidate_end + 0.01
        ) {
          reviewMessage("判断区间无效，原编辑保留");
          return;
        }
        setRangePoint("review-start", start);
        setRangePoint("review-end", end);
        showDialog("points-dialog");
        document.getElementById("review-title").value = r.title;
        document.getElementById("review-note").value =
          r.quality_reason || "根据重新判断结果人工选择";
        reviewMessage("建议已填入草稿，尚未保存范围或授权发布");
      }),
  );
  document.getElementById("review-drop-open").onclick = () => {
    document.getElementById("drop-dialog").showModal();
  };
  document.getElementById("review-drop").onclick = async () => {
    try {
      await mutateReview(`/api/segments/${s.segment_id}/drop`, {
        expected_revision: s.revision,
        reason: document.getElementById("review-drop-reason").value.trim() || "人工判断无需切片",
      });
      for (const key of [subtitleKey, styleKey, metadataDraftKey()]) localStorage.removeItem(key);
      await advanceCandidate();
    } catch (e) {
      if (!stillHere()) return;
      document.getElementById("drop-dialog").close();
      reviewMessage(e.message);
    }
  };
  document.getElementById("review-range").onclick = () => points(true);
  const tbody = document.getElementById("review-lines");
  tbody.oninput = writeReviewDraft;
  tbody.onclick = (e) => {
    const remove = e.target.closest("[data-delete-line]");
    if (remove && !remove.disabled) {
      remove.closest("tr").remove();
      writeReviewDraft();
    }
    if (e.target.closest("[data-subtitle-seek]") || (e.target.closest("tr") && !e.target.closest("input,textarea,button"))) {
      try {
        const relative = parseTimecode(
          e.target.closest("tr").querySelector("[data-start]").value,
        );
        if (relative >= s.source_end - s.source_start)
          throw new Error("开始时间必须在当前片段内");
        const video = review.media === "final" ? final : source;
        if (!video || video.readyState < 1)
          throw new Error("当前视频尚未就绪，请等待加载或切换到源录播");
        video.currentTime = relative + (review.media === "source" ? s.source_start : 0);
        reviewMessage(`已定位到第${e.target.closest("tr").rowIndex}行字幕`);
      } catch (error) {
        reviewMessage(`无法定位：${error.message}`);
      }
    }
  };
  const highlightSubtitle = video => {
    setMessage("context-position", timecode(video.currentTime));
    const relative = video.currentTime - (video === source ? s.source_start : 0);
    tbody.querySelectorAll("tr").forEach(row => {
      try {
        row.classList.toggle("playing", relative >= parseTimecode(row.querySelector("[data-start]").value) && relative < parseTimecode(row.querySelector("[data-end]").value));
      } catch { row.classList.remove("playing"); }
    });
  };
  source.ontimeupdate = () => highlightSubtitle(source);
  if (final) {
    final.ontimeupdate = () => highlightSubtitle(final);
    let savingPreview = false;
    const persistPreview = async () => {
      if (savingPreview || !final.played.length) return;
      const ranges = Array.from({length: final.played.length}, (_, i) => [final.played.start(i), final.played.end(i)]).filter(([a,b]) => b > a);
      if (!ranges.length) return;
      savingPreview = true;
      try {
        const result = await reviewAPI(`/api/segments/${s.segment_id}/preview`, {expected_revision:s.revision, ranges});
        if (stillHere()) setMessage('final-preview-status', `${result.complete ? '已完整预览' : '尚未完整预览'} · 预览记录不影响人工批准`);
      } catch (error) {
        if (stillHere()) setMessage('final-preview-status', `预览记录未保存：${error.message}。仍可人工批准。`);
      } finally { savingPreview = false; }
    };
    final.onpause = persistPreview;
    final.onended = persistPreview;
  }
  document.getElementById("review-add-line").onclick = () => {
    tbody.insertAdjacentHTML(
      "beforeend",
      subtitleRows([
        { start: 0, end: Math.min(1, s.source_end - s.source_start), text: "" },
      ]),
    );
    writeReviewDraft();
    const row = tbody.lastElementChild;
    row.querySelector("[data-text]").focus();
    row.scrollIntoView({ block: "nearest" });
  };
  document.getElementById("review-save").onclick = async () => {
    try {
      const key = subtitleKey;
      await mutateReview("/api/subtitles/save", {
        segment_id: s.segment_id,
        expected_revision: s.revision,
        duration: s.source_end - s.source_start,
        lines: collectReviewLines(),
      });
      localStorage.removeItem(key);
      await renderManualWorkbench();
      reviewMessage("字幕已保存，需要生成 / 重烧成片");
    } catch (e) {
      if (!stillHere()) return;
      reviewMessage(`保存失败：${e.message}。草稿保留。`);
    }
  };
  const submit = async (action) => {
    try {
      if (review.rangeUnsaved) throw new Error("请先保存当前范围，再执行后台任务");
      if (review.unsaved || localStorage.getItem(subtitleKey)) {
        reviewMessage("请先保存字幕草稿，再执行成片或发布");
        return;
      }
      if (review.styleUnsaved || localStorage.getItem(styleKey)) {
        reviewMessage("请先保存样式草稿");
        return;
      }
      if (action === "generate_metadata" && review.metadataUnsaved) {
        reviewMessage("请先保存当前文案草稿，再生成新的建议");
        return;
      }
      const j = await mutateReview("/api/actions", {
        action,
        target: s.segment_id,
        revision: s.revision,
      });
      s.active_job = { job_id: j.job_id, status: j.status, action };
      updateReviewActions();
      reviewMessage(action === "transcribe_subtitles"
        ? "已提交本地字幕识别，完成后会填入草稿。" : action === "generate_metadata"
        ? "已提交文案生成，结果仅作为建议，不自动保存。" : "后台任务已提交。");
      watchReviewJob(j.job_id);
    } catch (e) {
      if (!stillHere()) return;
      reviewMessage(e.message);
    }
  };
  document.getElementById("review-finalize").onclick = () => submit("finalize");
  document.getElementById("review-metadata-generate").onclick = () => submit("generate_metadata");
  document.getElementById("review-transcribe").onclick = () => {
    if (collectReviewLines(true).length &&
        !confirm("重新识别会替换当前字幕草稿，服务端已保存的字幕将保留到你点击保存。继续识别？")) return;
    submit("transcribe_subtitles");
  };
  document.getElementById("review-approve").onclick = () =>
    submit("approve_publish");
  if (document.getElementById("review-rejudge"))
    document.getElementById("review-rejudge").onclick = () =>
      submit("retry_judge");
  updateReviewActions();
  switchTab(review.tab);
  if (transcript) {
    writeReviewDraft();
    reviewMessage("本地识别完成，字幕已填入草稿。请试听核对后保存字幕。");
  }
  if (s.active_job) watchReviewJob(s.active_job.job_id);
}

async function watchReviewJob(id) {
  clearTimeout(reviewTimer);
  if (pageKey() !== "library") return;
  const epoch = pageEpoch,
    segmentId = review.segment?.segment_id;
  try {
    const j = await reviewAPI(`/api/actions/${id}`);
    if (
      pageKey() !== "library" ||
      epoch !== pageEpoch ||
      segmentId !== review.segment?.segment_id
    )
      return;
    setMessage(
      "review-job",
      `${actionLabels[j.action] || "后台任务"}：${reviewJobStates[j.status] || j.status}${j.progress?.reason ? " · " + j.progress.reason : ""}${j.result?.error ? " · " + j.result.error : ""}`,
    );
    setMessage("context-job", document.getElementById("review-job")?.textContent || "");
    if (["pending", "processing"].includes(j.status)) {
      review.segment.active_job = { job_id: id, status: j.status, action: j.action };
      updateReviewActions();
      reviewTimer = setTimeout(() => watchReviewJob(id), 2000);
    } else if (j.action === "generate_metadata") {
      review.segment.active_job = null;
      review.segment.metadata_job = {status: j.status, payload: j.payload, result: j.result};
      const suggestion = document.getElementById("review-metadata-suggestion");
      if (suggestion) suggestion.innerHTML = metadataSuggestionHTML(review.segment);
      updateReviewActions();
      reviewMessage(j.status === "done" ? "文案建议已生成。查看后可采用，原投稿资料保留。"
        : `文案生成失败：${j.result?.error || "请重试"}。原投稿资料保留。`);
    } else if (review.unsaved || review.styleUnsaved || review.metadataUnsaved || review.rangeUnsaved) {
      review.segment.active_job = null;
      updateReviewActions();
      reviewMessage("后台任务已结束；当前编辑保留，请保存草稿后刷新查看结果。");
    } else {
      await renderManualWorkbench();
      if (pageKey() === "library")
        reviewMessage(
          j.status === "done"
            ? j.action === "transcribe_subtitles"
              ? "本地识别完成，字幕已填入草稿。请试听核对后保存字幕。" : "任务完成"
            : `任务失败：${j.result?.error || "请查看处理记录"}`,
        );
    }
  } catch (e) {
    if (pageKey() === "library" && epoch === pageEpoch)
      reviewMessage(
        `任务查询失败：${e.message}。已提交任务仍保留，可刷新查询。`,
      );
  }
}
const streamerView = { room: "", offset: 0, recordings: [] };
async function renderStreamerProfiles() {
  clearTimeout(reviewTimer);
  const current = beginPage("streamer");
  if (pageParams().get("room")) streamerView.room = pageParams().get("room");
  const main = document.getElementById("main");
  main.innerHTML = pageHead("主播档案", "发布默认值与最近录播") + emptyState("正在读取主播…");
  const [rooms, recordings] = await Promise.all([
    reviewAPI("/api/rooms?page=1&size=100").catch(() => ({items: []})),
    readAllPages("/api/recordings", current).catch(() => []),
  ]);
  if (!current()) return;
  streamerView.recordings = sortedRecordings((recordings || []).map(recordingMeta), "newest");
  const profiles = new Map(streamerView.recordings.filter(r => r.room).map(r => [r.room, r.upName]));
  rooms.items.forEach(r => profiles.set(String(roomId(r)), roomName(r)));
  if (!streamerView.room) streamerView.room = profiles.keys().next().value || "";
  main.innerHTML = pageHead("主播档案", "发布默认值与最近录播") + `<div class="profile-layout"><aside class="profile-list">${[...profiles].map(([room, name]) => `<button class="recording-option ${room === streamerView.room ? "selected" : ""}" data-profile-room="${htmlEscape(room)}"><strong>${htmlEscape(name)}</strong><small class="muted"> · ${htmlEscape(room)}</small></button>`).join("") || '<p class="muted">暂无主播，可输入房间号读取。</p>'}<label class="field">房间号<input id="profile-room" type="number" min="1" value="${htmlEscape(streamerView.room)}"></label><button id="profile-load" class="btn btn-secondary btn-sm">读取档案</button></aside><div id="profile-content"></div></div>`;
  main.querySelectorAll("[data-profile-room]").forEach(button => button.onclick = () => {
    location.hash = pageLink("streamer", {room: button.dataset.profileRoom});
  });
  document.getElementById("profile-load").onclick = () => {
    streamerView.room = document.getElementById("profile-room").value;
    streamerView.offset = 0;
    loadStreamerProfile();
  };
  if (streamerView.room) await loadStreamerProfile();
}
async function loadStreamerProfile() {
  const room = streamerView.room,
    content = document.getElementById("profile-content");
  try {
    if (!/^\d+$/.test(room) || Number(room) <= 0)
      throw new Error("请输入有效的数字房间号");
    const [data, experience] = await Promise.all([
      reviewAPI(`/api/streamers/${room}/profile`),
      reviewAPI(
        `/api/streamers/${room}/experiences?offset=${streamerView.offset}&limit=20`,
      ),
    ]);
    if (pageKey() !== "streamer" || room !== streamerView.room) return;
    if (!content.isConnected) return;
    const p = data.profile;
    const recent = streamerView.recordings.filter(r => r.room === room).slice(0, 5);
    content.innerHTML = `<p id="profile-message" role="status"></p><section class="recent-recordings"><h2>${htmlEscape(p.display_name || `房间 ${room}`)} · 最近录播</h2>${recent.map(r => `<a href="${pageLink("library", {source_task_id: r.task_id})}">${htmlEscape(r.date ? `${r.date} ${r.time}` : r.name)} · 打开工作台</a>`).join("") || '<p class="muted">暂无本地录播</p>'}</section><div class="split review-split"><form id="profile-form" class="card" style="padding:16px"><h2>主播信息与发布默认值</h2><p>用于新片段；单条成片修改不覆盖本档案。</p>
      ${[
        ["display_name", "显示名"],
        ["aliases", "别名（逗号分隔）"],
        ["default_tags", "默认标签（逗号分隔）"],
        ["default_title_template", "默认标题模板（空值继承全局）"],
        ["default_tid", "默认投稿分区（空值继承全局）"],
      ]
        .map(
          ([key, label]) =>
            `<p><label>${label}<input style="width:100%" data-profile="${key}" value="${htmlEscape(Array.isArray(p[key]) ? p[key].join(",") : p[key])}"></label></p>`,
        )
        .join("")}
      <p><label for="profile-default-season">默认加入合集</label><select id="profile-default-season" style="width:100%"><option value="">不自动加入合集</option>${p.default_season ? `<option value="${htmlEscape(JSON.stringify(p.default_season))}" selected>${htmlEscape(p.default_season.title || `合集 ${p.default_season.id}`)} / ${htmlEscape(p.default_season.section_title || `小节 ${p.default_season.section_id}`)}</option>` : ''}</select></p>
      <button id="profile-season-load" type="button" class="btn btn-secondary btn-sm">读取我的合集</button><p id="profile-season-message" role="status" class="hint">用于该主播新建的手动切片和自动切片；单条切片可修改或清空。已有片段保留原选择，投稿前再次验证合集。</p>
      <p><label for="profile-subtitle-hotwords">字幕专名（每行一个）</label><textarea id="profile-subtitle-hotwords" style="width:100%;min-height:120px" data-profile="subtitle_hotwords" aria-describedby="subtitle-hotwords-help" placeholder="常见联动主播名&#10;角色名&#10;游戏名">${htmlEscape((p.subtitle_hotwords || []).join("\n"))}</textarea></p>
      <p id="subtitle-hotwords-help" class="hint">填写正确名称，自动切片和手动识别字幕共用；自动包含上方的主播显示名和别名。常用专名放在前面，避免填写大段说明。修改后用于下一次识别，已有字幕需重新识别。</p>
      ${[
        ["default_description", "默认简介"],
        ["approved_guidance", "已批准的人工指导"],
      ]
        .map(
          ([key, label]) =>
            `<p><label>${label}<textarea style="width:100%;min-height:65px" data-profile="${key}">${htmlEscape(p[key])}</textarea></label></p>`,
        )
        .join("")}
      ${[
        ["default_slice_options", "密度参数 JSON"],
        ["default_subtitle_style", "字幕样式 JSON"],
      ]
        .map(
          ([key, label]) =>
            `<p><label>${label}<textarea style="width:100%;min-height:100px" data-profile="${key}">${htmlEscape(JSON.stringify(p[key], null, 2))}</textarea></label></p>`,
        )
        .join("")}
      <p>密度键：burst_ratio、burst_window、burst_context、burst_merge_gap、burst_top_n。字幕键：font_name、font_size、margin_v、alignment、outline、primary_colour、outline_colour。空对象使用全局默认值。</p><button class="btn btn-primary" type="submit">保存档案</button></form>
      <div class="card" style="padding:16px"><h2>建议与证据</h2><p>${htmlEscape(data.evidence.message)}</p><p>有效 ${data.evidence.sample_size} · 正 ${data.evidence.positive_count} · 负 ${data.evidence.negative_count}</p><button id="profile-generate" class="btn btn-secondary">根据已有人工样本生成建议</button>
      ${data.recommendations.map((r) => `<article><p>${htmlEscape(r.changes?.approved_guidance)}</p><p>状态 ${htmlEscape(r.status)} · 样本 ${r.sample_size}</p><details><summary>查看证据 ID</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${htmlEscape(JSON.stringify(r.evidence_ids, null, 2))}</pre></details><button class="btn btn-secondary" data-apply="${htmlEscape(r.recommendation_id)}" ${r.status === "applied" ? "disabled" : ""}>人工应用建议</button></article>`).join("") || "<p>暂无建议。</p>"}
      <h2>审核经验（共 ${experience.total} 条）</h2>${experience.items.map((e) => `<article><p>${htmlEscape(e.experience_type)} · ${htmlEscape(e.source_rel_path)}</p><p>${htmlEscape(e.note)}</p><details><summary>查看源区间和内容证据</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${htmlEscape(JSON.stringify(e, null, 2))}</pre></details></article>`).join("") || "<p>暂无已保存的人工审核经验。</p>"}<div class="action-row"><button id="experience-prev" class="btn btn-secondary" ${streamerView.offset === 0 ? "disabled" : ""}>上一页</button><button id="experience-next" class="btn btn-secondary" ${streamerView.offset + 20 >= experience.total ? "disabled" : ""}>下一页</button></div></div></div>`;
    const profileForm = document.getElementById("profile-form");
    document.getElementById('profile-season-load').onclick = async (event) => {
      const button = event.currentTarget;
      const select = document.getElementById('profile-default-season');
      const message = document.getElementById('profile-season-message');
      const selected = select.value ? JSON.parse(select.value) : null;
      button.disabled = true;
      message.textContent = '正在读取当前账号的合集…';
      try {
        const data = await reviewAPI('/api/publication/seasons');
        if (!select.isConnected) return;
        const items = [...data.items];
        const current = selected && items.find(item => item.id === selected.id && item.section_id === selected.section_id);
        if (selected && !current) items.unshift(selected);
        select.innerHTML = '<option value="">不自动加入合集</option>' + items.map(item => `<option value="${htmlEscape(JSON.stringify(item))}">${htmlEscape(item.title || `合集 ${item.id}`)} / ${htmlEscape(item.section_title || `小节 ${item.section_id}`)}</option>`).join('');
        select.value = selected ? JSON.stringify(current || selected) : '';
        message.textContent = selected && !current
          ? '原选择已不在可用列表中，请重新选择或清空后保存。'
          : `已读取 ${data.items.length} 个可选小节。选择后点击“保存档案”。`;
      } catch (error) {
        if (message.isConnected) message.textContent = `读取失败：${error.message}`;
      } finally { button.disabled = false; }
    };
    const publications = data.recent_published || [];
    profileForm.parentElement.insertAdjacentHTML('beforebegin', `<section class="recent-recordings"><h2>最近发布</h2>${publications.map(item => `<a href="https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}" target="_blank" rel="noopener">${htmlEscape(item.title)} · ${beijingTime(item.published_at || Date.parse(item.updated_at)/1000)}</a>`).join('') || '<p class="muted">暂无成功发布记录</p>'}</section>`);
    const advanced = document.createElement("details");
    advanced.innerHTML = '<summary>高级覆盖与人工指导</summary>';
    for (const key of ["approved_guidance", "default_slice_options", "default_subtitle_style"]) {
      advanced.append(profileForm.querySelector(`[data-profile="${key}"]`).closest("p"));
    }
    const parameterHelp = [...profileForm.querySelectorAll("p")].find(p => p.textContent.startsWith("密度键："));
    if (parameterHelp) advanced.append(parameterHelp);
    profileForm.insertBefore(advanced, profileForm.querySelector('button[type="submit"]'));
    const action = async (callback) => {
      try {
        await callback();
        await loadStreamerProfile();
        document.getElementById("profile-message").textContent = "已保存。";
      } catch (e) {
        document.getElementById("profile-message").textContent =
          `操作失败：${e.message}`;
      }
    };
    document.getElementById("profile-form").onsubmit = (e) => {
      e.preventDefault();
      action(async () => {
        const payload = {};
        document.querySelectorAll("[data-profile]").forEach((el) => {
          const key = el.dataset.profile,
            value = el.value;
          payload[key] = key === "subtitle_hotwords"
            ? value.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
            : ["aliases", "default_tags"].includes(key)
            ? value
                .split(/[,，]/)
                .map((s) => s.trim())
                .filter(Boolean)
            : key.endsWith("options") || key.endsWith("style")
              ? JSON.parse(value)
              : key === 'default_tid' ? (value ? Number(value) : null) : value;
        });
        const season = document.getElementById('profile-default-season').value;
        payload.default_season = season ? JSON.parse(season) : null;
        await reviewAPI(`/api/streamers/${room}/profile`, payload);
      });
    };
    document.getElementById("profile-generate").onclick = () =>
      action(() =>
        reviewAPI(`/api/streamers/${room}/recommendations/generate`, {}),
      );
    document
      .querySelectorAll("[data-apply]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            action(() =>
              reviewAPI(
                `/api/streamers/${room}/recommendations/${b.dataset.apply}/apply`,
                {},
              ),
            )),
      );
    document.getElementById("experience-prev").onclick = () => {
      streamerView.offset -= 20;
      loadStreamerProfile();
    };
    document.getElementById("experience-next").onclick = () => {
      streamerView.offset += 20;
      loadStreamerProfile();
    };
  } catch (e) {
    if (!content.isConnected) return;
    content.innerHTML = `<p role="alert">档案不可用：${htmlEscape(e.message)}</p>`;
  }
}
