"""Source coverage page for a series project folder.

Scans recorded episode clips named "E<ep> <MMSS> <memo>.mp4" in a project folder
and its character subfolders, measures each clip with ffprobe, and writes a
self-contained HTML page (no network) showing which parts of each episode have
been recorded.

    python tools/source_coverage.py "D:/유튜브 채널/Project/사이버펑크/엣지러너 2" --episodes 10 --episode-min 26

The page lands in the project folder as "소스 커버리지.html" (or --out).
Files whose names do not start with "E<ep> <MMSS>" (trailers, teasers) are skipped.
Clips directly in the project folder count as "미분류"; a subfolder's name is its character.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CLIP_RE = re.compile(r"^E(\d+) (\d{2})(\d{2})")
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm"}
UNSORTED = "미분류"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def scan(root: Path) -> list[dict]:
    clips = []
    for path in sorted(root.glob("*")) + sorted(root.glob("*/*")):
        if path.suffix.lower() not in VIDEO_EXT or not path.is_file():
            continue
        m = CLIP_RE.match(path.stem)
        if not m:
            continue
        start = int(m[2]) * 60 + int(m[3])
        duration = round(probe_duration(path), 1)
        clips.append({
            "file": path.stem,
            "character": UNSORTED if path.parent == root else path.parent.name,
            "episode": int(m[1]),
            "start_s": start,
            "duration_s": duration,
            "end_s": round(start + duration, 1),
        })
    return clips


def render(title: str, clips: list[dict], episodes: int, episode_s: int) -> str:
    characters = sorted({c["character"] for c in clips} - {UNSORTED})
    if any(c["character"] == UNSORTED for c in clips):
        characters.append(UNSORTED)
    data = {
        "title": title,
        "episodes": episodes,
        "episode_s": episode_s,
        "characters": characters,
        "clips": clips,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__TITLE__", title).replace("__DATA__", payload)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="project folder (its subfolders are characters)")
    ap.add_argument("--episodes", type=int, required=True, help="number of episodes in the series")
    ap.add_argument("--episode-min", type=float, default=24, help="nominal episode length in minutes")
    ap.add_argument("--title", help="page title (default: '<folder name> 소스 커버리지')")
    ap.add_argument("--out", type=Path, help="output path (default: <folder>/소스 커버리지.html)")
    args = ap.parse_args()

    root = args.folder.resolve()
    clips = scan(root)
    title = args.title or f"{root.name} 소스 커버리지"
    out = args.out or root / "소스 커버리지.html"
    out.write_text(render(title, clips, args.episodes, round(args.episode_min * 60)), encoding="utf-8")
    print(f"{len(clips)} clips -> {out}")
    return 0


PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #faf9f7; --panel: #ffffff; --fg: #1f1e1d; --muted: #6b6862; --line: #e4e1db;
    --grid: #ece9e3; --track: #f3f1ed;
    --c0: #2f6fb3; --c1: #d0662f; --c2: #2f9a6a; --c3: #a64fb0; --c4: #b8962a; --c5: #3aa0b8;
    --unsorted: #9b978f;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1c1b1a; --panel: #252422; --fg: #ecebe8; --muted: #a29f98; --line: #3a3835;
      --grid: #33312e; --track: #2d2b29;
      --c0: #6aa5e6; --c1: #ef9461; --c2: #5cc595; --c3: #cf86d8; --c4: #e0c25a; --c5: #6fcbe0;
      --unsorted: #77736c;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, "Pretendard", "Malgun Gothic", sans-serif; }
  main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; display: flex; flex-direction: column; gap: 20px; }
  h1 { font-size: 26px; font-weight: 600; margin: 0 0 4px; }
  h2 { font-size: 16px; font-weight: 600; margin: 0; }
  .take, .note, .updated { color: var(--muted); margin: 0; }
  .note { font-size: 12px; margin: 4px 0 12px; }
  .updated { font-size: 12px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; min-width: 0; }
  .label, .sub { font-size: 12px; color: var(--muted); }
  .num { font-size: 28px; font-variant-numeric: tabular-nums; margin-top: 2px; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 12px; margin-bottom: 12px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
  .legend i { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
  .chart { position: relative; }
  .chart svg { display: block; width: 100%; height: auto; }
  .chart text { font-size: 11px; fill: var(--muted); font-variant-numeric: tabular-nums; }
  .chart text.ep { fill: var(--fg); }
  .tip { position: absolute; pointer-events: none; background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
         box-shadow: 0 4px 14px rgba(0,0,0,.15); padding: 6px 8px; font-size: 12px; display: none; max-width: 240px; z-index: 2; }
  .tip .h { color: var(--muted); }
  input[type=search] { width: 100%; margin-bottom: 8px; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px;
         background: var(--bg); color: var(--fg); font: inherit; font-size: 13px; }
  .tablebox { max-height: 28rem; overflow: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
  th { position: sticky; top: 0; background: var(--panel); text-align: left; font-weight: 500; color: var(--muted);
       padding: 6px 8px; border-bottom: 1px solid var(--line); cursor: pointer; user-select: none; white-space: nowrap; }
  td { padding: 6px 8px; border-bottom: 1px solid var(--line); }
  td.n { text-align: right; } th.n { text-align: right; }
  @media (max-width: 560px) { .narrow-hide { display: none; } }
</style>
</head>
<body>
<main>
  <header>
    <h1 id="title"></h1>
    <p class="take" id="take"></p>
    <p class="updated" id="updated"></p>
  </header>

  <section class="tiles">
    <div class="card"><div class="label">전체 커버리지</div><div class="num" id="t-cov"></div><div class="sub" id="t-basis"></div></div>
    <div class="card"><div class="label">긁은 분량</div><div class="num" id="t-min"></div><div class="sub" id="t-total"></div></div>
    <div class="card"><div class="label">클립 수</div><div class="num" id="t-clips"></div><div class="sub">예고편·트레일러 제외</div></div>
    <div class="card"><div class="label">아직 0%인 화</div><div class="num" id="t-empty"></div><div class="sub" id="t-emptylist"></div></div>
  </section>

  <section class="card">
    <h2>화별 타임라인</h2>
    <p class="note">각 화를 막대로 놓고, 파일명의 시작 시각부터 영상 길이만큼 칠했습니다. 캐릭터마다 줄이 따로 있고, 오른쪽 %는 겹친 구간을 한 번만 센 값입니다.</p>
    <div class="legend" id="legend"></div>
    <div class="chart" id="chart"><div class="tip" id="tip"></div></div>
  </section>

  <section class="card">
    <h2>클립 목록</h2>
    <p class="note">폴더별 녹화 파일. 머리글을 누르면 정렬됩니다.</p>
    <input type="search" id="search" placeholder="파일명·캐릭터 검색">
    <div class="tablebox">
      <table>
        <thead><tr>
          <th class="n" data-k="episode">화</th>
          <th data-k="character">캐릭터</th>
          <th data-k="file">파일</th>
          <th class="n" data-k="start_s">시작</th>
          <th class="n narrow-hide" data-k="end_s">끝</th>
          <th class="n" data-k="duration_s">길이</th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </section>
</main>

<script>
const DATA = __DATA__;
(function () {
  const $ = (id) => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const pct = (x) => (x * 100).toFixed(1) + '%';
  const chars = DATA.characters;
  const colorOf = (ch) => ch === '미분류' ? 'var(--unsorted)' : `var(--c${chars.indexOf(ch) % 6})`;

  const union = (iv) => {
    let total = 0, cs = null, ce = null;
    for (const [a, b] of iv.slice().sort((x, y) => x[0] - y[0])) {
      if (ce === null || a > ce) { if (ce !== null) total += ce - cs; cs = a; ce = b; } else ce = Math.max(ce, b);
    }
    return ce === null ? total : total + ce - cs;
  };

  const episodes = [];
  for (let ep = 1; ep <= DATA.episodes; ep++) {
    const c = DATA.clips.filter(r => r.episode === ep);
    const iv = c.map(r => [r.start_s, r.end_s]);
    const length = Math.max(DATA.episode_s, ...iv.map(v => v[1]));
    const covered = union(iv);
    episodes.push({ episode: ep, label: 'E' + String(ep).padStart(2, '0'), length, covered, coverage: covered / length, clips: c.length });
  }
  const total = episodes.reduce((s, e) => s + e.length, 0);
  const covered = episodes.reduce((s, e) => s + e.covered, 0);
  const empty = episodes.filter(e => e.clips === 0).map(e => e.episode);
  const best = episodes.slice().sort((a, b) => b.coverage - a.coverage)[0];

  document.title = DATA.title;
  $('title').textContent = DATA.title;
  $('updated').textContent = `${DATA.updated} 스캔`;
  $('take').textContent = `${DATA.episodes}화 중 ${Math.round(covered / 60)}분(${pct(covered / total)})을 긁었습니다. ` +
    (empty.length ? `${empty.join('·')}화는 아직 0%이고, ` : '') +
    (best && best.clips ? `가장 많이 긁은 화는 ${best.episode}화(${Math.round(best.coverage * 100)}%)입니다.` : '');
  $('t-cov').textContent = pct(covered / total);
  $('t-basis').textContent = `${DATA.episodes}화 × 약 ${Math.round(DATA.episode_s / 60)}분 기준`;
  $('t-min').textContent = `${Math.round(covered / 60)}분`;
  $('t-total').textContent = `전체 ${Math.round(total / 60)}분 중`;
  $('t-clips').textContent = DATA.clips.length;
  $('t-empty').textContent = `${empty.length}화`;
  $('t-emptylist').textContent = empty.length ? empty.join('·') + '화' : '없음';

  // legend
  chars.forEach(ch => {
    const c = DATA.clips.filter(r => r.character === ch);
    let sec = 0;
    for (let ep = 1; ep <= DATA.episodes; ep++) sec += union(c.filter(r => r.episode === ep).map(r => [r.start_s, r.end_s]));
    const sp = document.createElement('span');
    const sw = document.createElement('i'); sw.style.background = colorOf(ch);
    const t = document.createElement('span'); t.textContent = `${ch} · ${mmss(sec)} · ${c.length}개`;
    sp.append(sw, t); $('legend').appendChild(sp);
  });

  // timeline
  const chart = $('chart'), tip = $('tip');
  const el = (tag, attrs, parent) => { const n = document.createElementNS(SVG, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); parent.appendChild(n); return n; };
  function draw() {
    chart.querySelectorAll('svg').forEach(n => n.remove());
    const W = Math.max(280, chart.clientWidth);
    const laneH = 6, laneGap = 1, rowPad = 10, top = 22;
    const rowH = chars.length * (laneH + laneGap) + rowPad;
    const H = top + episodes.length * rowH;
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    chart.insertBefore(svg, tip);
    const leftW = 34, rightW = 50;
    const maxLen = Math.max(...episodes.map(e => e.length));
    const x = (s) => leftW + (W - leftW - rightW) * s / maxLen;
    const step = W < 480 ? 600 : 300;
    for (let t = 0; t <= maxLen; t += step) {
      el('line', { x1: x(t), x2: x(t), y1: top - 4, y2: H, stroke: 'var(--grid)' }, svg);
      el('text', { x: x(t), y: top - 8, 'text-anchor': 'middle' }, svg).textContent = `${t / 60}분`;
    }
    episodes.forEach((ep, i) => {
      const y0 = top + i * rowH + rowPad / 2, h = rowH - rowPad;
      el('text', { x: 0, y: y0 + h / 2, 'dominant-baseline': 'middle', class: 'ep' }, svg).textContent = ep.label;
      el('rect', { x: x(0), y: y0, width: x(ep.length) - x(0), height: h, rx: 3, fill: 'var(--track)', stroke: 'var(--line)' }, svg);
      const pt = el('text', { x: W, y: y0 + h / 2, 'text-anchor': 'end', 'dominant-baseline': 'middle' }, svg);
      pt.textContent = ep.clips ? pct(ep.coverage) : '0%';
      if (ep.clips) pt.setAttribute('style', 'fill: var(--fg)');
      DATA.clips.filter(c => c.episode === ep.episode).forEach(c => {
        const lane = chars.indexOf(c.character);
        const r = el('rect', { x: x(c.start_s), y: y0 + lane * (laneH + laneGap) + 1, width: Math.max(2, x(c.end_s) - x(c.start_s)),
                                height: laneH, rx: 2, fill: colorOf(c.character) }, svg);
        r.addEventListener('mousemove', (ev) => {
          tip.replaceChildren();
          const a = document.createElement('div'); a.className = 'h'; a.textContent = `${ep.label} · ${c.character}`;
          const b = document.createElement('div'); b.textContent = c.file;
          const d = document.createElement('div'); d.textContent = `${mmss(c.start_s)} – ${mmss(c.end_s)} (${mmss(c.duration_s)})`;
          tip.append(a, b, d); tip.style.display = 'block';
          const box = chart.getBoundingClientRect();
          const mx = ev.clientX - box.left, my = ev.clientY - box.top;
          tip.style.left = Math.min(Math.max(0, mx + 12), chart.clientWidth - tip.offsetWidth) + 'px';
          tip.style.top = (my + 14) + 'px';
        });
        r.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
      });
    });
  }
  draw();
  new ResizeObserver(draw).observe(chart);

  // table
  let sortK = 'episode', sortDir = 1;
  function drawTable() {
    const q = $('search').value.trim().toLowerCase();
    const rows = DATA.clips.filter(r => !q || (r.file + ' ' + r.character).toLowerCase().includes(q)).sort((a, b) => {
      const va = a[sortK], vb = b[sortK];
      return (va < vb ? -1 : va > vb ? 1 : a.start_s - b.start_s) * sortDir;
    });
    const tb = $('rows'); tb.replaceChildren();
    rows.forEach(r => {
      const tr = document.createElement('tr');
      [[r.episode, 'n'], [r.character], [r.file], [mmss(r.start_s), 'n'], [mmss(r.end_s), 'n narrow-hide'], [mmss(r.duration_s), 'n']].forEach(([v, cls]) => {
        const td = document.createElement('td'); td.textContent = v; if (cls) td.className = cls; tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    document.querySelectorAll('th').forEach(th => th.setAttribute('aria-sort', th.dataset.k === sortK ? (sortDir > 0 ? 'ascending' : 'descending') : 'none'));
  }
  document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {
    if (sortK === th.dataset.k) sortDir = -sortDir; else { sortK = th.dataset.k; sortDir = 1; }
    drawTable();
  }));
  $('search').addEventListener('input', drawTable);
  drawTable();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
