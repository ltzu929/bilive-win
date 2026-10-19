exec(open('output/investigate_asr_decoder.py',encoding='utf-8').read().split('for name,extra in ')[0])
import time,itertools,hashlib
cases=[('original_1',hotwords),('none_1',None),('name_only','咩栗'),('aliases_only','咩栗羊, 小棉花'),('unrelated','白露, 匹诺康尼'),('original_2',hotwords),('none_2',None),('original_3',hotwords)]
all_results=[]
for name,words in cases:
    started=time.monotonic()
    records.clear()
    iterator,info=TracedBatch(model).transcribe(audio,batch_size=2,**(options|{'hotwords':words}))
    segments=[asdict(v) for v in iterator]
    first=records[0]
    summary={'case':name,'hotwords':words,'elapsed':round(time.monotonic()-started,2),'first_token_hash':hashlib.sha256(json.dumps(first['tokens']).encode()).hexdigest()[:16],
             'max_identical_token_run':max(len(list(g)) for _,g in itertools.groupby(first['tokens'])),
             'cue_count_in_original_gap':sum(1 for v in segments if 3.4<v['start']<44.59),
             'first_cues':[{k:v[k] for k in ('start','end','text')} for v in segments[:4]]}
    all_results.append({'summary':summary,'raw_decoder':records.copy(),'segments':segments})
    (out/'hotword_repeat.json').write_text(json.dumps(all_results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True),flush=True)
