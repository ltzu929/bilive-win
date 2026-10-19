exec(open('output/investigate_asr_decoder.py',encoding='utf-8').read().split('for name,extra in ')[0])
import tempfile,itertools,time,re
from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import extract_audio
tempfile.tempdir=str(out)
previous=json.loads((out/'other_clips.json').read_text(encoding='utf-8'))
cases=[{'sample':'today_gap','source':rel,'start':5509,'end':5644}]+[v for v in previous if v['sample'] in [2,4,6]]
results=[]
original_fallback=model.generate_with_fallback
fallback_records=[]
def traced_fallback(encoder_output,prompt,tokenizer,opts):
    v=original_fallback(encoder_output,prompt,tokenizer,opts)
    fallback_records.append({'temperature':v[2],'compression_ratio':v[3],'avg_logprob':v[1],'decoded':tokenizer.tokenizer.decode(v[0].sequences_ids[0],skip_special_tokens=False)})
    return v
model.generate_with_fallback=traced_fallback
for case in cases:
    if case['sample']=='today_gap':
        sample_audio=audio
    else:
        wav=Path(extract_audio(str(root/'Videos'/case['source']),start_seconds=case['start'],duration_seconds=case['end']-case['start']))
        sample_audio=decode_audio(str(wav))
        wav.unlink()
    fallback_records.clear()
    started=time.monotonic()
    iterator,info=model.transcribe(sample_audio,**options)
    segments=[asdict(v) for v in iterator]
    summary={'sample':case['sample'],'source':case['source'],'start':case['start'],'end':case['end'],'elapsed':round(time.monotonic()-started,2),'cues':len(segments),'max_identical_token_run':max((max(len(list(g)) for _,g in itertools.groupby(v['tokens'])) for v in segments),default=0),'positive_temperature_windows':sum(v['temperature']>0 for v in fallback_records),'most_repeated_cue':__import__('collections').Counter(v['text'] for v in segments).most_common(1),'first_cues':[{k:v[k] for k in ('start','end','text')} for v in segments[:6]]}
    results.append({'summary':summary,'segments':segments,'decoder_windows':fallback_records.copy()})
    (out/'sequential_comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True),flush=True)
