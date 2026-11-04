"""Read-only structural benchmark. Run with engine/.venv/Scripts/python.exe."""
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'engine'))
from lumina.io import prproj

OUT = Path(__file__).resolve().parent
BASE = Path(r'D:\유튜브 채널\Project')
SOURCES = {
    'hoshino': ('블루 아카이브/아비도스 고등학교/타카나시 호시노/인계 파일/타카나시 호시노.prproj', '시퀀스 09'),
    'ishigori': ('주술회전/이시고리 류/이시고리 류.prproj', 'SEQ'),
    'denji': ('체인소맨/덴지/덴지.prproj', 'SEQ'),
    'reze': ('체인소맨/레제/Long/reze.prproj', 'SEQ'),
}
T = 254016000000

def main():
    summaries = []
    for key, (relative, name) in SOURCES.items():
        path = BASE / relative
        ex = prproj.extract(str(path))
        matches = [s for s in ex.sequences if s['name'] == name and s['settings']['width'] == 1920 and s['video_tracks']]
        assert len(matches) == 1, (key, len(matches))
        s = matches[0]
        rows, pictures, texts = [], [], []
        for kind in ['video_tracks', 'audio_tracks']:
            for ti, track in enumerate(s[kind], 1):
                for c in track['items']:
                    media = ex.media.get(c.get('media_uid'), {})
                    layers = [l for l in (c.get('graphic') or {}).get('layers', []) if l.get('type') == 'text']
                    if kind == 'video_tracks':
                        texts.extend(layers)
                        if media and not c.get('adjustment'):
                            pictures.append((c, media))
                    rows.append(dict(track=('V' if kind == 'video_tracks' else 'A')+str(ti),
                                     start=round(c['start']/T, 6), end=round(c['end']/T, 6),
                                     name=c['name'], media_kind=media.get('kind'),
                                     source=media.get('path'), source_in=round(c.get('source_in',0)/T,6),
                                     speed=c.get('speed',1), nested_sequence=c.get('sequence_uid'),
                                     text=' | '.join(l.get('text','') for l in layers),
                                     animated=bool(c.get('keyframes')), effects=','.join(f.get('type','') for f in c.get('effects',[]))))
        with (OUT / f'{key}-timeline.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        summaries.append(dict(key=key,project=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),sequence=name,
                              sequence_uid=s['uid'],settings=s['settings'],duration=max(r['end'] for r in rows),
                              media_instances=len(pictures),media_kinds=dict(Counter(m['kind'] for c,m in pictures)),
                              median_media_instance_seconds=statistics.median((c['end']-c['start'])/T for c,m in pictures),
                              under_one_second_instances=sum(c['end']-c['start']<T for c,m in pictures),
                              media_instances_with_motion_keys=sum(bool(c.get('keyframes')) for c,m in pictures),
                              media_instances_with_changed_speed=sum(abs(c.get('speed',1)-1)>.001 for c,m in pictures),
                              text_styles=Counter(f"{l.get('font_family')} | {l.get('font_size')} | tracking={l.get('tracking')} | box={l.get('box')}" for l in texts),
                              import_warnings=ex.warnings))
    (OUT/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),'utf8')
    for s in summaries:print(s['key'],s['sequence'],s['duration'],s['media_instances'],s['media_kinds'])

if __name__ == '__main__': main()
