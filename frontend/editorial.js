/* Human feedback is separate from model notes and publication copy. */
const editorialDraftKey = s => `bilive:editorial:${s.segment_id}`;
const editorialKinds = {content: '内容评价', boundary: '选段范围 / 前情', understanding: '理解或文案事实', publication: '发布取舍，与好坏无关', technical: '技术问题', duplicate: '重复版本', mixed: '多种原因，暂不判断好坏'};
const editorialKindOptions = selected => Object.entries(editorialKinds).map(([key, label]) => `<option value="${key}" ${key === (selected || 'content') ? 'selected' : ''}>${label}</option>`).join('');
function editorialDraft(s) {
  try {
    const draft = JSON.parse(localStorage.getItem(editorialDraftKey(s)));
    if (draft && typeof draft.reason === 'string') return {...draft, unsaved: true, stale: draft.revision !== s.revision};
  } catch { /* A missing browser draft does not replace saved feedback. */ }
  return s.human_feedback || {reason: '', uncertain: false};
}
function editorialFeedbackHTML(s) {
  if (!s) return '';
  const human = editorialDraft(s);
  return `<details class="editorial-feedback" ${human.reason || human.uncertain ? 'open' : ''}><summary>取舍理由（选填）</summary>
    <label class="field">备注类型<select id="review-human-kind">${editorialKindOptions(human.kind)}</select></label>
    <label class="field">这条为什么留 / 哪处值得看<textarea id="review-human-reason" rows="2" maxlength="1000" placeholder="例如：重点是她听懂后的反应，字幕看不出来">${htmlEscape(human.reason || '')}</textarea></label>
    <label class="editorial-check"><input id="review-human-uncertain" type="checkbox" ${human.uncertain ? 'checked' : ''}>拿不准，不作为明确好坏样本</label>
    <button id="review-feedback-save" class="btn btn-secondary btn-sm">保存备注</button><p id="editorial-feedback-status" class="hint" role="status">${human.stale ? '备注草稿来自旧版本，请结合当前片段核对后保存。' : human.unsaved ? '备注草稿尚未保存，保留时一起保存，也可单独保存。' : '保留时一起保存，也可单独保存备注。'}</p></details>`;
}
function editorialInput() {
  return {reason: document.getElementById('review-human-reason').value.trim(),
    kind: document.getElementById('review-human-kind').value,
    uncertain: document.getElementById('review-human-uncertain').checked};
}
function clearEditorialDraft(s) {
  try { localStorage.removeItem(editorialDraftKey(s)); } catch { /* Server copy is authoritative. */ }
}
function editorialHistoryHTML(s) {
  const events = s?.editorial_history || [];
  const actions = {create: '人工补选', keep: '保留', drop: '废弃', range: '调整范围', metadata: '修改投稿草稿', feedback: '取舍备注'};
  const kinds = editorialKinds;
  const fields = {source_start: '开始', source_end: '结束', editorial_angle: '看点', cover_design: '封面', title: '标题', description: '简介', tags: '标签', dynamic: '粉丝动态', tid: '分区', topic: '话题', season: '合集', decision: '决定'};
  const decisions = {keep: '系统保留', manual_keep: '人工保留', review: '待审核', drop: '废弃', judge_failed: '判断失败'};
  const value = (key, v) => v == null ? '—' : key === 'decision' ? decisions[v] || v
    : key === 'source_start' || key === 'source_end' ? timecode(v, false, true)
    : key === 'cover_design' ? (v.uploaded ? '上传的封面' : `${timecode(v.frame_ms / 1000, false, true)} · ${v.secondary ? '人物与物件并排' : '单图近景'} · ${v.caption || '无短句'}`)
    : typeof v === 'object' ? JSON.stringify(v) : String(v);
  return `<details class="editorial-history"><summary>取舍与修改记录 · ${events.length} 条</summary>${events.length ? [...events].reverse().map(event => {
    const before = event.before || {}, after = event.after || {}, human = event.feedback || {};
    const changes = Object.keys(fields).filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
    return `<article><p><strong>${htmlEscape(actions[event.action] || event.action)}</strong> <span class="hint">${htmlEscape(beijingTime(event.created_at))}${human.uncertain ? ' · 拿不准' : ''}${human.kind && human.kind !== 'content' ? ` · ${htmlEscape(kinds[human.kind] || human.kind)}` : ''}</span></p><p class="editorial-note">${htmlEscape(human.reason || '未填写理由')}</p>${changes.length ? `<div class="editorial-changes"><table class="list"><thead><tr><th>内容</th><th>修改前</th><th>修改后</th></tr></thead><tbody>${changes.map(key => `<tr><th>${fields[key]}</th><td>${htmlEscape(value(key, before[key]))}</td><td>${htmlEscape(value(key, after[key]))}</td></tr>`).join('')}</tbody></table></div>` : ''}</article>`;
  }).join('') : '<p class="hint">尚无取舍记录。后续操作会保存理由和修改前后内容；历史理由不会自动补写。</p>'}</details>`;
}
