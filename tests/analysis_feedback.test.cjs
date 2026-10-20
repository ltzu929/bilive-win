const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({ Date });
vm.runInContext(fs.readFileSync('frontend/ui.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('frontend/workbench.js', 'utf8'), context);
const state = (job, evidence, segments = [], worker) => {
  context.input = { job, evidence, segments, worker };
  return vm.runInContext('recordingAnalysisState(input.job, input.evidence, input.segments, input.worker)', context);
};
test('queued and processing are never presented as empty completed results', () => {
  assert.match(state({status: 'pending', progress: {reason: 'ASR model missing'}}).detail, /ASR model missing/);
  const busy = state({status: 'processing', progress: {started_at: Date.now()/1000-65}},
    {total: 3, summary: {completed: 1}});
  assert.match(busy.title, /已用 00:01:05/);
  assert.match(busy.detail, /1 \/ 3/);
  assert.match(busy.empty, /尚未结束/);
});
test('paused and offline tasks show actionable recovery without a busy spinner', () => {
  for (const worker of [{state:'paused'}, {state:'offline'}]) {
    for (const status of ['pending', 'processing']) {
      const result = state({status}, {status:'processing'}, [], worker);
      assert.equal(result.resume, true);
      assert.equal(result.tone, 'warning');
      assert.match(result.detail, /恢复分析/);
      assert.doesNotMatch(result.title, /已排队|正在分析/);
    }
  }
  assert.equal(state({status:'done'}, {total:0}, [], {state:'paused'}).resume, undefined);
  assert.equal(state({status:'pending'}, {}, [], {state:'running'}).tone, 'busy');
});
test('blocked environment and stopping service explain why work is not progressing', () => {
  assert.match(state({status:'pending',progress:{reason:'CUDA unavailable'}}, {}, [], {state:'running'}).detail, /环境检查未通过.*CUDA unavailable/);
  assert.equal(state({status:'pending'}, {}, [], {state:'stopping'}).resume, undefined);
});
test('running candidates report persisted processing stages', () => {
  const result = state({status:'processing'}, {total:2, candidates:[
    {state:'processing',timings:{candidate_cut:{status:'done'},judgment:{status:'processing'}}},
    {state:'processing',timings:{auxiliary_asr:{status:'processing'}}},
  ]}, [], {state:'running'});
  assert.match(result.detail, /候选 1：准备 AI 视频/);
  assert.match(result.detail, /候选 2：识别参考字幕/);
});
test('a candidate that finished judgment stays visible while others run', () => {
  const result = state({status:'processing'}, {total:3, candidates:[
    {state:'processing',timings:{judgment:{status:'processing'}}},
    {state:'processing',timings:{judgment:{status:'processing'}}},
    {state:'processing',timings:{judgment:{status:'done'}}},
  ]}, [], {state:'running'});
  assert.match(result.detail, /候选 1：准备 AI 视频/);
  assert.match(result.detail, /候选 2：准备 AI 视频/);
  assert.match(result.detail, /候选 3：判断已结束，等待汇总/);
});
test('no recall, explicit drops, technical failure and skipped analysis have different outcomes', () => {
  assert.match(state({status: 'done'}, {total: 0}).title, /未召回/);
  assert.match(state({status: 'done'}, {total: 2, summary: {dropped: 2}}).title, /AI 未建议保留/);
  assert.match(state({status: 'done'}, {total: 2, summary: {failed: 1}}).title, /人工检查/);
  assert.match(state({status: 'failed', result: {error: 'timeout'}}).detail, /timeout/);
  assert.match(state({status: 'failed'}).empty, /不能据此判断/);
  assert.match(state({status: 'done'}, {skipped: true, skip_reason: 'below min_source_size_mb'}).title, /跳过/);
  assert.match(state({status: 'done', result: {cancelled: true}}).title, /取消/);
});
test('partial evidence pages cannot falsely report all candidates dropped', () => {
  const result = state({status: 'done'}, {total: 30, candidates: [{state: 'drop'}]}, [{state: 'needs_review'}]);
  assert.match(result.title, /1 个片段可审核/);
});
test('completed AI judgment does not hide subtitle or rendering failure', () => {
  const result = state({status: 'done'}, {total: 1, summary: {},
    segments: [{error: 'PipelineError', timings: {formal_asr: {status: 'failed'}}}]}, [{state: 'needs_review'}]);
  assert.match(result.title, /人工检查/);
  assert.match(result.detail, /1 个片段在字幕 \/ 成片阶段失败/);
});

test('model empty results and legacy records explicitly explain why no segment exists', () => {
  for (const candidate of [
    {state:'empty',reason:'没有独立完整事件'},
    {state:'review',reason:'empty judgment requires manual review',judgments:[],timings:{judgment:{status:'done'}}},
  ]) {
    // The complete candidate page also works with the older running API summary.
    const evidence = {total:1,candidates:[candidate],summary:{needs_review:1}};
    const result = state({status:'done'}, evidence);
    assert.match(result.title, /AI 未选出达标片段/);
    assert.match(result.empty, /AI 返回空结果/);
    assert.doesNotMatch(result.title, /人工检查|失败/);
    if (candidate.state === 'review') assert.match(result.detail, /旧记录未保存具体理由/);
    context.evidence = {...evidence,segments:[]};
    const html = vm.runInContext('review.evidence = evidence; reviewEvidenceHTML()', context);
    assert.match(html, /AI 返回空结果，未选出达标片段/);
    assert.match(html, candidate.state === 'empty' ? /没有独立完整事件/ : /旧记录未保存具体理由/);
    assert.doesNotMatch(html, /empty judgment requires manual review/);
  }
});

test('empty outcomes do not conceal genuine failures or unresolved empty responses', () => {
  const mixed = state({status:'done'}, {total:3,summary:{empty:2,failed:1}});
  assert.match(mixed.title, /人工检查/);
  assert.match(mixed.detail, /1 个候选处理失败/);
  assert.match(mixed.detail, /2 个候选 AI 返回空结果/);
  assert.match(state({status:'done'}, {total:1,candidates:[
    {state:'review',reason:'empty judgment requires manual review',judgments:[]},
  ]}).title, /人工检查/);
  assert.match(state({status:'done'}, {total:1,summary:{empty:1},
    segments:[{error:'render failed'}]}).title, /人工检查/);
});
