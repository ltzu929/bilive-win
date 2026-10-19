exec(open('output/investigate_asr_gap.py',encoding='utf-8').read().split('for name,engine,extra in ')[0].replace("audio_path = extract_audio(str(root / 'Videos' / rel), start_seconds=5509, duration_seconds=135)", "audio_path = str(out / 'audio.wav')").replace("Path(audio_path).replace(out / 'audio.wav')", "").replace("(out / 'diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')", ""))
from dataclasses import asdict
options['without_timestamps'] = False
options['word_timestamps'] = False
iterator,info = BatchedInferencePipeline(model).transcribe(audio, batch_size=s.subtitle.asr_batch, **options)
segments = [asdict(v) for v in iterator]
(out/'batched_without_word_alignment.json').write_text(json.dumps(segments,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'start':v['start'],'end':v['end'],'text':v['text']} for v in segments],ensure_ascii=True),flush=True)
