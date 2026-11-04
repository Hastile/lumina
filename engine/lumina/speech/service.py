"""Sequence-owned speech jobs with immutable input and reviewed, undoable application."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from .. import timebase
from ..commands.registry import CommandError
from ..commands.project import seq_of
from ..commands.text_editing import text_fingerprint
from ..render.export import export
from ..adb.quiet_process import NO_WINDOW


def runtime():
    home = Path(os.environ.get('LUMINA_HOME', Path.home() / '.lumina'))
    candidates = [os.environ.get('LUMINA_SPEECH_PYTHON'), str(home / 'speech-runtime' / 'Scripts' / 'python.exe')]
    candidates += [str(Path(__file__).resolve().parents[2] / '.speech-venv' / 'Scripts' / 'python.exe')]
    candidates += [r'D:\Dev\lumina\engine\.speech-venv\Scripts\python.exe'] if os.name == 'nt' else []
    return next((p for p in candidates if p and Path(p).is_file()), None)


class SpeechService:
    def __init__(self, engine):
        self.engine = engine
        self.jobs = {}
        self.lock = threading.RLock()

    def status(self, _):
        python = runtime()
        return {'available': bool(python), 'runtime': python, 'backend': 'lumina-local', 'language': 'ko',
                'setup': 'Set LUMINA_SPEECH_PYTHON or install the speech runtime. No Phrase server is needed.'}

    def list(self, _):
        with self.lock:
            return [self.public(j) for j in self.jobs.values() if j['project_key'] == self.engine.active]

    def public(self, job):
        return {k: v for k, v in job.items() if k not in ('doc', 'process', 'cancel', 'request')}

    def get(self, p):
        with self.lock:
            j = self.jobs.get(p['job_id'])
            if j is None:
                raise CommandError('NO_JOB', '음성 작업을 찾을 수 없습니다.')
            progress = Path(j['directory']) / 'progress.json'
            if j['status'] == 'running' and progress.exists():
                try:
                    j.update(json.loads(progress.read_text(encoding='utf-8')))
                except (OSError, ValueError):
                    pass
            return self.public(j)

    def start(self, p):
        with self.lock:
            if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise CommandError('BUSY', '진행 중인 음성 작업이 있습니다.')
            python = runtime()
            if not python:
                raise CommandError('NO_SPEECH_RUNTIME', '음성 런타임이 필요합니다. docs/text-panel.md의 설치 방법을 확인하세요.')
            session = self.engine.session
            with session.lock:
                doc = copy.deepcopy(session.doc)
            seq = seq_of(doc, p.get('sequence_id'))
            aid = p.get('audio_track_id')
            if aid not in seq['audio_tracks']:
                raise CommandError('NO_AUDIO_TRACK', '받아쓸 내레이션 트랙을 선택하세요.')
            audio = seq['tracks'][aid]
            enabled = [c for c in audio['clips'].values() if c.get('enabled', True) and not c.get('muted')]
            if not enabled or audio.get('muted'):
                raise CommandError('NO_AUDIO', '선택한 트랙에 들리는 음성이 없습니다.')
            end = max(c['end'] for c in enabled)
            mode = p.get('mode', 'transcribe')
            if mode not in ('transcribe', 'script', 'retime'):
                raise CommandError('BAD_MODE', '지원하지 않는 음성 작업입니다.')
            groups = []
            if mode == 'retime':
                tids = p.get('caption_track_ids') or [tid for tid in seq['video_tracks'] if seq['tracks'][tid].get('caption_style')]
                for tid in tids:
                    if tid not in seq['video_tracks']:
                        raise CommandError('NO_TRACK', '자막 트랙을 찾을 수 없습니다.')
                    track = seq['tracks'][tid]
                    if track.get('locked'):
                        raise CommandError('TRACK_LOCKED', '재정렬할 자막 트랙의 잠금을 해제하세요.')
                    rows = [{'id': c['id'], 'text': c['caption']['text'], 'track_id': tid, 'previous_start': c['start'], 'previous_end': c['end']} for c in sorted(track['clips'].values(), key=lambda c: c['start']) if c.get('caption')]
                    if rows:
                        if any(not r['text'].strip() for r in rows):
                            raise CommandError('EMPTY_TEXT', '빈 자막을 정리한 뒤 다시 정렬하세요.')
                        groups.append(rows)
                if not groups:
                    raise CommandError('NO_CAPTIONS', '재정렬할 자막이 없습니다.')
                chronological = sorted([r for g in groups for r in g], key=lambda r: r['previous_start'])
                if all(a['previous_end'] <= b['previous_start'] for a, b in zip(chronological, chronological[1:])):
                    groups = [chronological]  # Caption effect tracks may alternate within one spoken sentence.
            elif mode == 'script':
                script = (seq.get('transcript') or {}).get('text', '')
                rows = [{'id': uuid.uuid4().hex, 'text': line.strip()} for line in script.splitlines() if line.strip()]
                if not rows:
                    raise CommandError('NO_SCRIPT', '대본을 입력하세요. 줄바꿈을 자막 분할로 사용합니다.')
                groups = [rows]
            root = Path(os.environ.get('LUMINA_HOME', Path.home() / '.lumina')) / 'speech-jobs' / uuid.uuid4().hex
            root.mkdir(parents=True)
            job = {'id': root.name, 'project_key': self.engine.active, 'sequence_id': seq['id'], 'audio_track_id': aid,
                   'mode': mode, 'status': 'queued', 'phase': '내레이션 준비', 'progress': 0, 'directory': str(root),
                   'fingerprint': text_fingerprint(seq, doc.get('media')), 'doc': doc, 'cancel': threading.Event(), 'process': None}
            # Render a private copy: selected track only, with its actual cuts, offsets, gain and speed.
            for tid in seq['audio_tracks']:
                seq['tracks'][tid]['muted'] = tid != aid
                seq['tracks'][tid]['solo'] = tid == aid
            models = Path(os.environ.get('LUMINA_SPEECH_MODELS', Path.home() / '.lumina' / 'models' / 'speech'))
            local_models = Path(r'D:\Dev\lumina\.speech-models')
            if not models.exists() and local_models.exists() and 'LUMINA_SPEECH_MODELS' not in os.environ:
                models = local_models
            job['request'] = {'mode': mode, 'audio': str(root / 'audio.wav'), 'duration': end / timebase.TICKS_PER_SECOND,
                              'models': str(models), 'groups': groups, 'shorts': bool(p.get('shorts', False))}
            self.jobs[job['id']] = job
            threading.Thread(target=self._run, args=(job, python, end), daemon=True, name='lumina-speech').start()
            return self.public(job)

    def _run(self, job, python, end):
        root = Path(job['directory'])
        try:
            job['status'] = 'running'
            result = export(job['doc'], job['sequence_id'], str(root / 'audio.wav'), preset_id='wav', start=0, end=end,
                            cancelled=job['cancel'].is_set)
            if result.get('warnings'):
                raise ValueError('음성 출력 경고: ' + '; '.join(result['warnings']))
            if job['cancel'].is_set():
                raise ValueError('취소됨')
            from ..media.probe import ffmpeg_bin
            # CTC consumes mono 16 kHz signed PCM, independent of sequence sample rate.
            converted = root / 'speech.wav'
            subprocess.run([ffmpeg_bin(), '-v', 'error', '-nostdin', '-y', '-i', str(root / 'audio.wav'),
                            '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(converted)],
                           check=True, capture_output=True, timeout=600, **NO_WINDOW)
            job['request']['audio'] = str(converted)
            (root / 'request.json').write_text(json.dumps(job['request'], ensure_ascii=False), encoding='utf-8')
            env = {**os.environ, 'PATH': str(Path(ffmpeg_bin()).parent) + os.pathsep + os.environ.get('PATH', ''), 'PYTHONIOENCODING': 'utf-8'}
            with (root / 'worker.log').open('wb') as log:
                proc = subprocess.Popen([python, str(Path(__file__).with_name('worker.py')), str(root), str(os.getpid())], env=env, stdout=log, stderr=log, **NO_WINDOW)
                job['process'] = proc
                if job['cancel'].is_set():
                    proc.terminate()
                code = proc.wait()
            if code or not (root / 'result.json').exists():
                error = json.loads((root / 'error.json').read_text(encoding='utf-8'))['error'] if (root / 'error.json').exists() else '음성 작업이 중단됐습니다. 작업 로그를 확인하세요.'
                raise ValueError(error)
            result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            seq = job['doc']['sequences'][job['sequence_id']]
            st = seq['settings']
            from .caption_quality import finish_rows
            raw = result['rows']
            groups = job['request']['groups']
            grouped = [[r for r in raw if r['id'] in {x['id'] for x in g}] for g in groups] if groups else [raw]
            result['rows'], result['quality_warnings'] = [], []
            for group in grouped:
                finished, warnings = finish_rows(group, shorts=job['request']['shorts'],
                    frame_ticks=timebase.ticks_per_frame(st['fps_num'], st['fps_den']))
                result['rows'].extend(finished)
                result['quality_warnings'].extend(warnings)
            result['caption_quality'] = 'gapless-clean-split-v1'
            with self.lock:
                if job['cancel'].is_set():
                    raise ValueError('취소됨')
                job.update(status='preview', phase='결과 확인', progress=100, result=result)
        except Exception as exc:
            job.update(status='cancelled' if job['cancel'].is_set() else 'failed', error=str(exc))
        finally:
            job.pop('doc', None)
            job['process'] = None

    def cancel(self, p):
        with self.lock:
            j = self.jobs.get(p['job_id'])
            if not j:
                raise CommandError('NO_JOB', '음성 작업을 찾을 수 없습니다.')
            j['cancel'].set()
            if j.get('process'):
                j['process'].terminate()
            if j['status'] == 'preview':
                j['status'] = 'cancelled'
            return self.public(j)

    def apply(self, p):
        with self.lock:
            j = self.jobs.get(p['job_id'])
            if not j or j['status'] != 'preview':
                raise CommandError('NO_PREVIEW', '적용할 미리보기가 없습니다.')
            if self.engine.active != j['project_key']:
                raise CommandError('TARGET_CHANGED', '분석한 프로젝트로 돌아가세요.')
            result = self.engine.session.execute('text.apply_speech', {
                'sequence_id': j['sequence_id'], 'expected_fingerprint': j['fingerprint'], 'mode': j['mode'],
                'rows': j['result']['rows'], 'audio_track_id': j['audio_track_id'],
                'transcript_only': bool(p.get('transcript_only', False)) if j['mode'] != 'retime' else False,
            }, '음성 분석 적용')
            j['status'] = 'applied'
            return result
