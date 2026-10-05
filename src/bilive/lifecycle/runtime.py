"""Wire live configuration and recorder state into source deletion checks."""
from pathlib import Path
from bilive.config.settings import load_settings
from bilive.lifecycle.profiles import room_from_source
from bilive.recorder.client import RecorderClient, RecorderUnavailable


def configure_workbench(wb, config_path, recorder=None):
    wb.settings_provider = lambda: load_settings(config_path)
    client = recorder or RecorderClient()

    def recording_active(rel):
        room = room_from_source(rel)
        if not room:
            return False
        try:
            data = client.request(f'/{room}/data')
            task = data.get('task_status', {})
            status = task.get('running_status')
            source = (wb.videos / rel).resolve().with_suffix('')
            active_paths = [task[key] for key in ('recording_path', 'postprocessing_path') if task.get(key)]
            if any(Path(path).resolve().with_suffix('') == source for path in active_paths):
                return True
            if status not in {'stopped', 'waiting', 'recording', 'remuxing', 'injecting'}:
                return True
            return status in {'recording', 'remuxing', 'injecting'} and not active_paths
        except (RecorderUnavailable, ValueError):
            return True
    wb.recording_active_provider = recording_active
