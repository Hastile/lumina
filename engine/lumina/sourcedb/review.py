"""Source-monitor ingest/review. Drafts live in Lumina's existing fact store.

No project edits; no second adb DB. Analysis works on an isolated temporary
source DB and returns proposals. Explicit publishing and atomic live edits both guard revisions and DB baselines.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import uuid
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

from . import db, ingest
from ..commands.registry import CommandError
from ..timebase import TICKS_PER_SECOND as S

MANAGED = {"work", "character", "description", "shot", "source_type", "origin_url", "episode",
           "action", "mood", "setting", "use_case", "speech", "dialogue", "note"}
TEXT_FIELDS = ("description", "shot", "work", "note")
TAGS = ("action", "mood", "setting", "use_case")
STAGES = ('cuts', 'describe', 'tagger', 'speech')


def stage_has(stage, rows):
    return any((stage == 'describe' and f['kind'] in ('description', 'shot')) or
               (stage == 'tagger' and (f['kind'] == 'wd_tag' or str(f.get('producer','')).startswith('wd-'))) or
               (stage == 'speech' and f['kind'] in ('speech', 'dialogue'))
               for f in rows if f.get('status') != 'rejected')


def analysis_state(con, mid, d, media=None):
    """Older DBs need no new review draft or new-version job to be reviewable."""
    jobs = {r['stage']: dict(r) for r in con.execute('SELECT * FROM job WHERE media_id=?', (mid,))} if mid else {}
    facts = [f for f in evidence(con, mid) if f['status'] != 'rejected'] if mid else []
    cuts = d.get('cuts', [])
    stages = {}
    for stage in STAGES:
        saved = d.get('analysis', {}).get(stage) or jobs.get(stage)
        if stage == 'speech' and (d.get('kind') == 'image' or
                (media is not None and 'audio' in media and not media['audio'])):
            stages[stage] = {'status': 'not_applicable', 'basis': '음성 트랙 없음'}
        elif stage == 'cuts' and cuts:
            stages[stage] = {'status': 'done', 'basis': '기존 컷 사용'}
        elif stage == 'cuts':
            stages[stage] = {'status': 'missing', 'basis': '컷 데이터 없음'}
        elif saved and saved['status'] == 'done':
            stages[stage] = {'status': 'done', 'basis': '기존 분석 이력', 'version': saved.get('version')}
        else:
            def has(rows):
                return stage_has(stage, rows)
            covered = sum(has([f for f in facts if overlap(f,c['start'],c['end'])] + c.get('evidence', [])) for c in cuts)
            present = has(facts) or bool(covered) or (stage == 'speech' and any(c.get('dialogue') for c in cuts))
            complete = present and (stage == 'speech' or (bool(cuts) and covered == len(cuts)))
            stages[stage] = {'status': 'done' if complete else 'partial' if present else 'missing',
                             'basis': '기존 분석 데이터' if present else '분석 이력 없음'}
            if saved and saved['status'] == 'failed':
                stages[stage].update(status='failed', error=saved.get('error'))
    missing = [s for s in STAGES if stages[s]['status'] not in ('done','not_applicable')]
    for stage, entry in jobs.items():
        if stage not in stages:
            stages[stage] = {'status':entry['status'], 'basis':'기존 DB 분석 이력', 'version':entry.get('version')}
    return {'state': 'unanalysed' if not cuts and not facts else 'partial' if missing else 'analysed',
            'stages': stages, 'missing': missing, 'reviewable': bool(cuts)}


@contextmanager
def connection(path):
    con = db.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


def fail(message, code="SOURCE_REVIEW"):
    raise CommandError(code, message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def evidence(con, mid):
    return [dict(r) for r in con.execute(
        "SELECT f.*, e.name AS entity_name FROM fact f LEFT JOIN entity e ON f.ref_id=e.entity_id "
        "WHERE f.media_id=? AND f.kind!='source_review' ORDER BY f.fact_id", (mid,))]


def baseline(con, mid):
    return digest({"cuts": [dict(r) for r in con.execute("SELECT * FROM cut WHERE media_id=? ORDER BY idx", (mid,))],
                   "facts": evidence(con, mid)})


def overlap(f, a, b):
    return f.get("t0_ticks") is None or (a == b == 0) or (f["t0_ticks"] < b and f["t1_ticks"] > a)


def blank_cut(a, b, key=None, origin="detector"):
    return dict(id=uuid.uuid4().hex, start=a, end=b, keyframe=key if key is not None else (a+b)//2,
                detected_start=a, detected_end=b, description="", shot="", work="", characters=[],
                tags={k: [] for k in TAGS}, dialogue=[], note="", excluded=False, reviewed=False,
                origin=origin, evidence=[])


def populate(cut, facts):
    rows = [f for f in facts if f["status"] != "rejected" and overlap(f, cut["start"], cut["end"])]
    rows.sort(key=lambda f: (f["status"] == "accepted", f["producer"] == "human", f["fact_id"]))
    for f in rows:
        k, value = f["kind"], f.get("entity_name") or f["value"]
        if k in TEXT_FIELDS and value:
            cut[k] = value
        elif k == 'hidden' and f.get('num') == 1:
            cut['excluded'] = True
        elif k == "character" and value and value not in cut["characters"]:
            cut["characters"].append(value)
        elif k in TAGS and value and value not in cut["tags"][k]:
            cut["tags"][k].append(value)
        elif k == "dialogue":
            try:
                line = json.loads(f["value"])
                line.update(start=max(cut["start"], f["t0_ticks"] or 0),
                            end=min(cut["end"], f["t1_ticks"] or cut["end"]))
                cut["dialogue"].append(line)
            except (ValueError, TypeError):
                pass
    # Legacy ASR has no speaker: never infer a speaker from visible characters.
    if not cut["dialogue"]:
        for f in rows:
            if f["kind"] == "speech" and f["value"]:
                cut["dialogue"].append(dict(start=max(cut["start"], f["t0_ticks"] or 0),
                    end=min(cut["end"], f["t1_ticks"] or cut["end"]), speaker="", text=f["value"], origin=f["producer"]))
    cut["evidence"] = rows
    return cut


def make_draft(con, mid):
    m = dict(con.execute("SELECT * FROM media WHERE media_id=?", (mid,)).fetchone())
    facts = evidence(con, mid)
    cuts = [populate(blank_cut(r["start_ticks"], r["end_ticks"], r["keyframe_ticks"], "database"), facts)
            for r in con.execute("SELECT * FROM cut WHERE media_id=? ORDER BY idx", (mid,))]
    for c in cuts:
        c['id'] = f"db-{mid}-{c['start']}-{c['end']}"
    identity = {k: "" for k in ("work", "source_type", "origin_url", "episode", "note")}
    for f in sorted(facts, key=lambda f: (f["status"] == "accepted", f["producer"] == "human")):
        if f["status"] != "rejected" and f["t0_ticks"] is None and f["kind"] in identity:
            identity[f["kind"]] = f.get("entity_name") or f["value"] or ""
    return dict(revision=1, media_id=mid, sha256=m["sha256"], kind=m["kind"],
                duration=m["duration_ticks"] or 0, fps_num=m["fps_num"] or 30, fps_den=m["fps_den"] or 1,
                identity=identity, cuts=cuts, baseline=baseline(con, mid), history=[], published_revision=None)


def read_draft(con, mid):
    row = con.execute("SELECT value FROM fact WHERE media_id=? AND kind='source_review' "
                      "AND producer='source-review@1' ORDER BY fact_id DESC LIMIT 1", (mid,)).fetchone()
    return json.loads(row[0]) if row else make_draft(con, mid)


def write_draft(con, d):
    db.put_facts(con, d["media_id"], "source-review@1", [{"kind": "source_review", "value": json.dumps(d, ensure_ascii=False)}], rebuild=False)


def checkpoint(d):
    d["history"] = [*d.get("history", [])[-19:], copy.deepcopy({"identity": d["identity"], "cuts": d["cuts"]})]
    d["revision"] += 1


def validate(d):
    if not d["cuts"]:
        fail("컷 검출을 먼저 실행하세요.")
    end = 0
    for c in d["cuts"]:
        a, b = c["start"], c["end"]
        if not isinstance(a, int) or not isinstance(b, int) or a != end or b < a or (d["kind"] != "image" and a == b):
            fail("컷은 공백·겹침 없이 소스 전체를 덮어야 합니다.")
        if not a <= c["keyframe"] <= max(a, b-1):
            fail("대표 프레임이 컷 밖에 있습니다.")
        for line in c["dialogue"]:
            if not (a <= line["start"] < line["end"] <= b) or not line["text"].strip():
                fail("대사 시간은 컷 안에 있어야 하며 내용이 필요합니다.")
        end = b
    if end != d["duration"]:
        fail("마지막 컷이 소스 끝과 맞지 않습니다.")


def clip_dialogue(lines, a, b):
    return [{**line, "start": max(a, line["start"]), "end": min(b, line["end"])}
            for line in lines if line["start"] < b and line["end"] > a]


def joined_dialogue(lines):
    out = []
    for line in sorted(lines, key=lambda x: (x['start'], x['end'])):
        prev = next((x for x in reversed(out) if x['text'] == line['text'] and x.get('speaker') == line.get('speaker')
                     and x.get('origin') == line.get('origin') and x['end'] >= line['start']), None)
        if prev:
            prev['end'] = max(prev['end'], line['end'])
        else:
            out.append(dict(line))
    return out


def edit(d, p):
    action = p["action"]
    if action == "undo":
        if not d["history"]:
            fail("되돌릴 검수 수정이 없습니다.")
        prev = d["history"].pop()
        d.update(prev)
        d["revision"] += 1
        return
    checkpoint(d)
    if action == "identity":
        for k, v in p.get("fields", {}).items():
            if k not in d["identity"] or not isinstance(v, str):
                fail("지원하지 않는 소스 정보입니다.")
            d["identity"][k] = v.strip()
        for c in d["cuts"]:
            c["reviewed"] = False
        return
    i = next((i for i, c in enumerate(d["cuts"]) if c["id"] == p.get("cut_id")), None)
    if i is None:
        fail("컷이 변경되었습니다. 새로고침하세요.", "STALE_REVIEW")
    c = d["cuts"][i]
    if action == "review":
        c["reviewed"] = bool(p.get("reviewed", True))
        c['reviewer'] = p.get('actor', 'human')
        return
    c["reviewed"] = False
    if action == "update":
        for k, v in p.get("fields", {}).items():
            if k in TEXT_FIELDS and isinstance(v, str):
                c[k] = v.strip()
            elif k == "characters" and isinstance(v, list) and all(isinstance(s, str) for s in v):
                c[k] = list(dict.fromkeys(s.strip() for s in v if s.strip()))
            elif k == "tags" and isinstance(v, dict) and set(v) <= set(TAGS):
                if any(not isinstance(a, list) or any(not isinstance(s, str) for s in a) for a in v.values()):
                    fail("태그는 문자열 목록이어야 합니다.")
                c[k].update(v)
            elif k == "excluded" and isinstance(v, bool):
                c[k] = v
            elif k == "dialogue" and isinstance(v, list):
                c[k] = [dict(start=int(x["start"]), end=int(x["end"]), speaker=str(x.get("speaker", "")).strip(),
                             text=str(x["text"]).strip(), origin="human") for x in v]
            else:
                fail("지원하지 않는 컷 정보입니다.")
        c["origin"] = p.get('actor', 'human')
    elif action in ("boundary", "split", "keyframe"):
        t = int(p["time"])
        frame = Fraction(S * d["fps_den"], d["fps_num"])
        t = round(round(Fraction(t) / frame) * frame)
        if action == "boundary":
            # A shared edge always moves both sides; speech retains its media times.
            if i + 1 >= len(d["cuts"]):
                fail("소스 마지막 경계는 움직일 수 없습니다.")
            right = d["cuts"][i+1]
            if not c["start"] < t < right["end"]:
                fail("경계는 양쪽 컷을 최소 한 프레임 남겨야 합니다.")
            lines = joined_dialogue(c["dialogue"] + right["dialogue"])
            c["end"], right["start"] = t, t
            for x in (c, right):
                x["dialogue"] = clip_dialogue(lines, x["start"], x["end"])
                x["keyframe"] = min(max(x["keyframe"], x["start"]), x["end"]-1)
                x["reviewed"] = False
        elif action == "split":
            if not c["start"] < t < c["end"]:
                fail("컷 내부에서 분할하세요.")
            right = copy.deepcopy(c)
            right.update(id=uuid.uuid4().hex, start=t, keyframe=(t+c["end"])//2)
            right["dialogue"] = clip_dialogue(c["dialogue"], t, c["end"])
            c["end"] = t
            c["keyframe"] = min(c["keyframe"], t-1)
            c["dialogue"] = clip_dialogue(c["dialogue"], c["start"], t)
            d["cuts"].insert(i+1, right)
        else:
            if not c["start"] <= t <= max(c["start"], c["end"]-1):
                fail("대표 프레임을 컷 내부에서 선택하세요.")
            c["keyframe"] = t
    elif action == "merge":
        if i+1 >= len(d["cuts"]):
            fail("다음 컷이 없습니다.")
        right = d["cuts"].pop(i+1)
        c["end"] = right["end"]
        c["description"] = " / ".join(dict.fromkeys(x for x in (c["description"], right["description"]) if x))
        c["characters"] = list(dict.fromkeys(c["characters"]+right["characters"]))
        c["dialogue"] = joined_dialogue(c["dialogue"] + right["dialogue"])
        c["evidence"] += right["evidence"]
        for k in TAGS:
            c["tags"][k] = list(dict.fromkeys(c["tags"][k]+right["tags"][k]))
        if c["work"] != right["work"]:
            c["note"] += "\n병합 전 다음 컷 IP: " + right["work"]
    else:
        fail("알 수 없는 검수 작업입니다.")
    validate(d)


class SourceReviewService:
    def __init__(self, engine, path=None):
        self.engine = engine
        self.path = Path(path or os.environ.get('LUMINA_SOURCE_DB') or db.DEFAULT_DB_PATH)
        self.lock = threading.RLock()
        self.jobs = {}

    def media(self, p):
        m = self.engine.session.doc["media"].get(p.get("media_id"))
        if not m or m["kind"] not in ("video", "image"):
            fail("프로젝트에서 영상 또는 이미지 소스를 여세요.")
        path = Path(m["path"]).resolve()
        if not path.is_file():
            fail("소스 파일을 찾을 수 없습니다.")
        return m, path

    def _find(self, con, path):
        row = con.execute("SELECT * FROM media_file WHERE path=?", (str(path),)).fetchone()
        if row:
            st = path.stat()
            if row["size"] != st.st_size or row["mtime_ms"] != int(st.st_mtime*1000):
                fail("파일이 변경되었습니다. 소스 등록·분석을 다시 실행하세요.", "SOURCE_CHANGED")
            return row["media_id"]
        return None

    def result(self, d, path, con):
        r = {k: v for k, v in d.items() if k != "history"}
        r["can_undo"] = bool(d.get("history"))
        r["path"] = str(path)
        r["db_path"] = str(self.path)
        r["job"] = copy.deepcopy(self.jobs.get(str(path)))
        r['analysis_status'] = analysis_state(con, d['media_id'], d)
        return r

    def get(self, p):
        media, path = self.media(p)
        with self.lock, connection(self.path) as con:
            mid = self._find(con, path)
            if mid is None:
                return {"registered": False, "path": str(path), "job": copy.deepcopy(self.jobs.get(str(path))),
                        'analysis_status': analysis_state(con, None, {'kind':media['kind']}, media)}
            d = read_draft(con, mid)
            return {"registered": True, **self.result(d, path, con), 'analysis_status':analysis_state(con, mid, d, media)}

    def _check(self, d, p):
        if p.get("revision") != d["revision"]:
            fail("다른 창 또는 AI가 검수 내용을 변경했습니다. 새로고침 후 다시 시도하세요.", "STALE_REVIEW")

    def update(self, p):
        _, path = self.media(p)
        with self.lock, connection(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            mid = self._find(con, path)
            if mid is None:
                fail("소스를 먼저 등록하세요.")
            d = read_draft(con, mid)
            self._check(d, p)
            edit(d, p)
            write_draft(con, d)
            return self.result(d, path, con)

    def preview(self, p):
        _, path = self.media(p)
        with self.lock, connection(self.path) as con:
            mid = self._find(con, path)
            if mid is None:
                fail("소스를 먼저 등록하세요.")
            d = read_draft(con, mid)
            self._check(d, p)
            validate(d)
            if baseline(con, mid) != d["baseline"]:
                fail("검색 DB가 변경되었습니다. 새 DB 상태로 검수를 다시 시작해야 합니다.", "STALE_REVIEW")
            pending = sum(not c["reviewed"] for c in d["cuts"])
            return {"token": digest(d), "revision": d["revision"], "pending": pending,
                    "kept": sum(not c["excluded"] for c in d["cuts"]), "excluded": sum(c["excluded"] for c in d["cuts"]),
                    "identity": d["identity"], "cuts": d["cuts"], "changes_project": False,
                    "replaces_facts": sum(f["kind"] in MANAGED and f["status"] != "rejected" for f in evidence(con, mid))}

    def publish(self, p):
        _, path = self.media(p)
        with self.lock, connection(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            mid = self._find(con, path)
            if mid is None:
                fail("소스를 먼저 등록하세요.")
            d = read_draft(con, mid)
            self._check(d, p)
            validate(d)
            if p.get("token") != digest(d) or baseline(con, mid) != d["baseline"]:
                fail("검수 내용이 바뀌었습니다. 반영 미리보기를 다시 확인하세요.", "STALE_REVIEW")
            if any(not c["reviewed"] for c in d["cuts"]):
                fail("제외 컷을 포함해 모든 컷의 검수를 완료하세요.")
            return self._publish_data(con, d, path, p)

    def _publish_data(self, con, d, path, p, touched=None):
        mid = d['media_id']
        previous = [f for f in evidence(con, mid) if f['status'] != 'rejected']
        # Keep superseded evidence as rejected, including the human correction trail.
        kinds = sorted(MANAGED | {"hidden"})
        con.execute(f"UPDATE fact SET status='rejected' WHERE media_id=? AND kind IN ({','.join('?' for _ in kinds)}) "
                    "AND status!='rejected'", (mid, *kinds))
        db.set_cuts(con, mid, [dict(start_ticks=c["start"], end_ticks=c["end"], keyframe_ticks=c["keyframe"]) for c in d["cuts"]])
        def fact(kind, value, a=None, b=None, basis="소스 검수에서 확인", cut_id=None):
            if value in (None, "", []):
                return
            from .catalog import resolve_entity
            ref = resolve_entity(con, kind, [value]) if kind in ('work','character') else None
            explicit = touched is None or kind in touched.get(cut_id, set())
            prior = next((f for f in reversed(previous) if f['kind']==kind and (f.get('entity_name') or f.get('value'))==value
                          and (a is None and f['t0_ticks'] is None or a is not None and overlap(f,a,b))), None)
            status = 'accepted' if explicit else (prior['status'] if prior else 'proposed')
            producer = p.get('actor','human') if explicit else (prior['producer'] if prior else 'source-review-live@1')
            basis = basis if explicit else (prior.get('basis') if prior else '기존 분석 유지 · 자동 승인 아님')
            # Rebuild once at the end rather than for every field.
            con.execute("INSERT INTO fact (media_id,t0_ticks,t1_ticks,kind,value,ref_id,producer,status,basis,created_ms) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (mid, a, b, kind, None if ref else value, ref, producer, status, basis, db.now_ms()))
        for k, v in d["identity"].items():
            if k != 'work':
                fact(k, v)
        for c in d["cuts"]:
            a, b = (None, None) if d["kind"] == "image" else (c["start"], c["end"])
            if c["excluded"]:
                con.execute("INSERT INTO fact (media_id,t0_ticks,t1_ticks,kind,num,producer,status,basis,created_ms) "
                            "VALUES (?,?,?,'hidden',1,'human','accepted','검수에서 제외',?)", (mid,a,b,db.now_ms()))
                continue
            for k in TEXT_FIELDS:
                fact(k, (c[k] or d['identity']['work']) if k == 'work' else c[k], a, b, cut_id=c['id'])
            for name in c["characters"]:
                fact("character", name, a, b, cut_id=c["id"])
            for k in TAGS:
                for value in c["tags"][k]:
                    fact(k, value, a, b, cut_id=c["id"])
            for line in c["dialogue"]:
                fact("speech", line["text"], line["start"], line["end"], cut_id=c["id"])
                fact("dialogue", json.dumps(line, ensure_ascii=False), line["start"], line["end"], cut_id=c["id"])
        db.set_status(con, mid, "live")
        db.rebuild_docs(con, mid)
        d["revision"] += 1
        d["published_revision"] = d["revision"]
        d["baseline"] = baseline(con, mid)
        write_draft(con, d)
        return self.result(d, path, con)

    def choices(self, p):
        from .catalog import catalog, key
        from .describe import SOURCE_TYPES, SHOTS
        cat = catalog()
        query = key(str(p.get('query', '')))
        work = cat.resolve('work', [str(p.get('work', ''))])
        works = [m for (kind, _), m in cat._rows.items() if kind == 'work'
                 and (not query or any(query in key(n) for n in m.names))]
        works.sort(key=lambda m: (m.ko or m.names[0]).casefold())
        works = works[:100]
        if work and work not in works: works.insert(0,work)
        chars = [m for (kind, _), m in cat._rows.items() if kind == 'character' and work and m.ip_id == work.catalog_id]
        chars.sort(key=lambda m: (m.ko or m.names[0]).casefold())
        def option(m):
            return {'value': m.ko or m.names[0], 'ref': m.ref}
        return {'available': cat.ok, 'works': [option(m) for m in works[:100]],
                'characters': [option(m) for m in chars], 'source_types': SOURCE_TYPES, 'shots': SHOTS}

    def _validate_choices(self, d, change):
        from .catalog import catalog
        from .describe import SOURCE_TYPES, SHOTS
        fields = change.get('fields', {})
        c = next((c for c in d['cuts'] if c['id']==change.get('cut_id')), None)
        old = d['identity'] if change['action']=='identity' else c
        if old is None:
            return
        cat = catalog()
        work_name = fields.get('work', old.get('work') or d['identity']['work'])
        work = cat.resolve('work', [work_name]) if work_name else None
        if 'work' in fields and fields['work'] and fields['work'] != old.get('work') and not work:
            fail('IP DB에서 작품을 선택하세요.')
        for field, allowed in [('source_type', SOURCE_TYPES), ('shot', SHOTS)]:
            if field in fields and fields[field] and fields[field] != old.get(field) and fields[field] not in allowed:
                fail('목록에서 '+field+' 값을 선택하세요.')
        if c:
            known = set(c['characters']) | {x.get('speaker','') for x in c['dialogue']}
            values = fields.get('characters', []) + [x.get('speaker','') for x in fields.get('dialogue', [])]
            for name in values:
                if not name or (name in known and work_name == (c['work'] or d['identity']['work'])):
                    continue
                match = cat.resolve('character', [name], work.catalog_id) if work else None
                if not match or match.ip_id != work.catalog_id:
                    fail('선택한 IP의 인물 목록에서 등장인물·화자를 선택하세요.')

    def save(self, p):
        """Atomic live edit + search DB sync. Unedited AI facts stay proposed."""
        _, path = self.media(p)
        with self.lock, connection(self.path) as con:
            con.execute('BEGIN IMMEDIATE')
            mid = self._find(con, path)
            if mid is None:
                fail('소스를 먼저 등록하세요.')
            d = read_draft(con, mid)
            self._check(d, p)
            if baseline(con, mid) != d['baseline']:
                fail('검색 DB가 변경되었습니다. 다시 불러온 후 수정하세요.', 'STALE_REVIEW')
            changes = p.get('changes')
            if not isinstance(changes, list) or not changes or len(changes)>50:
                fail('저장할 변경이 필요합니다.')
            touched = {}
            for change in changes:
                if change.get('action') not in ('update','identity','boundary','split','merge','keyframe','undo'):
                    fail('지원하지 않는 자동 저장 작업입니다.')
                self._validate_choices(d, change)
                fields = change.get('fields', {})
                kinds = set(fields) - {'characters','tags','dialogue','excluded'}
                if 'characters' in fields: kinds.add('character')
                if 'tags' in fields: kinds.update(fields['tags'])
                if 'dialogue' in fields: kinds.update(('speech','dialogue'))
                scope = None if change['action']=='identity' else change.get('cut_id')
                touched.setdefault(scope,set()).update(kinds)
                if change['action']=='identity' and 'work' in fields:
                    for c in d['cuts']:
                        if not c['work']: touched.setdefault(c['id'],set()).add('work')
                edit(d, {**change, 'actor':p.get('actor','human')})
            validate(d)
            return self._publish_data(con, d, path, p, touched)

    def start(self, p):
        m, path = self.media(p)
        stage = p.get("stage", "cuts")
        if stage not in (*STAGES, 'ingest', 'missing'):
            fail("지원하지 않는 분석 단계입니다.")
        with self.lock:
            if self.jobs.get(str(path), {}).get("status") == "running":
                fail("이 소스의 분석이 이미 실행 중입니다.")
            with connection(self.path) as con:
                mid = self._find(con, path)
                draft = read_draft(con, mid) if mid else {'kind':m['kind']}
                if mid:
                    self._check(draft, p)
                status = analysis_state(con, mid, draft, m)
                if stage in ('ingest','missing'):
                    plan = status['missing']
                else:
                    plan = [stage] if p.get('force') or stage in status['missing'] else []
                    if stage != 'cuts' and not draft.get('cuts'):
                        fail('소스 ingest를 먼저 실행하세요.')
                if not plan:
                    return {'status':'done', 'stage':stage, 'message':'기존 분석을 사용합니다. 추가 분석이 필요하지 않습니다.', 'error':None}
            job = {"id": uuid.uuid4().hex, "stage": stage, "status": "running", "message": "분석 중", "error": None}
            self.jobs[str(path)] = job
            threading.Thread(target=self._pipeline, args=(copy.deepcopy(m), path, dict(p), job, plan), daemon=True).start()
            return dict(job)

    def _pipeline(self, m, path, p, job, plan):
        try:
            self._run_pipeline(m, path, p, job, plan)
        except Exception as e:
            job.update(status='failed', error=str(e), message='분석 중단 — 기존 결과는 유지됨')

    def _run_pipeline(self, m, path, p, job, plan):
        errors = []
        revision = p.get('revision')
        for stage in plan:
            job.update(stage=stage, message=f'{stage} 분석 중 — 기존 분석은 유지')
            step = {'stage':stage}
            self._worker(m, path, {**p, 'revision':revision}, step)
            if step['status'] == 'failed':
                errors.append(f"{stage}: {step.get('error')}")
                # Do not continue against a concurrently edited draft or absent segmentation.
                if stage == 'cuts' or step.get('code') in ('STALE_REVIEW','SOURCE_CHANGED'):
                    break
            with self.lock, connection(self.path) as con:
                mid = self._find(con, path)
                draft = read_draft(con, mid)
                revision = draft['revision']
        job.update(status='failed' if errors else 'done', error='; '.join(errors) or None,
                   message='일부 분석 실패 — 성공한 결과는 유지됨' if errors else '분석 완료 — 기존 결과와 함께 검수 가능')

    def _worker(self, m, path, p, job):
        try:
            before = path.stat()
            with self.lock, connection(self.path) as con:
                mid = db.media_id_for_path(con, path)
                original = read_draft(con, mid) if mid else None
                if original:
                    self._check(original, p)
            if job["stage"] == "cuts":
                # Use the shared detector, but retain even brief real cuts for review.
                info = ingest.probe(path, m["kind"])
                sha = db.sha256_file(path)
                ranges = [(0, 0)]
                if m["kind"] == "video":
                    start, fps, cfr = ingest.stream_timing(path)
                    scores = ingest.scene_scores(path, start)
                    edges = [t for t, s in scores if s > ingest.SCENE_THRESHOLD and t > 0] + ingest.spike_times(scores)
                    ticks = sorted({0, info["duration_ticks"], *[n for n in (ingest.to_ticks(t, fps, cfr) for t in edges)
                                   if 0 < n < info["duration_ticks"]]})
                    ranges = list(zip(ticks, ticks[1:]))
                with self.lock, connection(self.path) as con:
                    con.execute("BEGIN IMMEDIATE")
                    if path.stat().st_size != before.st_size or path.stat().st_mtime_ns != before.st_mtime_ns:
                        fail("분석 중 소스 파일이 바뀌었습니다.")
                    mid, _ = db.register_file(con, path, m["kind"], probe=info, sha256=sha)
                    d = read_draft(con, mid)
                    if original and original["media_id"] == mid:
                        self._check(d, p)
                    checkpoint(d)
                    d.update(duration=info.get("duration_ticks", 0), fps_num=info.get("fps_num", 30), fps_den=info.get("fps_den", 1))
                    facts = evidence(con, mid)
                    d["cuts"] = [populate(blank_cut(a,b), facts) for a,b in ranges]
                    d['baseline'] = baseline(con, mid)
                    validate(d)
                    write_draft(con, d)
            else:
                self._analyze(path, original, job["stage"], force=p.get('force', False))
            job.update(status="done", message="분석 완료 — 제안 검수 필요")
        except Exception as e:
            job.update(status="failed", error=str(e), code=getattr(e,'code',None), message="분석 실패")

    def _analyze(self, path, original, stage, force=False):
        """Reuse analyzers without writing their output into the live source DB."""
        from . import describe
        root = Path(os.environ.get('LUMINA_ANALYZER_ROOT', Path(__file__).resolve().parents[2] / 'analyzers'))
        if not (root / 'run.py').is_file() and os.name == 'nt':
            root = Path(r'D:\Dev\lumina\engine\analyzers')
        selected_cuts = original['cuts']
        if not force and stage in ('describe','tagger'):
            selected_cuts = [c for c in selected_cuts if not stage_has(stage,c.get('evidence',[]))]
        if not selected_cuts:
            return
        with tempfile.TemporaryDirectory(prefix="lumina-review-") as folder:
            scratch = Path(folder) / "source.db"
            with connection(scratch) as con:
                mid, _ = db.register_file(con, path, original["kind"], sha256=original["sha256"], probe={
                    "duration_ticks": original["duration"], "fps_num": original["fps_num"], "fps_den": original["fps_den"]})
                db.set_cuts(con, mid, [dict(start_ticks=c["start"], end_ticks=c["end"], keyframe_ticks=c["keyframe"]) for c in selected_cuts])
                db.job_done(con, mid, "cuts", ingest.CUTS_V)
                db.set_status(con, mid, "live")
                con.commit()
                for c in selected_cuts:
                    thumb = ingest.thumb_path(original["sha256"], c["keyframe"])
                    if not thumb.exists():
                        if original["kind"] == "image":
                            ingest.image_thumb(path, thumb)
                        else:
                            ingest.extract_frame(path, Fraction(c["keyframe"], S), thumb)
                if stage == "describe":
                    describe.run(con, [mid], redo=True)
                else:
                    runner = root / "run.py"
                    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    if not python.exists():
                        fail("로컬 분석 환경이 없습니다: engine/analyzers/.venv")
                    proc = subprocess.run([str(python), str(runner), str(scratch), stage], capture_output=True,
                                          text=True, encoding="utf-8", errors="replace", timeout=3600,
                                          creationflags=0x08000000 if os.name == "nt" else 0)
                    if proc.returncode:
                        fail(proc.stderr[-1800:] or "로컬 분석 실행 실패")
                errors = con.execute("SELECT error FROM job WHERE status='failed'").fetchall()
                if errors:
                    fail("; ".join(r[0] for r in errors))
                facts = evidence(con, mid)
                if not facts and not con.execute("SELECT 1 FROM job WHERE stage=? AND status='done'", (stage,)).fetchone():
                    fail('분석에서 제안이나 완료 이력이 반환되지 않았습니다.')
            with self.lock, connection(self.path) as con:
                con.execute("BEGIN IMMEDIATE")
                if self._find(con, path) != original['media_id']:
                    fail('분석 중 소스가 변경되었습니다.')
                d = read_draft(con, original["media_id"])
                self._check(d, {"revision": original["revision"]})
                checkpoint(d)
                d.setdefault('analysis', {})[stage] = {'status':'done', 'completed_ms':db.now_ms()}
                for c in d["cuts"]:
                    proposal = populate(blank_cut(c["start"], c["end"]), facts)
                    # User edits remain authoritative; analysis is also available as raw evidence.
                    if not c["reviewed"]:
                        for k in ("description", "shot", "work", "characters", "tags", "dialogue"):
                            if k == 'tags':
                                for tag, values in proposal[k].items():
                                    if c['origin'] not in ('human','agent') or not c[k].get(tag):
                                        c[k][tag] = list(dict.fromkeys(c[k].get(tag, []) + values))
                            elif k == 'characters':
                                if c['origin'] not in ('human','agent') or not c[k]:
                                    c[k] = list(dict.fromkeys(c[k] + proposal[k]))
                            elif proposal[k] and not c[k]:
                                c[k] = proposal[k]
                        if c['origin'] not in ('human','agent'):
                            c["origin"] = stage
                    c["evidence"] += proposal["evidence"]
                write_draft(con, d)

    def import_srt(self, p):
        from ..adb.srt_parser import parse_srt_file
        # Parser API is shared with caption import; subtitles stay in source time.
        _, path = self.media(p)
        parsed = parse_srt_file(p['path'])
        entries = [dict(start=round(e['startMilliseconds'] * S / 1000),
                        end=round(e['endMilliseconds'] * S / 1000), text=e['text']) for e in parsed.get('cues', [])]
        if not entries:
            fail('읽을 수 있는 SRT 대사가 없습니다.')
        with self.lock, connection(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            mid = self._find(con, path)
            if mid is None:
                fail("컷 검출을 먼저 실행하세요.")
            d = read_draft(con, mid)
            self._check(d, p)
            checkpoint(d)
            for c in d["cuts"]:
                c["dialogue"] = clip_dialogue([dict(start=e["start"], end=e["end"], text=e["text"], speaker="", origin="srt") for e in entries], c["start"], c["end"])
                c["reviewed"] = False
            validate(d)
            write_draft(con, d)
            return self.result(d, path, con)
