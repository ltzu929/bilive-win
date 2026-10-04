"""Reuse pinned density, MiMo and ASR adapters; persist each review outcome."""
from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from bilive.domain.models import SubtitleLine, clamp_subtitle_times, segment_id_from_source_and_range
from bilive.media.finalize import PipelineError, ensure_raw_cut
from bilive.media.remux import _ffprobe_duration
from bilive.vendor.autoslice.burst_detector import detect_bursts
from bilive.worker.timing import timed_stage


def read_danmaku(xml: Path) -> list[tuple[float, str]]:
    if not xml.is_file():
        raise PipelineError("danmaku XML missing")
    result = []
    for node in ET.parse(xml).getroot().iter("d"):
        ts = float(node.attrib["p"].split(",")[0])
        if not math.isfinite(ts) or ts < 0:
            raise PipelineError("invalid danmaku timestamp")
        result.append((ts, node.text or ""))
    return result


class AnalysisPipeline:
    def __init__(self, settings, workbench, finalizer, judge=None, transcribe=None, correct=None):
        self.settings, self.workbench, self.finalizer = settings, workbench, finalizer
        self.judge = judge or self._judge
        self.transcribe = transcribe or self._transcribe
        self.asr_lock = threading.Lock()
        self.encode_limit = threading.BoundedSemaphore(max(1, int(settings.mimo.encode_concurrency)))
        self.correct = correct or self._correct

    def _correct(self, segments, **kwargs):
        from bilive.vendor.autoslice.transcript_correct import correct_transcript_segments
        return correct_transcript_segments(segments, **kwargs, base_url=self.settings.mimo.base_url,
                                           model=self.settings.mimo.model, timeout=self.settings.mimo.timeout)

    def _judge(self, **kwargs):
        from bilive.vendor.autoslice.mllm_sdk.mimo_video import judge_candidate_clips_with_mimo, encode_video_for_mimo
        m = self.settings.mimo
        return judge_candidate_clips_with_mimo(**kwargs, base_url=m.base_url, model=m.model, fps=m.fps,
            media_resolution=m.media_resolution, timeout=m.timeout, max_base64_bytes=m.max_base64_bytes,
            encoder=lambda *args, **kw: encode_video_for_mimo(*args, **kw, semaphore=self.encode_limit))

    def _transcribe(self, video: Path, **window) -> dict:
        from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import extract_audio, transcribe_audio_whisper
        sub = self.settings.subtitle
        from bilive.worker.preflight import cached_asr_model
        model_path = cached_asr_model(self.settings)
        extracted = extract_audio(str(video), **window)
        if not extracted:
            raise PipelineError('audio extraction failed')
        audio = Path(extracted)
        try:
            return transcribe_audio_whisper(str(audio), model_size=str(model_path), device=sub.asr_device,
                compute_type=sub.asr_compute_type, batch_size=sub.asr_batch, cpu_threads=sub.asr_cpu_threads,
                vad_filter=sub.asr_vad,engine=self.settings.multi_modal.whisper_engine,
                vad_min_silence_ms=self.settings.multi_modal.whisper_vad_min_silence_ms,
                vad_speech_pad_ms=self.settings.multi_modal.whisper_vad_speech_pad_ms)
        finally:
            audio.unlink(missing_ok=True)
            if self.settings.multi_modal.unload_audio_model_after_analysis:
                from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import unload_asr_models
                unload_asr_models()

    def transcribe_segment(self, job) -> dict:
        """Recognize only the saved range; return a draft without changing media or approval."""
        wb = self.workbench
        with wb.lock():
            s = wb.load(job.target)
            wb.check_revision(s, job.payload['revision'])
            wb.check_source_idle(s['source_relpath'])
            if s.get('publish_approval'):
                raise PipelineError('approved final is protected')
            source = wb.source(s['source_relpath'])
            start, end = s['source_start'], s['source_end']
        if end > _ffprobe_duration(source, []) + 0.01:
            raise PipelineError('range exceeds source duration')
        with self.asr_lock:
            transcript = self.transcribe(source, start_seconds=start, duration_seconds=end-start)
        if transcript.get('error'):
            raise PipelineError(f"本地字幕识别失败：{transcript['error']}")
        lines = []
        for value in transcript.get('segments', []):
            if (not isinstance(value.get('text'), str) or isinstance(value.get('start'), bool)
                    or isinstance(value.get('end'), bool)):
                raise PipelineError('invalid ASR cue')
            lines.append(SubtitleLine(len(lines)+1, float(value['start']), float(value['end']), value['text'].strip()))
        if not lines:
            raise PipelineError('当前片段未识别到语音，可试听后手工添加字幕')
        clamp_subtitle_times(lines, end-start)
        with wb.lock():
            wb.check_revision(wb.load(job.target), job.payload['revision'])
        return {'ok': True, 'revision': job.payload['revision'], 'lines': [asdict(line) for line in lines]}

    def run(self, source_relpath: str, task_id: str) -> dict:
        source = self.workbench.source(source_relpath)
        duration = _ffprobe_duration(source, [])
        danmaku = read_danmaku(source.with_suffix(".xml"))
        b = self.settings.burst
        from bilive.lifecycle.profiles import profile_for_source
        profile = profile_for_source(self.workbench.videos, source_relpath)
        options = profile.get('default_slice_options', {})
        bursts = detect_bursts([t for t, _ in danmaku], duration, burst_ratio=options.get('burst_ratio', b.ratio),
            burst_window=options.get('burst_window', b.window), context=options.get('burst_context', b.context),
            merge_gap=options.get('burst_merge_gap', b.merge_gap), top_n=options.get('burst_top_n', b.top_n), lag_seconds=b.lag)
        history = {"source_relpath":source_relpath, "candidates":[], "segments":[], "status":"processing"}
        path = source.with_suffix(source.suffix + ".task.json")
        self._save_history(path, history)
        with self.workbench.lock():
            bindings = []
            for event in bursts:
                candidate = self.workbench.artifacts / "candidates" / task_id / f"candidate-{round(event.start*1000)}-{round(event.end*1000)}.mp4"
                entry = {**asdict(event), "path":str(candidate), "state":"processing", "segments":[]}
                history["candidates"].append(entry)
                bindings.append((event, candidate, entry))
            self._save_history(path, history)
            with ThreadPoolExecutor(max_workers=max(1, int(self.settings.mimo.request_concurrency))) as pool:
                futures = [pool.submit(self._prepare, source, source_relpath, event, candidate, entry, danmaku, profile)
                           for event, candidate, entry in bindings]
                prepared = [future.result() for future in futures]
            from bilive.vendor.clip_rules import _mark_cross_candidate_duplicates
            items = [(i, SimpleNamespace(context_start=event.start, context_end=event.end), result)
                     for i, ((event, _, _), result) in enumerate(zip(bindings, prepared))]
            _mark_cross_candidate_duplicates(items, source_relpath, self._quality_review)
            ordered = sorted(zip(bindings, prepared), key=lambda pair:
                not any(r.judge_status == 'keep' and r.retain_recommendation for r in pair[1].get('results') or []))
            for (event, candidate, entry), prepared_result in ordered:
                results = prepared_result.get('results', [])
                entry['judgments'] = [asdict(r) for r in results or []]
                if prepared_result.get('error'):
                    entry.update(state='failed', reason=prepared_result['error'])
                else:
                    try:
                        if not results:
                            entry.update(state='review', reason='empty judgment requires manual review')
                        for result in sorted(results, key=lambda r:not (r.judge_status == 'keep' and r.retain_recommendation)):
                            self._result(result, source_relpath, source, event, candidate, entry, history,
                                         prepared_result.get('transcript'), duration)
                        if (len(results) == 1 and results[0].judge_status == 'drop'
                                and not results[0].retain_recommendation
                                and results[0].raw_model_response.get('decision') == 'drop'
                                and entry['state'] != 'review'):
                            entry['state'] = 'drop'
                            candidate.unlink(missing_ok=True)
                        elif results and entry['state'] != 'review':
                            entry['state'] = 'reviewed'
                    except Exception as exc:
                        entry.update(state='failed', reason=type(exc).__name__)
                self._save_history(path, history)
            history['status'] = 'done'
            history['requires_source_review'] = not bursts or any(c['state'] in {'failed','review'} for c in history['candidates'])
            self._save_history(path, history)
        return {'ok':True,'candidates':len(bursts),'segments':history['segments'],
                'requires_source_review':history['requires_source_review']}

    def _prepare(self, source, rel, event, candidate, entry, danmaku, profile):
        import time
        started = time.perf_counter()
        try:
            with timed_stage(entry,'candidate_cut'):
                ensure_raw_cut(source, candidate, event.start, event.end, [])
            transcript = None
            if self.settings.analysis.pre_judge_asr:
                try:
                    with timed_stage(entry,'auxiliary_asr'), self.asr_lock:
                        transcript = self.transcribe(candidate)
                    segments = transcript.get('segments', [])
                    if transcript.get('error') or not segments or any(
                            not isinstance(v.get('text'),str) or not v['text'].strip()
                            or not all(math.isfinite(float(v[k])) for k in ('start','end'))
                            or not 0 <= float(v['start']) < float(v['end']) <= event.end-event.start+0.01
                            for v in segments):
                        raise PipelineError('pre-judge ASR is not reusable')
                except Exception:
                    transcript = None
                    entry['pre_asr_failed'] = True
                    entry['timings']['auxiliary_asr']['status'] = 'failed'
            with timed_stage(entry,'judgment'):
                results = self.judge(video_path=str(candidate),artist=profile.get('display_name') or Path(rel).parent.name,
                guidance=str(profile.get('approved_guidance',''))[:2000],
                danmaku_text='\n'.join(f'{t-event.start:.2f}s {text}' for t,text in danmaku if event.start <= t <= event.end),
                candidate_duration=event.end-event.start,candidate_start=event.start,
                candidate_core_start=event.burst_start,candidate_core_end=event.burst_end,
                candidate_transcript=(transcript or {}).get('transcript','')[:self.settings.analysis.asr_prompt_max_chars])
            return {'results':results,'transcript':transcript}
        except Exception as exc:
            return {'error':type(exc).__name__}
        finally:
            entry['prepare_ms'] = round((time.perf_counter()-started)*1000)

    def _quality_review(self, result):
        quality = (result.quality_score,result.completeness_score,result.confidence)
        passes = all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0 <= v <= 1 for v in quality)
        passes = passes and self.settings.quality.all_pass(*quality)
        if result.judge_status == 'keep' and not passes:
            result.judge_status = 'review'
            result.retain_recommendation = False
            result.judge_error = 'missing or below-threshold quality scores'
            return True
        return False

    def retry_judge(self, job):
        from bilive.lifecycle.profiles import profile_for_source
        from bilive.domain.models import task_id_from_source_relpath
        with self.workbench.lock():
            s = self.workbench.load(job.target)
            self.workbench.check_revision(s, job.payload['revision'])
            if s.get('publish_approval'):
                raise PipelineError('approved final is protected')
            rel = s['source_relpath']
            source = self.workbench.source(rel)
            candidate = Path(s.get('candidate_path','')).resolve()
            root = (self.workbench.artifacts / 'candidates' / task_id_from_source_relpath(rel)).resolve()
            if not candidate.is_relative_to(root) or candidate.suffix != '.mp4' or not candidate.is_file():
                raise PipelineError('original candidate unavailable or outside source scope')
            start,end = float(s['candidate_start']),float(s['candidate_end'])
            if not all(math.isfinite(t) for t in (start,end)) or start < 0 or end <= start:
                raise PipelineError('invalid candidate interval')
            profile = profile_for_source(self.workbench.videos,rel)
            event = SimpleNamespace(start=start,end=end,burst_start=s['core_start'],burst_end=s['core_end'])
            entry = {}
            prepared = self._prepare(source,rel,event,candidate,entry,read_danmaku(source.with_suffix('.xml')),profile)
            if prepared.get('error'):
                raise PipelineError('candidate rejudgment failed: '+prepared['error'])
            results = prepared.get('results') or []
            for result in results:
                self._quality_review(result)
                trim = result.suggested_trim
                if trim and (not all(isinstance(t,(int,float)) and math.isfinite(t) for t in (trim.trim_start,trim.trim_end))
                             or not 0 <= trim.trim_start < trim.trim_end <= end-start+0.01):
                    result.judge_status = 'review'
                    result.retain_recommendation = False
                    result.judge_error = 'invalid judgment range'
            record = {'job_id':job.job_id,'segment_id':s['segment_id'],'revision':s['revision'],
                      'candidate_start':start,'candidate_end':end,'results':[asdict(r) for r in results],
                      'empty_requires_review':not results,'pre_asr_failed':entry.get('pre_asr_failed',False)}
            record_path = self.workbench.artifacts / 'rejudgments' / f'{job.job_id}.json'
            record_path.parent.mkdir(parents=True,exist_ok=True)
            self._save_history(record_path,record)
            # Rejudgment provides new evidence. Applying a range still uses the
            # explicit manual-edit path; existing subtitles/finals stay versioned.
            s['latest_rejudgment'] = json.loads(record_path.read_text(encoding='utf-8'))
            self.workbench.save(s)
            return {'ok':True,'result_count':len(results),'requires_manual_application':True}

    @staticmethod
    def _save_history(path: Path, value: dict):
        def finite(item):
            if isinstance(item,float) and not math.isfinite(item):
                return None
            if isinstance(item,dict):
                return {k:finite(v) for k,v in item.items()}
            if isinstance(item,list):
                return [finite(v) for v in item]
            return item
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(finite(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        tmp.replace(path)

    def _result(self, result, rel, source, event, candidate, entry, history, pre_asr, duration):
        trim = result.suggested_trim
        start = event.start + (trim.trim_start if trim else 0)
        end = event.start + (trim.trim_end if trim else event.end-event.start)
        if not all(math.isfinite(t) for t in (start,end)) or start < event.start or end > event.end+0.01 or end <= start:
            entry.update(state="review", reason="invalid judgment range")
            return
        sid = segment_id_from_source_and_range(rel, round(start*1000), round(end*1000))
        entry["segments"].append(sid)
        duplicate = any(abs(s["source_start"]-start) < 0.5 and abs(s["source_end"]-end) < 0.5
                        for s in self.workbench.list(rel))
        if (self.workbench.root / f"{sid}.json").exists():
            # Do not overwrite prior manual editing when a candidate overlaps.
            entry.update(state='review', reason='existing segment preserved; duplicate requires review')
            return
        quality = (result.quality_score, result.completeness_score, result.confidence)
        keep = result.judge_status == "keep" and result.retain_recommendation
        passes = all(isinstance(v, (int,float)) and math.isfinite(v) for v in quality) and self.settings.quality.all_pass(*quality)
        decision = "drop" if result.judge_status == "drop" else ("judge_failed" if result.judge_status == "judge_failed" else ("keep" if keep and passes and not duplicate else "review"))
        s = {"segment_id":sid,"source_relpath":rel,"source_start":start,"source_end":end,
             "title":result.title,"description":result.description,"tags":result.tags,
             "note":result.quality_reason,"decision":decision,"revision":1,"state":"needs_review",
             "subtitles_revision":None,"final_path":None,"final_revision":None,"publish_approval":False,
             "quality_score":quality[0] if isinstance(quality[0],(int,float)) and math.isfinite(quality[0]) else None,
             "completeness_score":quality[1] if isinstance(quality[1],(int,float)) and math.isfinite(quality[1]) else None,
             "confidence":quality[2] if isinstance(quality[2],(int,float)) and math.isfinite(quality[2]) else None,
             "candidate_path":str(candidate)}
        s.update(candidate_start=event.start,candidate_end=event.end,core_start=event.burst_start,core_end=event.burst_end,
                 original_source_start=start,original_source_end=end,
                 duplicate_of=getattr(result,'duplicate_of',None),duplicate_reason=getattr(result,'duplicate_reason',None))
        from bilive.lifecycle.profiles import profile_for_source
        profile = profile_for_source(self.workbench.videos, rel)
        s['description'] = result.description or profile.get('default_description', '')
        s['tags'] = list(dict.fromkeys([*result.tags, *profile.get('default_tags', [])]))
        default_style = (self.workbench.current_default_style() if self.workbench.default_style_provider
                         else self.settings.subtitle_style())
        s['subtitle_style'] = {**default_style,**profile.get('default_subtitle_style', {})}
        self.workbench.save(s)
        history["segments"].append(sid)
        sidecar = self.finalizer.artifact_paths(sid)["meta"].with_name("analysis_sidecar.json")
        sidecar.parent.mkdir(parents=True,exist_ok=True)
        result.candidate_start,result.candidate_end = event.start,event.end
        result.core_start,result.core_end = event.burst_start,event.burst_end
        result.source_start,result.source_end = start,end
        self._save_history(sidecar, asdict(result))
        if not keep or not passes or duplicate:
            if decision != "drop":
                entry["state"] = "review"
            return
        try:
            # Formal transcription uses the actual selected source range;
            # candidate timestamps are never treated as segment timestamps.
            s['processing_stage'] = 'formal_asr'
            with timed_stage(s,'formal_asr'):
                start, end, lines, alignment = self._formal_subtitles(source, sid, start, end, duration, event, pre_asr)
            if self.settings.subtitle.light_correct:
                from bilive.vendor.autoslice.analysis_result import TranscriptSegment
                original = [TranscriptSegment(v.start,v.end,v.text) for v in lines]
                timestamps = [(v.start,v.end) for v in original]
                try:
                    with timed_stage(s,'correction'):
                        corrected = self.correct(original,artist=profile.get('display_name') or Path(rel).parent.name,title=result.title)
                    if (len(corrected) != len(original) or any((b.start,b.end) != a or not isinstance(b.text,str) or not b.text.strip()
                            for a,b in zip(timestamps,corrected))):
                        raise PipelineError('subtitle correction changed timestamps or cue count')
                    lines = [SubtitleLine(i+1,v.start,v.end,v.text) for i,v in enumerate(corrected)]
                    alignment['correction'] = 'completed'
                except Exception:
                    alignment['correction'] = 'failed_original_retained'
                    s['timings']['correction']['status'] = 'failed'
            s.update(source_start=start,source_end=end,alignment=alignment)
            from bilive.vendor.autoslice.analysis_result import TranscriptSegment
            result.source_start,result.source_end = start,end
            result.transcript_segments = [TranscriptSegment(v.start,v.end,v.text) for v in lines]
            result.transcript = ' '.join(v.text for v in lines)
            s['processing_stage'] = 'subtitles_save'
            with timed_stage(s,'subtitles_save'):
                self.workbench.subtitles.save(sid,end-start,lines,1)
            s["subtitles_revision"] = 1
            self.workbench.save(s)
            from bilive.vendor.clip_rules import _intervals_are_duplicate, _text_similarity
            aligned_duplicate = next((other for other in self.workbench.list(rel)
                if other['segment_id'] != sid and other.get('final_path')
                and _intervals_are_duplicate({'start':start,'end':end},
                    {'start':other['source_start'],'end':other['source_end']})
                and _text_similarity(result.topic_summary or result.title,other.get('topic_summary') or other.get('title')) >= .50),None)
            if aligned_duplicate:
                s.update(decision='review',state='needs_review',processing_stage='duplicate_review',
                         duplicate_of=aligned_duplicate['segment_id'],duplicate_reason='duplicate after ASR alignment')
                entry['state'] = 'review'
                self.workbench.save(s)
                self._save_history(sidecar,{**asdict(result),'alignment':alignment,
                    'duplicate_of':s['duplicate_of'],'duplicate_reason':s['duplicate_reason']})
                return
            s['processing_stage'] = 'finalize'
            with timed_stage(s,'finalize'):
                final = self.finalizer.finalize(sid,rel,start,end,1)
            s.update(generated_path=final.final_path, processing_stage='stage')
            with timed_stage(s,'stage'):
                self.workbench.uploads.stage(final.final_path,result.title,s['description'],s['tags'])
            s.update(state="awaiting_publish",final_path=final.final_path,final_revision=1,processing_stage='completed')
        except Exception as exc:
            s.update(state="needs_review", processing_error=type(exc).__name__)
            entry["state"] = "review"
        self.workbench.save(s)
        self._save_history(sidecar,{**asdict(result),'alignment':s.get('alignment'),
            'processing_stage':s.get('processing_stage'),'processing_error':s.get('processing_error'),
            'timings':s.get('timings',{}),
            'duplicate_of':s.get('duplicate_of'),'duplicate_reason':s.get('duplicate_reason')})

    def _formal_subtitles(self, source, sid, start, end, duration, event, pre_asr):
        from bilive.vendor.clip_rules import snap_trim_to_segments, _slice_segments_to_trim
        from bilive.vendor.autoslice.analysis_result import TranscriptSegment, TrimSuggestion
        padding = max(0.0, float(self.settings.subtitle.padding))
        window_start, window_end = max(0.0,start-padding), min(duration,end+padding)
        if pre_asr and event.start <= window_start and event.end >= window_end:
            transcript, origin, window_duration = pre_asr, event.start, event.end-event.start
        else:
            padded = self.finalizer.artifact_paths(sid)['raw'].with_name('asr_window.mp4')
            key = {'source':str(source),'start':window_start,'end':window_end}
            binding = padded.with_suffix('.range.json')
            if not binding.is_file() or json.loads(binding.read_text(encoding='utf-8')) != key:
                fresh = padded.with_name('asr_next.mp4')
                fresh.unlink(missing_ok=True)
                ensure_raw_cut(source,fresh,window_start,window_end,[])
                fresh.replace(padded)
                self._save_history(binding,key)
            ensure_raw_cut(source, padded, window_start, window_end, [])
            with self.asr_lock:
                transcript = self.transcribe(padded)
            origin, window_duration = window_start, window_end-window_start
        segments = []
        for value in transcript.get('segments', []):
            if not isinstance(value.get('text'),str) or isinstance(value.get('start'),bool) or isinstance(value.get('end'),bool):
                raise PipelineError('invalid formal ASR cue')
            a,b,text = float(value['start']),float(value['end']),str(value['text']).strip()
            if not all(math.isfinite(t) for t in (a,b)) or a < 0 or b <= a or b > window_duration+0.01 or not text:
                raise PipelineError('invalid formal ASR timestamps')
            segments.append(TranscriptSegment(origin+a,origin+min(b,window_duration),text))
        if not segments:
            raise PipelineError('formal ASR returned no timestamps')
        snapped = TrimSuggestion(start,end,'selected source range')
        if self.settings.subtitle.snap_enabled:
            snapped = snap_trim_to_segments(snapped,segments,max(0.0,float(self.settings.subtitle.snap_tolerance)))
        if not (0 <= snapped.trim_start < snapped.trim_end <= duration):
            raise PipelineError('invalid snapped source range')
        _, sliced = _slice_segments_to_trim(segments, snapped)
        if not sliced:
            raise PipelineError('formal ASR has no speech in selected range')
        lines = [SubtitleLine(i+1,v.start,v.end,v.text) for i,v in enumerate(sliced)]
        return snapped.trim_start, snapped.trim_end, lines, {'asr_origin':origin,'asr_duration':window_duration,
            'padding':padding,'snap_tolerance':self.settings.subtitle.snap_tolerance,
            'original_start':start,'original_end':end,'source_start':snapped.trim_start,'source_end':snapped.trim_end}
