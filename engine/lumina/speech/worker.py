"""Isolated ML worker. JSON files isolate large model dependencies from the editor process."""
import json
import os
from pathlib import Path
import re
import sys
import site
import threading
import time


def watch_parent(pid):
    """Release GPU memory when the editor engine exits, including forced shutdown."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(0)
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            os._exit(0)
        time.sleep(1)

TPS = 254016000000
DLL_HANDLES = []
if os.name == 'nt':
    for package_root in set(site.getsitepackages() + [site.getusersitepackages()]):
        for directory in Path(package_root).glob('nvidia/*/bin'):
            os.environ['PATH'] = str(directory) + os.pathsep + os.environ.get('PATH', '')
            DLL_HANDLES.append(os.add_dll_directory(str(directory)))


def write(path, value, *, best_effort=False):
    """Atomically publish JSON, tolerating Windows readers briefly holding the target."""
    tmp = path.with_suffix('.partial')
    payload = json.dumps(value, ensure_ascii=False)
    for attempt in range(8):
        try:
            tmp.write_text(payload, encoding='utf-8')
            tmp.replace(path)
            return True
        except OSError as exc:
            if not isinstance(exc, PermissionError) and getattr(exc, 'winerror', None) not in (5, 32, 33):
                raise
            if attempt == 7:
                if best_effort:
                    return False
                raise
            time.sleep(min(.01 * 2 ** attempt, .2))


def map_rows(rows, words, *, close_gaps=False):
    """Map exact existing strings to acoustic character positions; never estimate by text length."""
    compact = lambda s: ''.join(s.split())
    if ''.join(compact(r['text']) for r in rows) != ''.join(compact(w['word']) for w in words):
        raise ValueError('음성 정렬 결과와 대본이 달라 적용할 수 없습니다.')
    characters = []
    for w in words:
        chars = w.get('chars')
        if not chars:
            raise ValueError('글자 단위 음성 정렬 정보가 없습니다. 정밀 정렬을 사용하세요.')
        characters.extend(c for c in chars if not c['char'].isspace())
    if ''.join(c['char'] for c in characters) != ''.join(compact(r['text']) for r in rows):
        raise ValueError('글자 정렬 정보와 대본이 일치하지 않습니다.')
    result, index = [], 0
    for row in rows:
        mapped = []
        for offset, char in enumerate(row['text']):
            if char.isspace():
                continue
            c = characters[index]
            mapped.append({'offset': offset, 'start': round(c['start'] * TPS), 'end': round(c['end'] * TPS), 'score': c.get('score', 0)})
            index += 1
        result.append({**row, 'start': mapped[0]['start'], 'end': mapped[-1]['end'], 'chars': mapped})
    # Preserve silence as silence; only trim overlapping model boundaries, do not fill gaps.
    for left, right in zip(result, result[1:]):
        left['end'] = right['start'] if close_gaps else min(left['end'], right['start'])
    return result


def run(request, progress):
    import torch
    import stable_whisper
    from precise_timing import align_audio
    import longform_split
    import shorts_split

    models = Path(request['models'])
    models.mkdir(parents=True, exist_ok=True)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    progress('음성 모델 준비', 5)
    model = stable_whisper.load_faster_whisper('large-v3', device=device, compute_type='float16' if device == 'cuda' else 'int8', download_root=str(models))
    audio = request['audio']
    if request['mode'] in ('retime', 'script'):
        groups = request['groups']
        output = []
        for i, rows in enumerate(groups):
            script = '\n'.join(r['text'] for r in rows)
            progress('대본과 음성 정렬', 10 + i * 70 / len(groups))
            aligned = model.align(audio, script, language='ko', original_split=True, verbose=None)
            if aligned is None:
                raise ValueError('대본을 음성에 맞추지 못했습니다.')
            words = [w for s in aligned.to_dict()['segments'] for w in s.get('words', [])]
            words = align_audio(Path(audio), words, models, lambda n: progress('한국어 정밀 정렬', 20 + n * .7), device=device)
            output.extend(map_rows(rows, words, close_gaps=True))
        return {'rows': output, 'device': device, 'timing_engine': 'korean-ctc-v1'}
    progress('음성을 받아쓰는 중', 10)
    segments, _ = model.transcribe_original(audio, language='ko', word_timestamps=True, vad_filter=True, beam_size=5)
    words = []
    for segment in segments:
        words.extend({'word': w.word, 'start': w.start, 'end': w.end} for w in (segment.words or []))
        progress('음성을 받아쓰는 중', min(65, 10 + segment.end / request['duration'] * 55))
    if not words:
        raise ValueError('선택한 트랙에서 발화를 찾지 못했습니다.')
    words = align_audio(Path(audio), words, models, lambda n: progress('한국어 정밀 정렬', 65 + n * .25), device=device)
    splitter = shorts_split if request['shorts'] else longform_split
    if request['shorts']:
        import uuid
        starts = shorts_split.split_indices(words)
        rows = [{'id': uuid.uuid4().hex, 'text': ' '.join(w['word'].strip() for w in words[a:b])}
                for a, b in zip(starts, starts[1:] + [len(words)])]
    else:
        segments = splitter.split_segments(words, request['duration'])
        rows = [{'id': s['id'], 'text': s['text']} for s in segments]
    return {'rows': map_rows(rows, words), 'device': device, 'timing_engine': 'korean-ctc-v1'}


if __name__ == '__main__':
    if len(sys.argv) > 2:
        threading.Thread(target=watch_parent, args=(int(sys.argv[2]),), daemon=True).start()
    root = Path(sys.argv[1])
    try:
        request = json.loads((root / 'request.json').read_text(encoding='utf-8'))
        result = run(request, lambda phase, percent: write(root / 'progress.json', {'phase': phase, 'progress': round(percent)}, best_effort=True))
        write(root / 'result.json', result)
    except Exception as exc:
        write(root / 'error.json', {'error': str(exc)})
        raise
