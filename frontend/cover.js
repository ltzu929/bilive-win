/* Exported times refer to the source recording; the editor displays clip-relative seconds. */
function coverPrompt(segment, frame, title, manualAngle) {
  const suggestion = segment.editorial_suggestion || {};
  const manual = (manualAngle || '').trim();
  const angle = manual || suggestion.angle || title || segment.description || segment.note;
  const relative = frame / 1000 - segment.source_start;
  const lines = [
    '请用我上传的这张直播原帧制作一张 16:9 的 B 站视频封面，直接给我成品图。',
    title && '投稿标题（只供理解，看视频卡片时会单独显示）：' + title,
    angle && angle !== title && '这条切片的看点：' + angle,
    '这张图取自源片段第 ' + Number(relative.toFixed(3)) + ' 秒。',
  ];
  if (!manual && !suggestion.angle) {
    const context = (segment.note || segment.description || '').trim();
    if (context && context !== angle) lines.push('这段内容的背景：' + context.slice(0, 350));
  }
  const withinEvidence = frame / 1000 >= suggestion.start && frame / 1000 <= suggestion.end;
  if ((!manual || manual === suggestion.angle) && withinEvidence && suggestion.evidence) {
    lines.push('画面参考：' + suggestion.evidence);
  }
  const saved = segment.cover_design || {};
  const caption = (saved.frame_ms === frame && saved.caption) ||
    ((!manual || manual === suggestion.angle) && withinEvidence && suggestion.caption);
  if (caption) {
    lines.push('封面只写这句：“' + caption + '”。按自然语气断句，选最有意味的几个字作为重音。');
  } else {
    const nearby = (segment.subtitles || []).filter(line =>
      line.end >= relative - 2 && line.start <= relative + 3 && line.text?.trim()).slice(0, 3);
    if (nearby.length) lines.push('附近字幕供理解语境：' + nearby.map(line => line.text.trim()).join(' / '));
    lines.push('封面只写一句短而具体的话。标题已经交代的情境，可以用画面里的一句反应、一个问题或一个细节来接。引用人物原话时，只用给出的原话。');
  }
  lines.push('保留原帧里人物和物件的真实样貌。按这条看点构图：展示物件时，让观众能看清它的独特细节，用人物的表情、视线或已有手势把注意力引过去；人物互动时，优先留下最传神的反应。裁去无关的直播界面。');
  lines.push('把短句放在观众能看出是谁在说的位置，用字号、字重或颜色突出重音，避开脸、手和关键物件。缩成投稿卡片后仍要一眼读清。不要把完整投稿标题再印在图上，也不要另加底部标题条或角标。');
  return lines.filter(Boolean).join('\n\n');
}

function coverEditorHTML(s) {
  const base = '/api/segments/' + encodeURIComponent(s.segment_id);
  const preview = s.cover_path ? `<img src="${apiURL(base+'/cover')}?revision=${s.revision}" alt="当前投稿封面">` : '尚未上传封面';
  return `<div class="submission-field"><label>视频封面</label><div>
    <div class="submission-cover" id="saved-cover">${preview}</div>
    ${s.publish_approval || s.state === 'dropped' ? '' : `<div id="cover-editor">
      <div class="action-row">${s.final_valid ? '<button id="cover-current" type="button" class="btn btn-secondary">从当前画面选帧</button>' : '<label>片内秒数<input id="cover-time" type="number" min="0" step=".001" value="0"></label><button id="cover-capture" type="button" class="btn btn-secondary">取帧</button>'}
      ${s.editorial_suggestion ? '<button id="cover-use-suggestion" type="button" class="btn btn-secondary">看推荐画面</button>' : ''}</div>
      <div id="cover-frames" class="cover-frames"></div>
      <div id="cover-export" hidden><div class="action-row"><button type="button" class="btn btn-secondary" id="cover-download">下载原图</button>
        <button type="button" class="btn btn-secondary" id="cover-copy">复制 GPT 提示词</button></div>
        <details id="cover-prompt-details"><summary>查看提示词</summary><textarea id="cover-prompt" rows="7" readonly aria-label="GPT 封面提示词"></textarea></details></div>
      <label>上传做好的封面<input id="cover-upload" type="file" accept="image/jpeg,image/png,image/webp"></label>
      <p id="cover-status" role="status"></p>
    </div>`}</div></div>`;
}

async function coverUploadRecipe(file) {
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) throw new Error('请选择 JPG、PNG 或 WebP 图片。');
  // Generated PNGs often exceed the draft API payload limit. Resize locally for the posting cover.
  let blob = file;
  if (file.size > 2_000_000) {
    const bitmap = await createImageBitmap(file);
    try {
      const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .88));
      if (!blob || blob.size > 2_000_000) throw new Error('图片转换失败，请换用 JPG 格式。');
    } finally { bitmap.close(); }
  }
  const url = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('无法读取图片'));
    reader.readAsDataURL(blob);
  });
  return {upload:url.split(',')[1]};
}

function bindCoverEditor(view) {
  const editor = document.getElementById('cover-editor');
  if (!editor) return;
  const s = view.segment, base = '/api/segments/' + encodeURIComponent(s.segment_id);
  const el = id => editor.querySelector('#cover-' + id);
  const status = message => { el('status').textContent = message; };
  const player = document.querySelector('.submission-preview video');
  const title = document.getElementById('submission-title');
  const key = 'bilive:cover-material:' + s.segment_id;
  const inRange = ms => Number.isSafeInteger(ms) && ms/1000 >= s.source_start && ms/1000 < s.source_end;
  let frame = view.coverDraft?.frame_ms ?? s.cover_design?.frame_ms;
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (inRange(saved)) frame = saved;
  } catch { /* The frame can still be selected without browser storage. */ }
  if (!inRange(frame)) frame = null;
  function refreshPrompt() {
    el('export').hidden = frame === null;
    if (frame !== null) el('prompt').value = coverPrompt(view.segment, frame, title.value.trim(), view.manualAngle ?? view.segment.editorial_angle);
  }
  function showFrames(frames) {
    const available = [...new Set([...frames, frame])].filter(inRange).sort((a,b) => a-b);
    el('frames').replaceChildren();
    for (const ms of available) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'cover-frame';
      button.setAttribute('aria-pressed', String(ms === frame));
      button.innerHTML = `<img src="${apiURL(base+'/frames/'+ms)}" alt=""><span>${Number((ms/1000-s.source_start).toFixed(3))} 秒</span>`;
      button.onclick = () => {
        frame = ms;
        try { localStorage.setItem(key, JSON.stringify(ms)); } catch { /* Optional selection history. */ }
        showFrames(available); status('');
      };
      el('frames').append(button);
    }
    refreshPrompt();
  }
  let capturing = false;
  async function requestFrames(sourceTime) {
    const revision = view.segment.revision;
    let job = await reviewAPI('/api/actions', {action:'cover_frames',target:s.segment_id,revision,payload:{source_time:sourceTime}});
    if (['paused','unavailable'].includes(job.worker?.state)) throw new Error('取帧任务已保存，请在服务设置中恢复 Worker。');
    while (view.current() && ['pending','processing'].includes(job.status)) {
      await new Promise(resolve => setTimeout(resolve, 750));
      job = await reviewAPI('/api/actions/'+job.job_id);
    }
    if (!view.current()) return [];
    if (job.status !== 'done' || !job.result?.frames?.length) throw new Error(job.result?.error || '取帧未完成');
    if (revision !== view.segment.revision) throw new Error('草稿版本已变化，请重新取帧');
    return job.result.frames;
  }
  async function capture(relative) {
    if (capturing || submissionLocked(view)) return;
    if (!Number.isFinite(relative) || relative < 0 || relative >= s.source_end-s.source_start) return status('请输入片段内的秒数。');
    capturing = true;
    try {
      status('正在取帧…');
      const frames = await requestFrames(s.source_start + relative);
      if (view.current()) { showFrames(frames); status('点选要发给 GPT 的画面。'); }
    } catch (error) { if (view.current()) status(error.message); }
    finally { capturing = false; }
  }
  if (el('current')) el('current').onclick = () => {
    player.pause();
    const duration = chatOutputTime(s.source_end-s.source_start, s.chat_cuts);
    capture(chatSourceTime(Math.min(player.currentTime, duration-.001), s.chat_cuts));
  };
  if (el('capture')) {
    const take = () => capture(el('time').valueAsNumber);
    el('capture').onclick = take;
    el('time').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); take(); } };
  }
  if (el('use-suggestion')) el('use-suggestion').onclick = () => {
    const relative = s.editorial_suggestion.frame - s.source_start;
    if (player) { player.pause(); player.currentTime = chatOutputTime(relative, s.chat_cuts); }
    capture(relative);
  };
  el('download').onclick = async () => {
    const ms = frame;
    if (ms === null) return;
    el('download').disabled = true;
    try {
      const url = apiURL(base+'/frames/'+ms+'?original=true');
      let response = await fetch(url);
      if (response.status === 409) {
        status('正在从视频提取原尺寸图片…');
        try {
          await requestFrames(ms/1000);
        } catch (error) {
          if (view.current() && frame === ms) {
            status(error.message + '。也可使用之前留存的素材：');
            const cached = document.createElement('a');
            cached.href = apiURL(base+'/frames/'+ms);
            cached.download = '留存帧-' + ms + '.jpg';
            cached.textContent = '下载已有 JPG';
            el('status').append(cached);
          }
          return;
        }
        if (!view.current()) return;
        response = await fetch(url);
      }
      if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/png')) throw new Error('原尺寸图片尚不可用，请重新取帧。');
      const link = document.createElement('a');
      const objectUrl = URL.createObjectURL(await response.blob());
      link.href = objectUrl;
      link.download = (title.value.trim() || '切片').replace(/[\\/:*?"<>|]/g,'_').slice(0,65) + '-' + Number((ms/1000-s.source_start).toFixed(3)) + '秒.png';
      link.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
      status('已开始下载原帧 PNG。');
    } catch (error) { if (view.current()) status(error.message); }
    finally { el('download').disabled = false; }
  };
  el('copy').onclick = async () => {
    refreshPrompt();
    try {
      await navigator.clipboard.writeText(el('prompt').value);
      status('提示词已复制，可和原图一起发给 GPT。');
    } catch {
      el('prompt-details').open = true;
      el('prompt').focus(); el('prompt').select();
      status('提示词已选中，按 Ctrl+C 复制。');
    }
  };
  title.addEventListener('input', refreshPrompt);
  let generation = 0;
  async function previewUpload(recipe, version) {
    const response = await fetch(apiURL(base+'/cover-preview'), {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(recipe)});
    if (!response.ok) throw new Error((await response.json()).detail || '无法读取封面');
    const blob = await response.blob();
    if (!view.current() || version !== generation) return;
    if (view.coverUrl) URL.revokeObjectURL(view.coverUrl);
    view.coverUrl = URL.createObjectURL(blob);
    const img = new Image(); img.src = view.coverUrl; img.alt = '待保存的投稿封面';
    document.getElementById('saved-cover').replaceChildren(img);
  }
  el('upload').onchange = async () => {
    const file = el('upload').files[0];
    if (!file) return;
    const version = ++generation;
    view.coverLoading = true; updateSubmission(view);
    try {
      const recipe = await coverUploadRecipe(file);
      await previewUpload(recipe, version);
      if (!view.current() || version !== generation) return;
      view.coverDraft = recipe; submissionChanged(view);
      status('已预览上传的封面，点「保存草稿」采用。');
    } catch (error) { if (view.current() && version === generation) status(error.message); }
    finally { if (version === generation) { view.coverLoading = false; updateSubmission(view); } }
  };
  if (view.coverDraft) previewUpload(view.coverDraft, generation).catch(error => { if (view.current()) status(error.message); });
  view.showCoverFrames = showFrames;
  showFrames([...(s.cover_frames || []), frame]);
}
