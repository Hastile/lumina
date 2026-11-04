import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import pytest
from lumina.mobile_monitor import handler, Monitor


def test_http_is_read_only_and_does_not_serve_files(tmp_path):
    video=tmp_path/'test.mp4'
    video.write_bytes(b'0123456789')
    class Fake:
        def state(self): return {'read_only': True}
        def frame(self, project, sequence, ticks):
            assert (project,sequence,ticks)==('p','s',0)
            return b'jpeg-test'
        def video(self, token):
            assert token=='known'
            return video
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(Fake()))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root=f'http://127.0.0.1:{server.server_port}'
    try:
        assert json.load(urlopen(root+'/api/state'))['read_only']
        assert urlopen(root+'/api/frame?project=p&sequence=s&ticks=0').read()==b'jpeg-test'
        assert b'viewport' in urlopen(root).read()
        ranged=urlopen(Request(root+'/video?id=known',headers={'Range':'bytes=2-5'}))
        assert ranged.status==206 and ranged.read()==b'2345'
        assert urlopen(Request(root+'/video?id=known',headers={'Range':'bytes=-3'})).read()==b'789'
        with pytest.raises(HTTPError) as error:urlopen(Request(root+'/video?id=known',headers={'Range':'bytes=20-'}))
        assert error.value.code==416
        with pytest.raises(HTTPError) as error:urlopen(Request(root+'/api/preview',data=b'{}',method='POST'))
        assert error.value.code==403
        for method in ['POST','PUT','PATCH','DELETE']:
            with pytest.raises(HTTPError) as error:urlopen(Request(root+'/api/state',method=method))
            assert error.value.code==405
        for path in ['/execute','/api/call','/../server.py','/engine.json']:
            with pytest.raises(HTTPError) as error:urlopen(root+path)
            assert error.value.code==404
    finally:
        server.shutdown();server.server_close();thread.join()


def test_frame_refuses_wrong_project(monkeypatch):
    calls=[]
    class Fake:
        def dispatch(self, name, args):
            calls.append(name)
            return [{'key':'current','active':True}]
        def close(self): pass
    monkeypatch.setattr('lumina.mobile_monitor._remote',lambda:Fake())
    with pytest.raises(ValueError):Monitor().frame('old','seq',0)
    assert calls==['project.list']
