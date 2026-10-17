"""Several open projects, closing with unsaved changes, and the media cache."""

import os
import time

import pytest

from lumina import media_cache
from lumina.commands.registry import CommandError
from lumina.engine import Engine

from .test_session import FakeServices


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("LUMINA_CACHE", raising=False)
    monkeypatch.setattr(media_cache, "default_root", lambda: tmp_path / "cache")


def make():
    e = Engine(services=FakeServices(), run_jobs=False)
    events = []
    e.subscribe(events.append)
    return e, events


def ex(e, name, params=None):
    return e.dispatch("execute", {"name": name, "params": params or {}})["result"]


def test_new_reuses_the_blank_project_then_adds(tmp_path):
    e, events = make()
    e.dispatch("project.new", {"name": "A"})
    assert [p["name"] for p in e.dispatch("project.list")] == ["A"]
    ex(e, "sequence.create", {"name": "S"})
    e.dispatch("project.new", {"name": "B"})
    lst = e.dispatch("project.list")
    assert [p["name"] for p in lst] == ["A", "B"]
    assert lst[1]["active"] and lst[0]["dirty"]
    assert e.session.doc["name"] == "B" and not e.session.doc["sequences"]
    assert events[-1]["type"] == "projects"
    assert any(ev["type"] == "reset" and ev["document"]["name"] == "B" for ev in events)
    # switching back shows A's document again
    e.dispatch("project.activate", {"key": lst[0]["key"]})
    assert e.session.doc["name"] == "A" and e.session.doc["sequences"]


def test_events_of_background_projects_are_not_forwarded():
    e, events = make()
    e.dispatch("project.new", {"name": "A"})
    a = e.active
    ex(e, "sequence.create", {"name": "S"})
    e.dispatch("project.new", {"name": "B"})
    n = len(events)
    e.projects[a].execute("sequence.create", {"name": "T"})
    assert all(ev["type"] != "patch" for ev in events[n:])


def test_close_refuses_unsaved_until_discarded(tmp_path):
    e, _ = make()
    e.dispatch("project.new", {"name": "A"})
    ex(e, "sequence.create", {"name": "S"})
    with pytest.raises(CommandError) as err:
        e.dispatch("project.close", {})
    assert err.value.code == "UNSAVED"
    e.dispatch("project.close", {"discard": True})
    lst = e.dispatch("project.list")
    assert len(lst) == 1 and lst[0]["name"] == "Untitled" and not lst[0]["dirty"]


def test_close_saved_project_and_open_dedupes(tmp_path):
    e, _ = make()
    e.dispatch("project.new", {"name": "A"})
    ex(e, "sequence.create", {"name": "S"})
    path = str(tmp_path / "a.lumina")
    e.dispatch("project.save", {"path": path})
    e.dispatch("project.new", {"name": "B"})
    e.dispatch("project.open", {"path": path})  # already open: brought to front, not opened twice
    assert len(e.dispatch("project.list")) == 2 and e.session.doc["name"] == "A"
    e.dispatch("project.close", {})
    assert [p["name"] for p in e.dispatch("project.list")] == ["B"]
    e.dispatch("project.open", {"path": path})
    assert [p["name"] for p in e.dispatch("project.list")] == ["B", "a"]  # a saved project is named by its file


def test_discarding_drops_the_recovery_log(tmp_path):
    e, _ = make()
    e.dispatch("project.new", {"name": "A"})
    path = str(tmp_path / "a.lumina")
    e.dispatch("project.save", {"path": path})
    ex(e, "sequence.create", {"name": "S"})
    assert os.path.exists(path + ".oplog")
    e.dispatch("project.close", {"discard": True})
    assert not os.path.exists(path + ".oplog")
    e.dispatch("project.open", {"path": path})
    assert not e.session.doc["sequences"]


def test_close_all_lists_every_unsaved_project():
    e, _ = make()
    e.dispatch("project.new", {"name": "A"})
    ex(e, "sequence.create", {"name": "S"})
    e.dispatch("project.new", {"name": "B"})
    ex(e, "sequence.create", {"name": "S"})
    e.dispatch("project.new", {"name": "C"})
    with pytest.raises(CommandError) as err:
        e.dispatch("project.close_all", {})
    assert err.value.details["names"] == ["A", "B"]
    e.dispatch("project.close_all", {"discard": True})
    assert [p["name"] for p in e.dispatch("project.list")] == ["Untitled"]


def _fill(mid, age_days=0.0, size=1000):
    d = media_cache.root() / mid
    d.mkdir(parents=True, exist_ok=True)
    f = d / "thumb.jpg"
    f.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(f, (t, t))
    os.utime(d, (t, t))
    return f


def test_cache_info_and_clean_unused():
    e, _ = make()
    e.dispatch("project.new", {"name": "A"})
    mid = ex(e, "media.import", {"paths": ["a.mp4"]})["media_ids"][0]
    thumb = _fill(mid)
    ex(e, "media.set_derived", {"media_id": mid, "thumbnail_path": str(thumb)})
    _fill("gone1")
    _fill("gone2", size=500)
    info = e.dispatch("cache.info")
    assert info["items"] == 3 and info["bytes"] == 2500 and info["used_items"] == 1
    res = e.dispatch("cache.clean", {"mode": "unused"})
    assert sorted(res["removed_media"]) == ["gone1", "gone2"] and res["freed_bytes"] == 1500
    assert thumb.exists() and e.session.doc["media"][mid]["thumbnail_path"] == str(thumb)
    # cleaning everything forgets the open project's generated files too
    e.dispatch("cache.clean", {"mode": "all"})
    assert not thumb.exists() and e.session.doc["media"][mid]["thumbnail_path"] is None


def test_cache_clean_older_and_settings(tmp_path):
    e, _ = make()
    _fill("old", age_days=40)
    _fill("new", age_days=1)
    res = e.dispatch("cache.clean", {"mode": "older", "days": 30})
    assert res["removed_media"] == ["old"]
    with pytest.raises(CommandError):
        e.dispatch("cache.clean", {"mode": "older"})
    out = e.dispatch("cache.settings", {"cache_dir": str(tmp_path / "elsewhere"), "auto_clean_days": 7})
    assert out["root"] == str(tmp_path / "elsewhere") and media_cache.settings()["auto_clean_days"] == 7
    assert e.dispatch("cache.info")["items"] == 0
    # auto clean runs when the engine starts
    _fill("stale", age_days=10)
    Engine(services=FakeServices(), run_jobs=False)
    assert not (media_cache.root() / "stale").exists()
