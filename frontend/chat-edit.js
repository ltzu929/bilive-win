/* Cut coordinates stay relative to the selected source range. */
function chatOutputTime(source, cuts = []) {
  return source - cuts.reduce((removed, [a, b]) => removed + Math.max(0, Math.min(source, b) - a), 0);
}

function chatSourceTime(output, cuts = []) {
  let source = output;
  for (const [a, b] of cuts) {
    if (source < a - 1e-7) break;
    source += b - a;
  }
  return source;
}

function draftCuts(draft) {
  return (draft?.cuts || []).filter(c => c.enabled).map(c => [c.start, c.end]);
}

function hasChatPreview(s) {
  return !!s?.chat_draft && !s.publish_approval && s.state !== 'dropped';
}

function chatChanges(s) {
  return hasChatPreview(s) && JSON.stringify(draftCuts(s.chat_draft)) !== JSON.stringify(s.chat_cuts || []);
}

function chatEditHTML(s) {
  if (s.chat_kind === 'other' || s.publish_approval || s.state === 'dropped') return '';
  const draft = s.chat_draft;
  const sec = value => Number(value.toFixed(2)) + ' 秒';
  const original = s.source_end - s.source_start;
  const cuts = draftCuts(draft);
  const after = chatOutputTime(original, cuts);
  const label = {head:'片头', tail:'片尾', pause:'片中停顿'};
  const rows = (draft?.cuts || []).map((c, index) =>
    `<li><div><strong>${label[c.kind]}</strong> <span class="num">${sec(c.end-c.start)}</span>
      <span class="muted">${c.enabled ? '已剪' : '已恢复'}</span><small>片内 ${timecode(c.start, true, true)} – ${timecode(c.end, true, true)}</small></div>
      <div class="action-row"><button type="button" class="text-button" data-chat-listen="${index}">听剪点</button>
      <button type="button" class="text-button" data-chat-source="${index}">看原段</button>
      <button type="button" class="btn btn-secondary btn-sm" data-chat-toggle="${index}">${c.enabled ? '恢复这段' : '重新剪掉'}</button></div></li>`).join('');
  const summary = draft ? draft.cuts.length
    ? `原片 ${sec(original)} → 预览 ${sec(after)}，共剪去 ${sec(original-after)}。`
    : '没有找到适合剪短的空档，预览保留完整片段。'
    : s.subtitles_revision === s.revision ? '按人声、画面和弹幕识别长空档。' : '先到「核对字幕」保存当前字幕。';
  return `<section class="chat-edit" aria-label="聊天自动精剪"><h3>聊天自动精剪</h3>
    <p>${summary}</p>${rows ? '<ul>'+rows+'</ul>' : ''}
    <div class="action-row">${draft ? '<button type="button" id="chat-play" class="btn btn-secondary btn-sm">播放精剪预览</button>'
      : '<button type="button" id="chat-build" class="btn btn-secondary">生成聊天精剪预览</button>'}
      ${chatChanges(s) ? '<button type="button" id="chat-apply" class="btn btn-primary">采用这版并生成成片</button>' : ''}</div>
    <p id="chat-status" class="hint" role="status"></p></section>`;
}
