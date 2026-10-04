"""Read persisted recording evidence and bounded file occupancy; no processing."""
import json
from pathlib import Path
from bilive.domain.models import source_relpath_from_task_id


def recording_evidence(wb,task,limit=20,offset=0):
    rel=source_relpath_from_task_id(task);source=wb.source(rel)
    history_path=source.with_suffix('.mp4.task.json')
    history=json.loads(history_path.read_text(encoding='utf-8')) if history_path.is_file() else None
    candidates=(history or {}).get('candidates',[])
    files=set()
    for suffix in ('.mp4','.flv','.xml','.flv.meta','.flv.meta.json','.json','.ass','.mp4.task.json',
                   '.mp4.pending','.mp4.processing','.mp4.failed','.mp4.done'):
        path=source.with_suffix(suffix).resolve()
        if path.is_relative_to(wb.videos) and path.is_file():files.add(path)
    candidate_root=(wb.artifacts/'candidates'/task).resolve()
    if candidate_root.is_relative_to(wb.artifacts):
        files.update(p.resolve() for p in candidate_root.rglob('*') if p.is_file() and p.resolve().is_relative_to(candidate_root))
    segment_files=[]
    for s in wb.list(rel):
        directory=(wb.artifacts/s['segment_id']).resolve()
        if not directory.is_relative_to(wb.artifacts):raise ValueError('segment artifacts outside root')
        artifacts=[]
        for path in sorted(directory.glob('*')):
            path=path.resolve()
            if path.is_file() and path.is_relative_to(directory):
                files.add(path)
                artifacts.append({'name':path.name,'size':path.stat().st_size})
        segment_files.append({'segment_id':s['segment_id'],'title':s['title'],'state':s['state'],
            'stage':s.get('processing_stage'),'error':s.get('processing_error'),'timings':s.get('timings',{}),'artifacts':artifacts})
    return {'source_size':source.stat().st_size,'related_files_size':sum(p.stat().st_size for p in files),
            'file_count':len(files),'history_available':history is not None,
            'status':(history or {}).get('status'),'error':(history or {}).get('error'),
            'skipped':(history or {}).get('skipped',False),'skip_reason':(history or {}).get('skip_reason'),
            'candidates':candidates[offset:offset+limit],'total':len(candidates),'limit':limit,'offset':offset,
            'segments':segment_files}
