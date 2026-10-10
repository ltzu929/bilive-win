from types import SimpleNamespace

from bilive.vendor.autoslice.analysis_result import TranscriptSegment
from bilive.vendor.autoslice.transcript_correct import correct_transcript_segments


def test_confirmed_names_reach_correction_prompt_without_changing_cues(monkeypatch):
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-placeholder')
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"corrections":[]}'))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    lines = [TranscriptSegment(0.2, 1.4, '白露')]
    result = correct_transcript_segments(lines, artist='主播甲', hotwords=['白露', '匹诺康尼'],
        client_factory=lambda **kw: client)
    prompt = captured[0]['messages'][1]['content']
    assert captured[0]['model'] == 'mimo-v2.6-flash'
    assert captured[0]['max_completion_tokens'] == 2048
    assert captured[0]['extra_body'] == {'thinking': {'type': 'disabled'}}
    assert captured[0]['response_format'] == {'type': 'json_object'}
    assert '["白露", "匹诺康尼"]' in prompt
    assert [(v.start, v.end, v.text) for v in result] == [(0.2, 1.4, '白露')]
