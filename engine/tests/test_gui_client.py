"""GUI-first MCP: it can start idle, but it never edits a detached local document."""
import json

import pytest

from lumina import client
from lumina.commands.registry import CommandError


@pytest.fixture
def gui_env(tmp_path, monkeypatch):
    path = tmp_path / 'engine.json'
    monkeypatch.setattr('lumina.server.discovery_path', lambda: path)
    gui = client.LocalEngine(run_jobs=False)
    gui.endpoint = ('127.0.0.1', 1234, 'test')

    def announce():
        path.write_text(json.dumps(dict(host='127.0.0.1', port=1234, token='test')))

    monkeypatch.setattr(client, '_remote', lambda: gui if path.exists() else None)
    yield gui, announce
    gui.close()


def test_mcp_can_start_and_report_idle_without_creating_engine_or_gui(monkeypatch):
    def forbidden():
        raise AssertionError('startup must stay idle')
    monkeypatch.setattr(client, 'LocalEngine', forbidden)
    monkeypatch.setattr(client, '_launch_editor', forbidden)
    monkeypatch.setattr(client, '_endpoint', lambda: None)
    e = client.GuiEngine()
    assert e.current is None
    assert e.dispatch('editor.status') == dict(mode='gui', connected=False, headless=False, projects=[])


def test_first_work_starts_gui_and_edits_only_its_document(gui_env, monkeypatch):
    gui, announce = gui_env
    launches = []
    monkeypatch.setattr(client, '_launch_editor', lambda: (launches.append(True), announce()))
    e = client.GuiEngine()
    assert e.dispatch('editor.ensure')['case'] == 'gui_started'
    e.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'visible'}})
    assert gui.dispatch('snapshot')['document']['name'] == 'visible'
    assert launches == [True]


def test_already_open_target_keeps_unsaved_gui_edits(gui_env, monkeypatch, tmp_path):
    gui, announce = gui_env
    path = str(tmp_path / 'target.lumina')
    gui.dispatch('project.save', {'path': path})
    gui.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'manual unsaved'}})
    announce()
    e = client.GuiEngine()
    status = e.dispatch('editor.ensure', {'project_path': path})
    assert status['case'] == 'project_already_open'
    assert len(status['projects']) == 1 and status['projects'][0]['dirty']
    assert gui.dispatch('snapshot')['document']['name'] == 'manual unsaved'


def test_other_project_stays_open_while_target_is_opened(gui_env, tmp_path):
    gui, announce = gui_env
    target = client.LocalEngine(run_jobs=False)
    target.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'target'}})
    path = str(tmp_path / 'target.lumina')
    target.dispatch('project.save', {'path': path})
    target.close()
    gui.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'other unsaved'}})
    announce()
    status = client.GuiEngine().dispatch('editor.ensure', {'project_path': path})
    assert status['case'] == 'project_opened' and len(status['projects']) == 2
    assert gui.dispatch('snapshot')['document']['name'] == 'target'
    assert next(p for p in status['projects'] if p['name'] == 'other unsaved')['dirty']


def test_launch_failure_never_falls_back_to_headless(gui_env, monkeypatch):
    monkeypatch.setattr(client, '_launch_editor', lambda: None)
    e = client.GuiEngine(startup_timeout=0)
    with pytest.raises(CommandError) as error:
        e.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'invisible'}})
    assert error.value.code == 'GUI_START_TIMEOUT' and e.current is None


def test_close_mcp_disconnects_without_closing_gui_project(gui_env):
    gui, announce = gui_env
    announce()
    e = client.GuiEngine()
    e.dispatch('editor.ensure')
    e.close()
    assert len(gui.dispatch('project.list')) == 1


def test_manual_project_switch_cannot_redirect_agent_edits(gui_env):
    gui, announce = gui_env
    gui.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'intended project'}})
    announce()
    e = client.GuiEngine()
    e.dispatch('editor.ensure')
    gui.dispatch('project.new', {'name': 'other manual work'})
    with pytest.raises(CommandError) as error:
        e.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'wrong target'}})
    assert error.value.code == 'TARGET_CHANGED'
    assert gui.dispatch('snapshot')['document']['name'] == 'other manual work'
