"""Integration check with a supplied short audio fixture; never opens a user project."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

root = Path(__file__).resolve().parents[1]
home = root / '.tmp' / ('internal-speech-' + uuid.uuid4().hex)
home.mkdir(parents=True)
os.environ['LUMINA_HOME'] = str(home)
os.environ['LUMINA_SPEECH_MODELS'] = str(root / '.speech-models')
os.environ['LUMINA_TEXT_STYLES'] = str(home / 'styles.json')
os.environ['LUMINA_PRESETS'] = str(home / 'presets.json')
from lumina.engine import Engine

engine = Engine(run_jobs=False)
mid = engine.dispatch('execute', {'name': 'media.import', 'params': {'paths': [sys.argv[1]]}})['result']['media_ids'][0]
sid = engine.dispatch('execute', {'name': 'sequence.create_from_media', 'params': {'media_id': mid}})['result']['sequence_id']
seq = engine.session.doc['sequences'][sid]
job = engine.dispatch('speech.start', {'sequence_id': sid, 'audio_track_id': seq['audio_tracks'][0], 'mode': 'transcribe', 'shorts': False})
last = None
deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    job = engine.dispatch('speech.job', {'job_id': job['id']})
    state = (job['status'], job['phase'], job['progress'])
    if state != last:
        print(state, flush=True); last = state
    if job['status'] not in ('queued', 'running'):
        break
    time.sleep(1)
assert job['status'] == 'preview', job
rows = job['result']['rows']
assert rows and all(r['chars'] for r in rows)
engine.dispatch('speech.apply', {'job_id': job['id']})
caps = [c for t in seq['tracks'].values() for c in t['clips'].values() if c.get('caption')]
assert len(caps) == len(rows)
engine.dispatch('undo')
assert not [c for t in seq['tracks'].values() for c in t['clips'].values() if c.get('caption')]
engine.dispatch('redo')
before = {c['id']: c['caption']['text'] for t in seq['tracks'].values() for c in t['clips'].values() if c.get('caption')}
retime = engine.dispatch('speech.start', {'sequence_id': sid, 'audio_track_id': seq['audio_tracks'][0], 'mode': 'retime'})
deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    retime = engine.dispatch('speech.job', {'job_id': retime['id']})
    if retime['status'] not in ('queued', 'running'):
        break
    time.sleep(1)
assert retime['status'] == 'preview', retime
assert {r['id']: r['text'] for r in retime['result']['rows']} == before
engine.dispatch('speech.apply', {'job_id': retime['id']})
after = {c['id']: c['caption']['text'] for t in seq['tracks'].values() for c in t['clips'].values() if c.get('caption')}
assert after == before
print('PASS: retime preserves caption IDs and text', flush=True)
out = home / 'verification.json'
out.write_text(json.dumps({'rows': rows, 'device': job['result']['device'], 'checks': ['timeline audio render', 'transcribe', 'CTC characters', 'apply captions', 'undo', 'redo', 'retime preserves IDs and text']}, ensure_ascii=False, indent=2), encoding='utf-8')
print('PASS:', out, flush=True)
