import json
import os
from pathlib import Path
import threading

import pytest

from lumina.speech.worker import write


def test_publish_retries_transient_permission_error(tmp_path, monkeypatch):
    original = Path.replace
    calls = []
    def replace(path, target):
        calls.append(target)
        if len(calls) < 3:
            raise PermissionError('reader has file open')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    path = tmp_path / 'progress.json'
    assert write(path, {'progress': 40}, best_effort=True)
    assert json.loads(path.read_text()) == {'progress': 40}
    assert len(calls) == 3


def test_progress_is_optional_but_result_write_failure_is_fatal(tmp_path, monkeypatch):
    def locked(*args):
        raise PermissionError('locked')
    monkeypatch.setattr(Path, 'replace', locked)
    monkeypatch.setattr('lumina.speech.worker.time.sleep', lambda _: None)
    assert write(tmp_path / 'progress.json', {}, best_effort=True) is False
    with pytest.raises(PermissionError):
        write(tmp_path / 'result.json', {})


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing regression')
def test_publish_while_windows_reader_holds_target(tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    path = tmp_path / 'progress.json'
    path.write_text('{}')
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    timer = threading.Timer(.12, lambda: kernel.CloseHandle(handle))
    timer.start()
    try:
        assert write(path, {'progress': 100})
        assert json.loads(path.read_text())['progress'] == 100
    finally:
        timer.join()
