/* A single segment's posting draft. Saving never activates the upload queue. */
let submissionTimer;
let submissionView = null;
const submissionFields = ['title', 'description', 'tags', 'tid', 'topic', 'season', 'dynamic'];
const submissionSourceId = s => btoa(Array.from(new TextEncoder().encode(s.source_relpath.replaceAll('\\', '/')), b => String.fromCharCode(b)).join('')).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
const submissionDraftKey = s => `bilive:metadata:${submissionSourceId(s)}:${s.segment_id}`;
const submissionReturnLink = s => pageLink('library', {source_task_id: submissionSourceId(s), segment_id: s.segment_id, mode: 'final'});
const submissionOption = (value, label) => `<option value="${htmlEscape(JSON.stringify(value))}">${htmlEscape(label)}</option>`;
const submissionTopicLabel = t => `${t.name}${t.mission_id ? ' · 活动' : ''}`;
const submissionSeasonLabel = s => `${s.title} / ${s.section_title}`;

function submissionSummaryHTML(s) {
  return `<section class="submission-summary"><h3>投稿资料</h3><strong>${htmlEscape(s.title || '尚未填写标题')}</strong><p>${htmlEscape(s.description || '尚未填写简介')}</p><p class="hint">${htmlEscape((s.tags || []).join(' · ') || '尚未填写标签')}${s.topic ? ` · #${htmlEscape(s.topic.name)}#` : ''}</p>${s.season ? `<p class="hint">合集：${htmlEscape(submissionSeasonLabel(s.season))}</p>` : ''}<p class="hint">${s.publish_approval ? '已提交的资料可在发布队列查看。' : '确认成片后点击“去投稿”，检查封面并完善投稿资料。'}</p></section>`;
}

async function renderSubmission() {
  clearTimeout(submissionTimer);
  const current = beginPage('submission');
  const main = document.getElementById('main');
  submissionView = null;
  main.innerHTML = pageHead('视频投稿', '填写投稿资料，确认后提交') + emptyState('正在读取成片…');
  const id = pageParams().get('segment_id');
  if (!id) {
    main.innerHTML = pageHead('视频投稿') + emptyState('请从成片确认或发布队列选择一条切片。') + '<a class="btn btn-primary" href="#/publish">打开发布队列</a>';
    return;
  }
  try {
    const s = await reviewAPI(`/api/segments/${encodeURIComponent(id)}`);
    if (!current()) return;
    const view = submissionView = {segment: s, current, busy: false, dirty: false, draftRevision: s.revision};
    drawSubmission(view);
  } catch (error) { if (current()) pageError('视频投稿', error, renderSubmission); }
}

function drawSubmission(view) {
  const s = view.segment;
  const meta = recordingMeta({source_relpath: s.source_relpath});
  document.getElementById('main').innerHTML = `
    <div class="submission-shell">
      <nav class="submission-breadcrumb" aria-label="当前位置"><a href="#/publish">发布队列</a><span>/</span><span aria-current="page">视频投稿</span></nav>
      ${pageHead('视频投稿', '确认当前成片，完善投稿资料', `<a class="btn btn-secondary" href="${submissionReturnLink(s)}">${icon('arrow-left')}返回成片检查</a>`)}
      <div id="submission-notice" role="status"></div>
      <div class="submission-layout">
        <form id="submission-form" class="submission-form">
          <section class="card submission-section"><div class="submission-section-title"><h2>视频文件</h2><span class="badge ${s.final_valid ? 'green' : 'orange'}">${s.final_valid ? '成片已就绪' : '成片待生成'}</span></div>
            <div class="submission-file">${icon('video')}<div><strong>${htmlEscape(s.title || '当前切片')}</strong><p>${htmlEscape(meta.upName)} · ${timecode(s.source_end - s.source_start, true)} · ${timecode(s.source_start)}–${timecode(s.source_end)}</p><p class="hint">${htmlEscape(meta.name)}</p></div></div>
          </section>
          <section class="card submission-section"><div class="submission-section-title"><h2>基本信息</h2><span class="muted">资料已继承当前切片</span></div>
            <fieldset id="submission-fields">
              <div class="submission-field"><label>视频封面</label><div><div class="submission-cover">${s.cover_path ? `<img src="${htmlEscape(apiURL(`/api/segments/${encodeURIComponent(s.segment_id)}/cover`))}?revision=${s.revision}" alt="当前投稿封面">` : '<span class="muted">生成成片后显示封面</span>'}</div><p class="hint">使用当前成片生成的封面。</p></div></div>
              <div class="submission-field"><label for="submission-title">标题 <span class="required">*</span></label><div><input id="submission-title" required maxlength="80" placeholder="填写视频标题"><p class="submission-counter" id="submission-title-count"></p></div></div>
              <div class="submission-field"><label for="submission-tid">分区 <span class="required">*</span></label><div><input id="submission-tid" type="number" min="1" max="10000" step="1" required placeholder="填写 B 站分区编号"><p class="hint">沿用当前切片分区，可修改编号。</p></div></div>
              <div class="submission-field"><label for="submission-tags">标签</label><div><input id="submission-tags" placeholder="用逗号分隔，例如：直播切片，日常"><div class="submission-tag-preview" id="submission-tag-preview"></div><p class="hint" id="submission-tag-count"></p></div></div>
              <div class="submission-field"><label for="submission-description">简介</label><div><textarea id="submission-description" rows="6" maxlength="2000" placeholder="介绍这段切片的内容"></textarea><p class="submission-counter" id="submission-description-count"></p></div></div>
              <div class="submission-field"><label for="submission-topic">话题 / 活动</label><div><div class="submission-search"><input id="submission-topic-keywords" maxlength="100" placeholder="输入话题关键词"><button id="submission-topic-search" type="button" class="btn btn-secondary">搜索</button></div><select id="submission-topic"><option value="">不参与话题</option>${s.topic ? submissionOption(s.topic, submissionTopicLabel(s.topic)) : ''}</select><p class="hint" id="submission-topic-info" role="status">话题占一个标签，活动收录以活动规则为准。</p></div></div>
              <div class="submission-field"><label for="submission-season">加入合集</label><div><div class="submission-search"><select id="submission-season"><option value="">不加入合集</option>${s.season ? submissionOption(s.season, submissionSeasonLabel(s.season)) : ''}</select><button id="submission-season-load" type="button" class="btn btn-secondary">读取合集</button></div><p class="hint" id="submission-season-info" role="status">投稿成功后加入所选合集和小节。</p></div></div>
              <details class="submission-more" open><summary>更多设置</summary><div class="submission-field"><label for="submission-dynamic">粉丝动态</label><div><textarea id="submission-dynamic" rows="3" maxlength="233" placeholder="随视频发布的动态文案（选填）"></textarea><p class="submission-counter" id="submission-dynamic-count"></p></div></div></details>
            </fieldset>
          </section>
          <section class="card submission-section submission-suggestion"><div class="submission-section-title"><div><h2>文案助手</h2><p class="hint">根据当前已保存字幕生成建议，采用后仍可修改。</p></div><button id="submission-generate" type="button" class="btn btn-secondary">${icon('file-text')}生成文案建议</button></div><div id="submission-suggestion" aria-live="polite">${metadataSuggestionHTML(s)}</div></section>
          <footer class="submission-footer"><div><strong id="submission-save-state" role="status">投稿资料已保存</strong><p class="hint" id="submission-feedback" role="status">保存草稿后可稍后继续，点击提交才会开始后台投稿。</p></div><div class="action-row"><button id="submission-save" type="button" class="btn btn-secondary">保存草稿</button><button id="submission-submit" type="submit" class="btn btn-primary">${icon('cloud-upload')}提交投稿</button></div></footer>
        </form>
        <aside class="submission-preview card"><h2>成片预览</h2>${s.final_valid ? `<video controls preload="metadata" src="${htmlEscape(apiURL(`/api/media/final/${encodeURIComponent(s.segment_id)}?revision=${s.revision}`))}" ${s.cover_path ? `poster="${htmlEscape(apiURL(`/api/segments/${encodeURIComponent(s.segment_id)}/cover`))}?revision=${s.revision}"` : ''}></video>` : emptyState('请返回工作台生成当前版本成片')}<div class="submission-preview-info"><strong id="submission-preview-title">${htmlEscape(s.title)}</strong><p>${htmlEscape(meta.upName)}</p><p>${timecode(s.source_end - s.source_start, true)} · 当前成片</p></div><a href="${submissionReturnLink(s)}">${icon('arrow-left')}返回检查字幕与画面</a><a href="${pageLink('publish', {segment_id: s.segment_id})}">${icon('list-details')}查看发布进度</a></aside>
      </div>
    </div>`;
  const controls = submissionFields.map(key => document.getElementById(`submission-${key}`));
  const values = [s.title || '', s.description || '', (s.tags || []).join('，'), s.tid || '', s.topic ? JSON.stringify(s.topic) : '', s.season ? JSON.stringify(s.season) : '', s.dynamic || ''];
  controls.forEach((control, i) => { control.value = values[i]; });
  view.controls = controls;
  view.savedValues = controls.map(c => c.value);
  try {
    const draft = JSON.parse(localStorage.getItem(submissionDraftKey(s)));
    if (draft && !s.publish_approval && s.state !== 'dropped') {
      controls.forEach((control, i) => {
        if (draft.values[i] == null) return;
        if ([4, 5].includes(i) && draft.values[i] && ![...control.options].some(o => o.value === draft.values[i])) {
          const value = JSON.parse(draft.values[i]);
          control.insertAdjacentHTML('beforeend', submissionOption(value, i === 4 ? submissionTopicLabel(value) : submissionSeasonLabel(value)));
        }
        control.value = draft.values[i];
      });
      view.draftRevision = draft.revision;
      view.dirty = controls.some((c, i) => c.value !== view.savedValues[i]);
    }
  } catch { /* Invalid local draft does not replace the server copy. */ }
  controls.forEach(control => {
    control.addEventListener('input', () => submissionChanged(view));
    control.addEventListener('change', () => submissionChanged(view));
  });
  document.getElementById('submission-topic').addEventListener('change', () => submissionTopicInfo(view));
  document.getElementById('submission-topic-search').onclick = () => submissionLoadChoices(view, 'topic');
  document.getElementById('submission-topic-keywords').onkeydown = event => {
    if (event.key === 'Enter') { event.preventDefault(); document.getElementById('submission-topic-search').click(); }
  };
  document.getElementById('submission-season-load').onclick = () => submissionLoadChoices(view, 'season');
  document.getElementById('submission-save').onclick = () => runSubmission(view, 'save');
  document.getElementById('submission-generate').onclick = () => runSubmission(view, 'generate_metadata');
  document.getElementById('submission-form').onsubmit = event => { event.preventDefault(); runSubmission(view, 'approve_publish'); };
  document.getElementById('submission-suggestion').onclick = event => {
    if (!event.target.closest('#review-metadata-apply') || submissionLocked(view) || view.draftRevision !== view.segment.revision) return;
    const job = view.segment.metadata_job;
    if (job?.payload?.revision !== view.segment.revision || job?.result?.revision !== view.segment.revision) return;
    const metadata = job.result.metadata;
    controls[0].value = metadata.title;
    controls[1].value = metadata.description;
    controls[2].value = metadata.tags.join('，');
    submissionChanged(view);
    setMessage('submission-feedback', '文案建议已填入草稿，请核对后保存。');
  };
  updateSubmission(view);
  if (s.active_job) watchSubmissionJob(view, s.active_job.job_id);
}

function submissionLocked(view) {
  const s = view.segment;
  return !!(view.busy || s.publish_approval || s.active_job || s.state === 'dropped');
}

function submissionChanged(view) {
  view.dirty = view.controls.some((c, i) => c.value !== view.savedValues[i]);
  try {
    if (view.dirty) localStorage.setItem(submissionDraftKey(view.segment), JSON.stringify({revision: view.draftRevision, values: view.controls.map(c => c.value)}));
    else localStorage.removeItem(submissionDraftKey(view.segment));
  } catch { setMessage('submission-feedback', '浏览器草稿存储不可用，请保存草稿后再离开。'); }
  updateSubmission(view);
}

function updateSubmission(view) {
  if (!view.current()) return;
  const s = view.segment, locked = submissionLocked(view), stale = view.draftRevision !== s.revision;
  document.getElementById('submission-fields').disabled = locked;
  for (const id of ['submission-save', 'submission-submit', 'submission-generate']) document.getElementById(id).disabled = locked || stale;
  document.getElementById('submission-save').disabled ||= !view.dirty;
  document.getElementById('submission-submit').disabled ||= !s.final_valid;
  document.getElementById('submission-generate').disabled ||= s.subtitles_revision !== s.revision || !s.subtitles?.length;
  const apply = document.getElementById('review-metadata-apply');
  if (apply) apply.disabled = locked || stale;
  const noticeEl = document.getElementById('submission-notice');
  noticeEl.innerHTML = s.publish_approval ? notice('这条切片已提交，投稿资料已锁定。可在发布队列查看进度。')
    : s.state === 'dropped' ? notice('这条切片已废弃，无法投稿。', 'warning')
    : stale ? notice('切片版本已变化，请对照当前成片核对恢复的草稿。<button type="button" class="text-button" id="submission-rebase">已核对，继续编辑这些资料</button>', 'warning')
    : !s.final_valid ? notice('当前版本还没有有效成片，可以先保存投稿草稿，再返回工作台生成成片。', 'warning') : '';
  const rebase = document.getElementById('submission-rebase');
  if (rebase) { rebase.disabled = locked; rebase.onclick = () => { view.draftRevision = s.revision; view.savedValues = []; submissionChanged(view); }; }
  setMessage('submission-save-state', view.busy ? '正在处理…' : s.publish_approval ? '已提交投稿' : s.active_job ? '后台任务处理中' : view.dirty ? '草稿尚未保存到项目' : '投稿资料已保存');
  const lengths = {title: 80, description: 2000, dynamic: 233};
  for (const [key, max] of Object.entries(lengths)) setMessage(`submission-${key}-count`, `${Array.from(document.getElementById(`submission-${key}`).value).length} / ${max}`);
  const tags = [...new Set(document.getElementById('submission-tags').value.split(/[,，\n]/).map(t => t.trim()).filter(Boolean))];
  const topic = document.getElementById('submission-topic').value;
  const count = topic ? new Set([...tags, JSON.parse(topic).name]).size : tags.length;
  setMessage('submission-tag-count', `${count} / 10 个标签${topic ? '（含所选话题）' : ''} · 每个最多 20 字`);
  document.getElementById('submission-tag-preview').innerHTML = tags.map(tag => `<span>${htmlEscape(tag)}</span>`).join('');
  setMessage('submission-preview-title', document.getElementById('submission-title').value || '未填写标题');
}

async function submissionTopicInfo(view) {
  const value = document.getElementById('submission-topic').value;
  if (!value) return setMessage('submission-topic-info', '不参与话题。');
  const topic = JSON.parse(value);
  try {
    setMessage('submission-topic-info', '正在核对话题当前状态…');
    const info = await reviewAPI(`/api/publication/topics/${topic.id}?name=${encodeURIComponent(topic.name)}&mission_id=${topic.mission_id}`);
    if (!view.current() || document.getElementById('submission-topic').value !== value) return;
    setMessage('submission-topic-info', [info.description, info.activity_description, info.tips].filter(Boolean).join(' ') || '话题已读取，活动收录以活动规则为准。');
  } catch (error) { if (view.current()) setMessage('submission-topic-info', `话题核对失败：${error.message}`); }
}

async function submissionLoadChoices(view, kind) {
  if (submissionLocked(view)) return;
  const topic = kind === 'topic', keywords = document.getElementById('submission-topic-keywords').value.trim();
  if (topic && !keywords) return setMessage('submission-topic-info', '请输入话题关键词。');
  const button = document.getElementById(topic ? 'submission-topic-search' : 'submission-season-load');
  button.disabled = true;
  try {
    const result = await reviewAPI(topic ? `/api/publication/topics?keywords=${encodeURIComponent(keywords)}` : '/api/publication/seasons');
    if (!view.current()) return;
    const control = document.getElementById(`submission-${kind}`), selected = control.value;
    const key = item => topic ? `${item.id}:${item.mission_id}:${item.name}` : `${item.id}:${item.section_id}`;
    const items = new Map(result.items.map(item => [key(item), item]));
    if (selected) { const item = JSON.parse(selected); items.set(key(item), item); }
    control.innerHTML = `<option value="">${topic ? '不参与话题' : '不加入合集'}</option>` + [...items.values()].map(item => submissionOption(item, topic ? submissionTopicLabel(item) : submissionSeasonLabel(item))).join('');
    control.value = selected;
    setMessage(`submission-${kind}-info`, result.items.length ? `已读取 ${result.items.length} 个${topic ? '话题' : '合集小节'}，请选择。${result.has_more ? '可缩小关键词查看更多结果。' : ''}` : `当前没有匹配的${topic ? '话题' : '合集'}。`);
  } catch (error) { if (view.current()) setMessage(`submission-${kind}-info`, `读取失败：${error.message}`); }
  finally { if (view.current()) button.disabled = submissionLocked(view); }
}

async function saveSubmission(view) {
  if (view.draftRevision !== view.segment.revision) throw new Error('请先核对当前版本的草稿');
  if (!view.dirty) return;
  const values = view.controls.map(c => c.value);
  let updated;
  try { updated = await reviewAPI(`/api/segments/${encodeURIComponent(view.segment.segment_id)}/metadata`, {
    expected_revision: view.segment.revision, title: values[0], description: values[1],
    tags: values[2].split(/[,，\n]/).map(t => t.trim()).filter(Boolean), tid: values[3] ? Number(values[3]) : null,
    topic: values[4] ? JSON.parse(values[4]) : null, season: values[5] ? JSON.parse(values[5]) : null, dynamic: values[6],
  });
    if ((updated.dynamic || '') !== values[6]) throw new Error('当前工作台服务尚未支持动态文案，请更新并重启服务后重试');
  } catch (error) {
    // Keep the draft while refreshing a changed/protected server revision.
    try {
      const fresh = await reviewAPI(`/api/segments/${encodeURIComponent(view.segment.segment_id)}`);
      if (view.current()) view.segment = fresh;
    } catch { /* Original save error is the useful error to display. */ }
    throw error;
  }
  if (!view.current()) return;
  view.segment = updated;
  view.draftRevision = updated.revision;
  view.savedValues = values;
  view.dirty = false;
  try { localStorage.removeItem(submissionDraftKey(updated)); } catch { /* Server save succeeded. */ }
  document.getElementById('submission-suggestion').innerHTML = metadataSuggestionHTML(updated);
}

async function runSubmission(view, action) {
  if (submissionLocked(view)) return;
  const form = document.getElementById('submission-form');
  if (!form.reportValidity()) return;
  view.busy = true;
  updateSubmission(view);
  try {
    await saveSubmission(view);
    if (!view.current()) return;
    if (action === 'save') { setMessage('submission-feedback', '草稿已保存到项目，尚未启动上传。'); return; }
    if (action === 'approve_publish' && !view.segment.final_valid) throw new Error('请先返回工作台生成当前版本成片');
    const job = await reviewAPI('/api/actions', {action, target: view.segment.segment_id, revision: view.segment.revision});
    if (!view.current()) return;
    view.segment.active_job = {...job, action};
    setMessage('submission-feedback', action === 'approve_publish' ? '发布请求已保存，等待后台处理。可在发布队列继续查看。' : '正在生成文案建议…');
    watchSubmissionJob(view, job.job_id);
  } catch (error) {
    if (action !== 'save') {
      try {
        const fresh = await reviewAPI(`/api/segments/${encodeURIComponent(view.segment.segment_id)}`);
        if (view.current()) view.segment = fresh;
      } catch { /* Preserve the action error and the current draft. */ }
    }
    if (view.current()) setMessage('submission-feedback', `未完成：${error.message}。当前填写内容已保留。`);
  }
  finally { view.busy = false; if (view.current()) updateSubmission(view); }
}

async function watchSubmissionJob(view, id) {
  clearTimeout(submissionTimer);
  if (!view.current()) return;
  try {
    const job = await reviewAPI(`/api/actions/${encodeURIComponent(id)}`);
    if (!view.current()) return;
    if (['pending', 'processing'].includes(job.status)) {
      submissionTimer = setTimeout(() => watchSubmissionJob(view, id), 2000);
      return;
    }
    view.segment.active_job = null;
    if (job.action === 'approve_publish' && job.status === 'done') {
      location.hash = pageLink('publish', {segment_id: view.segment.segment_id});
      return;
    }
    if (job.action === 'generate_metadata') {
      view.segment.metadata_job = {status: job.status, payload: job.payload, result: job.result};
      document.getElementById('submission-suggestion').innerHTML = metadataSuggestionHTML(view.segment);
    } else {
      view.segment = await reviewAPI(`/api/segments/${encodeURIComponent(view.segment.segment_id)}`);
      if (!view.current()) return;
    }
    setMessage('submission-feedback', job.status === 'done' ? '文案建议已生成，可核对后采用。' : `任务未完成：${job.result?.error || '请检查后重试'}`);
    updateSubmission(view);
  } catch (error) {
    if (view.current()) {
      setMessage('submission-feedback', `暂时无法读取进度：${error.message}。后台任务仍会继续。`);
      submissionTimer = setTimeout(() => watchSubmissionJob(view, id), 5000);
    }
  }
}
