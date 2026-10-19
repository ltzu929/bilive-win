exec(open('output/investigate_asr_decoder.py',encoding='utf-8').read().split('for name,extra in ')[0])
from faster_whisper.vad import get_speech_timestamps,VadOptions
import itertools
clips=get_speech_timestamps(audio,VadOptions(min_silence_duration_ms=2000,speech_pad_ms=400,max_speech_duration_s=30))
separate=[{k:v/16000 for k,v in c.items()} for c in clips]
fixed=[{'start':t,'end':min(t+30,len(audio)/16000)} for t in range(0,int(len(audio)/16000),30)]
results=[]
for name,timestamps in [('natural_utterances_separate',separate),('continuous_30s',fixed)]:
    records.clear()
    iterator,info=TracedBatch(model).transcribe(audio,batch_size=2,**(options|{'clip_timestamps':timestamps}))
    segments=[asdict(v) for v in iterator]
    summary={'case':name,'max_identical_token_run':max((max(len(list(g)) for _,g in itertools.groupby(v['tokens'])) for v in records),default=0),'cues_in_original_gap':sum(1 for v in segments if 3.4<v['start']<44.59),'first_cues':[{k:v[k] for k in ('start','end','text')} for v in segments[:12]]}
    results.append({'summary':summary,'raw_decoder':records.copy(),'segments':segments})
    (out/'grouping.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True),flush=True)
