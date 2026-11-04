"""Tailnet-only read-only viewer. No generic RPC, file serving or edit endpoint."""
import argparse
import io
import ipaddress
import json
import threading
import tempfile
import time
import uuid
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from .client import _remote


class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = None
        self.previews = {}
        self.preview_dir = tempfile.TemporaryDirectory(prefix='lumina-mobile-play-')

    def preview(self, project, sequence, ticks, duration):
        if type(ticks) is not int or ticks < 0 or duration not in (15, 30, 60):
            raise ValueError('미리보기는 15/30/60초 구간만 지원합니다')
        for token in list(self.previews):
            self.preview_status(token)
        with self.lock:
            if any(p['status'] in ('queued', 'running') for p in self.previews.values()):
                raise ValueError('이미 미리보기를 준비 중입니다')
            engine = _remote()
            if engine is None:
                raise RuntimeError('Lumina 연결 없음')
            try:
                active = next(p for p in engine.dispatch('project.list', {}) if p['active'])
                if active['key'] != project:
                    raise ValueError('프로젝트가 바뀌었습니다')
                t = engine.dispatch('timeline.state', {'sequence_id': sequence})
                if ticks >= t['duration']['ticks']:
                    raise ValueError('재생할 구간이 없습니다')
                end = min(t['duration']['ticks'], ticks + duration * 254016000000)
                token = uuid.uuid4().hex
                path = Path(self.preview_dir.name) / (token + '.mp4')
                job = engine.dispatch('export.start', {'sequence_id': sequence, 'path': str(path),
                    'preset': 'h264', 'start': ticks, 'end': end, 'wait': False})
                result = {'id': token, 'job': job['id'], 'project': project, 'sequence': sequence,
                          'start': ticks, 'end': end, 'status': 'queued', 'progress': 0,
                          'created': time.time(), 'path': path}
                self.previews[token] = result
                while len(self.previews) > 4:
                    old = next(iter(self.previews))
                    self.previews.pop(old)['path'].unlink(missing_ok=True)
                return self.public_preview(result)
            finally:
                engine.close()

    @staticmethod
    def public_preview(item):
        return {k: v for k, v in item.items() if k not in ('path', 'job')}

    def preview_status(self, token):
        with self.lock:
            item = self.previews.get(token)
            if item is None:
                raise ValueError('미리보기가 만료되었습니다')
            if item['status'] in ('queued', 'running'):
                engine = _remote()
                if engine is None:
                    item.update(status='failed', error='Lumina 연결 끊김. 새로 준비하세요')
                else:
                    try:
                        jobs = engine.dispatch('jobs.list', {})
                        job = next((j for j in jobs if j['id'] == item['job']), None)
                        if job is None:
                            item.update(status='failed', error='작업을 찾을 수 없습니다. 새로 준비하세요')
                        else:
                            item.update(status=job['status'], progress=job.get('progress', 0))
                            if job.get('error'):
                                item['error'] = '렌더 실패. PC Lumina의 내보내기 상태를 확인하세요'
                    finally:
                        engine.close()
            return self.public_preview(item)

    def video(self, token):
        item = self.previews.get(token)
        if item is None or item['status'] != 'done' or not item['path'].is_file():
            raise ValueError('재생 파일이 아직 준비되지 않았습니다')
        return item['path']

    def state(self):
        with self.lock:
            engine = _remote()
            if engine is None:
                raise RuntimeError('Lumina를 PC에서 열어주세요')
            try:
                projects = engine.dispatch('project.list', {})
                project = next(p for p in projects if p['active'])
                summary = engine.dispatch('project.summary', {})
                timeline = engine.dispatch('timeline.state', {})
                after = next(p for p in engine.dispatch('project.list', {}) if p['active'])
                if after['key'] != project['key']:
                    raise RuntimeError('프로젝트 변경 중')
                # The mobile client never receives media paths or engine credentials.
                return {'project': {k: project[k] for k in ('key', 'name', 'dirty')},
                        'timeline': timeline, 'sequences': summary['sequences'],
                        'read_only': True, 'updated': time.time()}
            finally:
                engine.close()

    def frame(self, project, sequence, ticks):
        if ticks < 0:
            raise ValueError('negative time')
        with self.lock:
            engine = _remote()
            if engine is None:
                raise RuntimeError('Lumina 연결 없음')
            try:
                active = next(p for p in engine.dispatch('project.list', {}) if p['active'])
                if active['key'] != project:
                    raise ValueError('프로젝트가 바뀌었습니다. 새로고침하세요')
                summary = engine.dispatch('project.summary', {})
                seq = next((s for s in summary['sequences'] if s['id'] == sequence), None)
                if seq is None or ticks > seq['duration_seconds'] * 254016000000:
                    raise ValueError('시퀀스 범위 밖')
                key = (project, sequence, ticks)
                if self.cache and self.cache[0] == key and time.monotonic() - self.cache[1] < 1:
                    return self.cache[2]
                with tempfile.TemporaryDirectory(prefix='lumina-mobile-') as directory:
                    path = Path(directory) / 'frame.png'
                    engine.dispatch('export.frame', {'sequence_id': sequence, 'time': ticks,
                                                     'max_size': 960, 'path': str(path)})
                    after = next(p for p in engine.dispatch('project.list', {}) if p['active'])
                    if after['key'] != project:
                        raise ValueError('검수 중 프로젝트 변경')
                    with Image.open(path) as image:
                        out = io.BytesIO()
                        image.convert('RGB').save(out, 'JPEG', quality=80)
                data = out.getvalue()
                self.cache = (key, time.monotonic(), data)
                return data
            finally:
                engine.close()


def handler(monitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, kind='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            route = urlsplit(self.path)
            try:
                if route.path == '/':
                    self.reply(200, Path(__file__).with_name('mobile_monitor.html').read_bytes(), 'text/html; charset=utf-8')
                elif route.path == '/api/state':
                    self.reply(200, json.dumps(monitor.state(), ensure_ascii=False).encode())
                elif route.path == '/api/frame':
                    args = parse_qs(route.query)
                    self.reply(200, monitor.frame(args['project'][0], args['sequence'][0], int(args['ticks'][0])), 'image/jpeg')
                elif route.path == '/api/preview':
                    args = parse_qs(route.query)
                    self.reply(200, json.dumps(monitor.preview_status(args['id'][0]), ensure_ascii=False).encode())
                elif route.path == '/video':
                    self.send_video(monitor.video(parse_qs(route.query)['id'][0]))
                else:
                    self.reply(404, b'{"error":"not found"}')
            except (ValueError, KeyError) as exc:
                self.reply(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())
            except Exception:
                self.reply(503, json.dumps({'error': 'Lumina 연결/렌더 대기 중. PC에서 프로젝트를 열어주세요.'}, ensure_ascii=False).encode())

        def do_POST(self):
            if self.path != '/api/preview':
                return self.reply(405, b'{"error":"read only"}')
            if self.headers.get('X-Lumina-Preview') != '1':
                return self.reply(403, b'{"error":"preview request required"}')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2048:
                    raise ValueError('invalid request size')
                body = json.loads(self.rfile.read(size))
                result = monitor.preview(body['project'], body['sequence'], body['ticks'], body['duration'])
                self.reply(200, json.dumps(result).encode())
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())
            except Exception:
                self.reply(503, b'{"error":"preview unavailable"}')

        def deny(self):
            self.reply(405, b'{"error":"read only"}')
        do_PUT = do_DELETE = do_PATCH = deny

        def send_video(self, path):
            size = path.stat().st_size
            start, end = 0, size - 1
            header = self.headers.get('Range')
            if header:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
                if not match or not any(match.groups()):
                    return self.reply(416, b'')
                a, b = match.groups()
                if a:
                    start = int(a)
                    end = min(int(b), end) if b else end
                else:
                    start = max(0, size - int(b))
                if start > end or start >= size:
                    return self.reply(416, b'')
            self.send_response(206 if header else 200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(end - start + 1))
            if header:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as file:
                    file.seek(start)
                    left = end - start + 1
                    while left:
                        block = file.read(min(left, 65536))
                        if not block:
                            break
                        self.wfile.write(block)
                        left -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    host = ipaddress.ip_address(args.host)
    if host not in ipaddress.ip_network('100.64.0.0/10') and not host.is_loopback:
        parser.error('Only a Tailscale IPv4 or loopback address is allowed')
    server = ThreadingHTTPServer((str(host), args.port), handler(Monitor()))
    print(f'Lumina read-only monitor: http://{host}:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
