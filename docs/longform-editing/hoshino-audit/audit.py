"""Read-only reference audit; run with Phrase Studio's Python environment."""
import difflib
import hashlib
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, r'D:\Dev\phrase-studio')
import longform_split as lf

OUT = Path(__file__).resolve().parent
ROOT = Path(r'D:\유튜브 채널\Project\블루 아카이브\아비도스 고등학교\타카나시 호시노')

def sec(t):
    h,m,s = map(float,t.replace(',','.').split(':'))
    return h*3600+m*60+s

def stamp(t):
    ms=round(t*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

def measure(cues):
    chars=[len(lf.compact(c['text'])) for c in cues]
    times=[c['end']-c['start'] for c in cues]
    return dict(count=len(cues),median_chars=statistics.median(chars),median_seconds=statistics.median(times),under1s=sum(t<1 for t in times),over5s=sum(t>5 for t in times),over30chars=sum(n>30 for n in chars))

def main():
    original=[]
    for block in re.split(r'\n\s*\n',(ROOT/'타카나시 호시노.srt').read_text('utf-8-sig').strip()):
        lines=block.splitlines();a,b=lines[1].split(' --> ')
        original.append(dict(start=sec(a),end=sec(b),text=' '.join(lines[2:])))
    with zipfile.ZipFile(ROOT/'타카나시 호시노.vrew') as z:
        vrew=json.loads(z.read('project.json'))
    raw=[w for c in vrew['transcript']['clips'] for w in c['words'] if w['text'].strip()]
    assert all(w['playbackRate']==1 for w in raw)
    source=''.join(lf.compact(w['text']) for w in raw)
    target=''.join(lf.compact(c['text']) for c in original)
    ops=difflib.SequenceMatcher(None,source,target,autojunk=False).get_opcodes()
    # Map corrected characters to the original timed word(s); no proportional
    # timing estimates. Only original word onsets/offsets are used.
    char_words=[]
    for i,w in enumerate(raw):char_words.extend([i]*len(lf.compact(w['text'])))
    target_words=[None]*len(target)
    corrections=[]
    for tag,a,b,c,d in ops:
        if tag=='equal':
            for k in range(d-c):target_words[c+k]=(char_words[a+k],char_words[a+k])
        else:
            corrections.append(dict(before=source[a:b],after=target[c:d],source_span=[a,b],target_span=[c,d]))
            lo=char_words[min(a,len(source)-1)];hi=char_words[max(a,b-1)]
            for k in range(c,d):target_words[k]=(lo,hi)
    words=[];cursor=0
    for cue in original:
        for token in cue['text'].split():
            n=len(lf.compact(token));mapped=target_words[cursor:cursor+n]
            assert all(m is not None for m in mapped)
            lo=min(m[0] for m in mapped);hi=max(m[1] for m in mapped)
            words.append(dict(word=token,start=raw[lo]['originalStartTime'],end=raw[hi]['originalStartTime']+raw[hi]['duration']))
            cursor+=n
    # Resegmentation requires strict onset progression. If a corrected token
    # spans multiple ASR tokens, use their outer bounds; never split ASR time.
    overlaps=[i for i,(a,b) in enumerate(zip(words,words[1:])) if a['end']>b['start']+.001]
    assert not overlaps, f'Overlapping token mappings: {overlaps}'
    duration=original[-1]['end']
    new=lf.split_segments(words,duration)
    assert ''.join(lf.compact(c['text']) for c in new)==target
    def spans(cues):
        at=0;out=[]
        for i,c in enumerate(cues):
            end=at+len(lf.compact(c['text']));out.append((at,end,i));at=end
        return out
    oldsp,newsp=spans(original),spans(new)
    ob={b for a,b,i in oldsp[:-1]};nb={b for a,b,i in newsp[:-1]}
    reviews=[]
    for a,b,i in newsp:
        old=[j for x,y,j in oldsp if x<b and y>a]
        c=new[i]
        reviews.append(dict(index=i+1,**c,old_indices=[j+1 for j in old],old_texts=[original[j]['text'] for j in old],removed_boundaries=sorted(p for p in ob-nb if a<p<b),new_start=a in nb-ob,new_end=b in nb-ob,chars=len(lf.compact(c['text']))))
    summary=dict(original=measure(original),longform=measure(new),words=len(words),corrections=len(corrections),common_boundaries=len(ob&nb),removed_boundaries=len(ob-nb),added_boundaries=len(nb-ob),splitter_sha256=hashlib.sha256(Path(lf.__file__).read_bytes()).hexdigest())
    data=dict(summary=summary,corrections=corrections,words=words,original=original,segments=new,review=reviews)
    (OUT/'comparison.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf8')
    (OUT/'hoshino-longform.srt').write_text('\n\n'.join(f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}' for i,c in enumerate(new,1))+'\n','utf8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for c in reviews:
        if c['new_start'] or c['new_end'] or c['chars']>30 or c['end']-c['start']>5 or c['end']-c['start']<1:
            print(f'{c["index"]:03} {stamp(c["start"])} {c["end"]-c["start"]:.2f}s {c["chars"]}字 {c["text"]} | OLD: '+ ' / '.join(c['old_texts']))

if __name__=='__main__':main()
