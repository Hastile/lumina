"""The MCP server's engine follows the GUI: picked up once it starts, reconnected after a restart."""

import pytest

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


def test_a_gui_started_later_is_picked_up_while_headless_is_blank(discovery, monkeypatch):
    local = FakeLocal("local")
    e = client.FollowingEngine(local)
    assert e.dispatch("x") == "local"
    gui = Fake("gui")
    discovery.write_text("{}")
    monkeypatch.setattr(client, "_remote", lambda: gui)
    assert e.dispatch("x") == "gui" and local.closed


def test_headless_work_is_not_abandoned_for_a_gui(discovery, monkeypatch):
    local = FakeLocal("local")
    local.dispatch = lambda m, p=None: [{"blank": False, "dirty": True}] if m == "project.list" else "local"
    e = client.FollowingEngine(local)
    discovery.write_text("{}")
    monkeypatch.setattr(client, "_remote", lambda: Fake("gui"))
    assert e.dispatch("x") == "local"


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
