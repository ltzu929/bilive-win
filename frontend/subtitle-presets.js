// Converted from the user's 红色.prtextstyle and 蓝色字幕.prtextstyle.
// Font PostScript name: DOUYINSANSBOLD-GB; run size 48, tracking 120/1000 em.
// Shadow opacity 100, distance 3, size 10, blur 0. PR-specific gradients and
// stroke alignment are approximated by ASS. Placement remains the clip's own.
const subtitlePresets = [
  {id: 'pr-red', name: '红色', style: {
    font_name: 'Douyin Sans', font_size: 48, bold: true, spacing: 5.76,
    primary_colour: '&H005959D9', outline_colour: '&H00FFFFFF', outline: 2,
    shadow_colour: '&H00ADADEE', shadow: 3, shadow_outline: 10,
  }},
  {id: 'pr-blue', name: '蓝色字幕', style: {
    font_name: 'Douyin Sans', font_size: 48, bold: true, spacing: 5.76,
    primary_colour: '&H00D99859', outline_colour: '&H00FFFFFF', outline: 2,
    shadow_colour: '&H00EECBAD', shadow: 3, shadow_outline: 10,
  }},
];

function subtitlePresetId(style) {
  return subtitlePresets.find(p => Object.entries(p.style).every(([key, value]) => style?.[key] === value))?.id || '';
}

function applySubtitlePreset(style, id) {
  const preset = subtitlePresets.find(p => p.id === id);
  return preset ? {...style, ...preset.style} : {...style};
}

function subtitlePresetSelectHTML(id, style, {inherit = false} = {}) {
  const selected = subtitlePresetId(style);
  const followsGlobal = inherit && !Object.keys(style || {}).length;
  return `<label class="field">${inherit ? '默认字幕样式' : '字幕样式'}<select id="${id}">${inherit ? `<option value="inherit" ${followsGlobal ? 'selected' : ''}>跟随全局默认</option>` : ''}<option value="" disabled ${selected || followsGlobal ? '' : 'selected'}>当前 / 自定义样式</option>${subtitlePresets.map(p => `<option value="${p.id}" ${selected === p.id ? 'selected' : ''}>${p.name}（PR）</option>`).join('')}</select></label>`;
}

function subtitlePresetSampleHTML() {
  return `<div class="subtitle-preset-sample" aria-label="字幕样式示意"><span aria-hidden="true"></span><span></span></div><p class="hint">PR 预设使用抖音美好体 Bold · 效果近似转换，位置沿用当前设置</p>`;
}

function updateSubtitlePresetSample(root, style) {
  const spans = root?.querySelectorAll('.subtitle-preset-sample span');
  if (spans?.length !== 2) return;
  const [shadow, text] = spans;
  for (const span of spans) {
    span.textContent = '这是字幕效果预览';
    span.style.fontFamily = `${JSON.stringify(style.font_name || 'sans-serif')}, sans-serif`;
    span.style.fontWeight = style.bold ? '700' : '400';
    span.style.fontSize = `${Math.max(12, (style.font_size || 20) * .55)}px`;
    span.style.letterSpacing = `${(style.spacing || 0) * .55}px`;
    span.style.paintOrder = 'stroke fill';
  }
  text.style.color = assColorHex(style.primary_colour);
  text.style.webkitTextStroke = `${(style.outline ?? 1) * .55}px ${assColorHex(style.outline_colour || '&H00000000')}`;
  const distance = (style.shadow || 0) / Math.sqrt(2) * .55;
  shadow.style.visibility = style.shadow || style.shadow_outline ? 'visible' : 'hidden';
  shadow.style.transform = `translate(${distance}px, ${distance}px)`;
  shadow.style.color = assColorHex(style.shadow_colour || '&H64000000');
  shadow.style.opacity = 1 - parseInt((style.shadow_colour || '&H64000000').slice(2,4), 16) / 255;
  shadow.style.webkitTextStroke = `${(style.shadow_outline || style.outline || 0) * .55}px ${shadow.style.color}`;
}
