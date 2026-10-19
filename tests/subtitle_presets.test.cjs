const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function context() {
  const ctx = vm.createContext({});
  vm.runInContext(fs.readFileSync('frontend/subtitle-presets.js', 'utf8'), ctx);
  return ctx;
}

test('PR presets preserve placement and switch the complete appearance without mutating the clip', () => {
  const ctx = context();
  ctx.original = {font_name: 'Arial', margin_v: 137, alignment: 8, font_size: 80};
  const red = vm.runInContext("applySubtitlePreset(original, 'pr-red')", ctx);
  assert.equal(red.margin_v, 137);
  assert.equal(red.alignment, 8);
  assert.equal(red.font_name, 'Douyin Sans');
  assert.equal(red.font_size, 48);
  assert.equal(red.bold, true);
  assert.equal(red.spacing, 5.76);
  assert.equal(red.primary_colour, '&H005959D9');
  assert.equal(red.shadow_colour, '&H00ADADEE');
  assert.equal(ctx.original.font_size, 80);
  ctx.red = red;
  assert.equal(vm.runInContext('subtitlePresetId(red)', ctx), 'pr-red');
  const blue = vm.runInContext("applySubtitlePreset(red, 'pr-blue')", ctx);
  assert.equal(blue.primary_colour, '&H00D99859');
  assert.equal(blue.shadow_colour, '&H00EECBAD');
  assert.equal(blue.margin_v, 137);
  ctx.custom = {...blue, font_size: 72};
  assert.equal(vm.runInContext('subtitlePresetId(custom)', ctx), '');
  assert.match(vm.runInContext("subtitlePresetSelectHTML('picker', red)", ctx), /value="pr-red" selected/);
});

test('streamer picker offers global inheritance and detects saved presets without adding placement', () => {
  const ctx = context();
  const inherited = vm.runInContext("subtitlePresetSelectHTML('profile', {}, {inherit: true})", ctx);
  assert.match(inherited, /value="inherit" selected/);
  assert.match(inherited, /默认字幕样式/);
  for (const id of ['pr-red', 'pr-blue']) {
    ctx.id = id;
    const style = vm.runInContext('applySubtitlePreset({}, id)', ctx);
    assert.equal(style.margin_v, undefined);
    assert.equal(style.alignment, undefined);
    ctx.style = style;
    const html = vm.runInContext("subtitlePresetSelectHTML('profile', style, {inherit: true})", ctx);
    assert.match(html, new RegExp(`value="${id}" selected`));
    assert.doesNotMatch(html, /value="inherit" selected/);
  }
  const custom = vm.runInContext("subtitlePresetSelectHTML('profile', {outline: 3}, {inherit: true})", ctx);
  assert.match(custom, /value="" disabled selected/);
});
