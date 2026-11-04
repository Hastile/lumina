"""The MCP server's engine follows the GUI: picked up once it starts, reconnected after a restart."""

import pytest
import json

from lumina import client
from lumina.commands.registry import CommandError


class Fake:
    def __init__(self, name, fail=None):
        self.name, self.fail, self.calls, self.closed = name, fail, [], False

    def dispatch(self, method, params=None):
        if self.fail is not None:
            raise client.EngineGone(sent=self.fail)
        self.calls.append(method)
        return [{"blank": True, "dirty": False}] if method == "project.list" else self.name

    def close(self):
        self.closed = True


class FakeLocal(Fake, client.LocalEngine):
    pass


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    path = tmp_path / "engine.json"
    monkeypatch.setattr("lumina.server.discovery_path", lambda: path)
    return path


def announce(path, port=1234):
    path.write_text(json.dumps(dict(host='127.0.0.1', port=port, token='test')))


def test_a_gui_started_later_is_picked_up_while_headless_is_blank(discovery, monkeypatch):
    local = FakeLocal("local")
    e = client.FollowingEngine(local)
    assert e.dispatch("x") == "local"
    gui = Fake("gui")
    announce(discovery)
    monkeypatch.setattr(client, "_remote", lambda: gui)
    assert e.dispatch("x") == "gui" and local.closed


class GUI:
    def __init__(self, endpoint=('127.0.0.1', 1234, 'test')):
        self.local = client.LocalEngine(run_jobs=False)
        self.endpoint = endpoint
        self.closed = False

    def dispatch(self, method, params=None):
        return self.local.dispatch(method, params)

    def close(self):
        self.closed = True
        self.local.close()


def rename(engine, name):
    return engine.dispatch('execute', {'name': 'project.rename', 'params': {'name': name}})


def test_unsaved_headless_work_moves_to_gui_and_edits_are_shared(discovery, monkeypatch):
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'headless work')
    gui = GUI()
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'headless work'
        recovery = list((discovery.parent / 'handoff').glob('*.lumina'))
        assert len(recovery) == 1
        assert json.loads(recovery[0].read_text(encoding='utf-8'))['document']['name'] == 'headless work'
        rename(e, 'agent visible')
        assert gui.dispatch('snapshot')['document']['name'] == 'agent visible'
        rename(gui, 'manual visible')
        assert e.dispatch('snapshot')['document']['name'] == 'manual visible'
    finally:
        e.close()


def test_gui_unsaved_project_is_not_overwritten_by_headless_handoff(discovery, monkeypatch):
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'agent unsaved')
    gui = GUI()
    rename(gui, 'user unsaved')
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'user unsaved'
        assert sorted(p['name'] for p in gui.dispatch('project.list'))[1] == 'user unsaved'
        assert len(gui.dispatch('project.list')) == 2
    finally:
        e.close()


def test_saved_nonblank_headless_project_attaches_to_gui(discovery, monkeypatch, tmp_path):
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'saved agent work')
    local.dispatch('project.save', {'path': str(tmp_path / 'saved.lumina')})
    gui = GUI()
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'saved agent work'
        assert not (discovery.parent / 'handoff').exists()
    finally:
        e.close()


def test_new_gui_endpoint_wins_even_when_old_engine_is_still_alive(discovery, monkeypatch):
    old, new = GUI(), GUI(('127.0.0.1', 5678, 'test'))
    rename(new, 'current GUI')
    e = client.FollowingEngine(old)
    announce(discovery, 5678)
    monkeypatch.setattr(client, '_remote', lambda: new)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'current GUI'
        assert old.closed
    finally:
        e.close()


def test_handoff_keeps_the_active_headless_project(discovery, monkeypatch):
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'first')
    first = local.engine.active
    local.dispatch('project.new', {'name': 'second'})
    local.dispatch('project.activate', {'key': first})
    gui = GUI()
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'first'
        assert len(gui.dispatch('project.list')) == 2
    finally:
        e.close()


def test_saved_headless_state_is_recovered_if_disk_was_overwritten(discovery, monkeypatch, tmp_path):
    path = str(tmp_path / 'shared.lumina')
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'agent latest')
    local.dispatch('project.save', {'path': path})
    overwritten = client.LocalEngine(run_jobs=False)
    rename(overwritten, 'old GUI saved over disk')
    overwritten.dispatch('project.save', {'path': path})
    overwritten.close()
    gui = GUI()
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'agent latest'
        assert len(list((discovery.parent / 'handoff').glob('*.lumina'))) == 1
    finally:
        e.close()


def test_same_file_open_in_gui_keeps_both_versions(discovery, monkeypatch, tmp_path):
    path = str(tmp_path / 'shared.lumina')
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'saved agent work')
    local.dispatch('project.save', {'path': path})
    gui = GUI()
    gui.dispatch('project.open', {'path': path})
    rename(gui, 'manual unsaved work')
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'manual unsaved work'
        projects = e.dispatch('project.list')
        assert len(projects) == 2
        recovered = next(p for p in projects if p['path'] != path)
        e.dispatch('project.activate', {'key': recovered['key']})
        assert e.dispatch('snapshot')['document']['name'] == 'saved agent work'
        assert json.loads(open(path, encoding='utf-8').read())['document']['name'] == 'saved agent work'
    finally:
        e.close()


def test_same_file_open_in_gui_keeps_both_versions(discovery, monkeypatch, tmp_path):
    path = str(tmp_path / 'shared.lumina')
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'saved agent work')
    local.dispatch('project.save', {'path': path})
    gui = GUI()
    gui.dispatch('project.open', {'path': path})
    rename(gui, 'manual unsaved work')
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        assert e.dispatch('snapshot')['document']['name'] == 'manual unsaved work'
        projects = e.dispatch('project.list')
        assert len(projects) == 2
        recovered = next(p for p in projects if p['path'] != path)
        e.dispatch('project.activate', {'key': recovered['key']})
        assert e.dispatch('snapshot')['document']['name'] == 'saved agent work'
        assert json.loads(open(path, encoding='utf-8').read())['document']['name'] == 'saved agent work'
    finally:
        e.close()


def test_open_transaction_finishes_before_handoff(discovery, monkeypatch):
    local = client.LocalEngine(run_jobs=False)
    local.dispatch('transaction.begin', {'label': 'complete edit'})
    rename(local, 'transaction work')
    gui = GUI()
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        e.dispatch('transaction.commit')
        assert e.current is local
        assert e.dispatch('snapshot')['document']['name'] == 'transaction work'
        assert e.current is gui
    finally:
        e.close()


def test_failed_handoff_never_executes_a_detached_write(discovery, monkeypatch):
    local = client.LocalEngine(run_jobs=False)
    rename(local, 'preserve this')
    gui = GUI()
    original = gui.dispatch

    def fail_open(method, params=None):
        if method == 'project.open':
            raise OSError('unavailable GUI project')
        return original(method, params)

    gui.dispatch = fail_open
    e = client.FollowingEngine(local)
    announce(discovery)
    monkeypatch.setattr(client, '_remote', lambda: gui)
    try:
        with pytest.raises(OSError):
            rename(e, 'must not run')
        assert e.current is local
        assert local.dispatch('snapshot')['document']['name'] == 'preserve this'
        assert len(list((discovery.parent / 'handoff').glob('*.lumina'))) == 1
    finally:
        e.close()


def test_a_restarted_gui_is_reconnected(discovery, monkeypatch):
    new = Fake("new gui")
    monkeypatch.setattr(client, "_remote", lambda: new)
    # lost before it was sent: resent on the new engine
    e = client.FollowingEngine(Fake("old", fail=False))
    assert e.dispatch("x") == "new gui"
    # maybe ran already: reported, not repeated
    e = client.FollowingEngine(Fake("old", fail=True))
    with pytest.raises(CommandError) as err:
        e.dispatch("x")
    assert err.value.code == "ENGINE_RESTARTED" and new.calls == ["x"]
    assert e.dispatch("y") == "new gui"
    # no GUI any more
    monkeypatch.setattr(client, "_remote", lambda: None)
    e = client.FollowingEngine(Fake("old", fail=False))
    with pytest.raises(CommandError) as err:
        e.dispatch("x")
    assert err.value.code == "NO_ENGINE"
