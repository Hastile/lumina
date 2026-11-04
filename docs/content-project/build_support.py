"""Build reference files and distribution from immutable source snapshots."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
DOWNLOADS = Path(r'C:\Users\HETX\Downloads\캐디노트_지침_2026-10-24')
BACKUP = OUT / 'ORIGINALS_BACKUP.zip'
REPO_INPUTS = [
    'docs/editing-grammar/README.md', 'docs/editing-grammar/common-cuts.md',
    'docs/editing-grammar/캐디노트_편집_지침_v1.0.md', 'docs/editing-grammar/longform.md',
    'docs/editing-grammar/lumina-workflow.md', 'docs/longform-editing/caption-segmentation.md',
    'docs/shorts-editing/README.md', 'docs/shorts-editing/shorts-cut-grammar.md',
    'docs/shorts-editing/shorts-caption-effect-grammar.md', 'docs/shorts-editing/shorts-sfx-grammar.md',
    'docs/shorts-editing/shorts-marker-workflow.md', 'docs/shorts-editing/shorts-edit-review-lessons.md',
    'docs/shorts-editing/shorts-tmi-grammar.md', 'docs/shorts-editing/shorts-copy-grammar.md',
    'docs/shorts-editing/shorts-project-per-folder.md', 'docs/shorts-editing/move-sources-not-copy.md',
    'docs/shorts-editing/shorts-upload-title-grammar.md', 'docs/text-panel.md',
    'docs/chat-project/PROJECT_INSTRUCTIONS.md', 'docs/chat-project/01_MCP_WORKFLOW.md',
    'docs/chat-project/05_REFERENCE_EVIDENCE.md',
]
FILES = ['PROJECT_INSTRUCTIONS.md', '01_COMMON.md', '02_SCRIPT.md', '03_EDITING.md',
         '04_LUMINA_MCP.md', '05_RESEARCH_REFERENCE.md', '06_EXAMPLES_REFERENCE.md',
         'README.md', 'MERGE_NOTES.md', 'UPDATE_CHECKLIST.md', 'SOURCES.json']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def between(body, start, end=None):
    a = body.index(start)
    b = body.index(end, a + len(start)) if end else len(body)
    return body[a:b].strip()


def add_research_examples(body):
    examples = json.loads((OUT / 'research_ip_examples.json').read_text(encoding='utf-8'))
    for item in examples:
        anchor = item['anchor']
        assert body.count(anchor + '\n') == 1, anchor
        start = body.index(anchor + '\n') + len(anchor) + 1
        if item.get('intro'):
            body = body[:start] + '\n' + item['text'] + '\n' + body[start:]
            continue
        level = len(anchor) - len(anchor.lstrip('#'))
        following = re.search(r'^#{1,' + str(level) + r'} ', body[start:], re.M)
        end = start + following.start() if following else len(body)
        separator = re.search(r'\n---\s*$', body[start:end])
        if separator:
            end = start + separator.start()
        assert item.get('sources'), anchor
        links = ', '.join(f'[{title}]({url})' for title, url in item['sources'])
        block = '\n\n**실제 IP 예시와 구분 포인트**\n\n' + item['text'] + '\n\n근거: ' + links + '.\n\n'
        body = body[:end] + block + body[end:]
    assert body.count('**실제 IP 예시와 구분 포인트**') == len(examples) - 1
    return body


def main():
    if not BACKUP.exists():
        sources = []
        with zipfile.ZipFile(BACKUP, 'x', zipfile.ZIP_DEFLATED) as z:
            paths = [(p, 'downloads/' + p.name) for p in sorted(DOWNLOADS.iterdir()) if p.is_file() and p.suffix in ('.md', '.txt')]
            paths += [(ROOT / name, 'repository/' + name) for name in REPO_INPUTS]
            for path, entry in paths:
                data = path.read_bytes()
                z.writestr(entry, data)
                sources.append({'path': str(path), 'archive_entry': entry, 'sha256': sha(data), 'bytes': len(data)})
            z.writestr('manifest.json', json.dumps(sources, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(BACKUP) as z:
        sources = json.loads(z.read('manifest.json'))
        for source in sources:
            assert sha(z.read(source['archive_entry'])) == source['sha256']
        def read(name):
            return z.read(name).decode('utf-8-sig').replace('\r\n', '\n')
        research = read('downloads/캐릭터_디자인_분석_STRICT_MASTER_v2.md')
        research = add_research_examples(research)
        (OUT / '05_RESEARCH_REFERENCE.md').write_text(
            '# 전문 리서치 참고 — 적용 범위\n\n'
            '깊은 캐릭터 디자인 조사나 전체 리서치 보고서를 만들 때 아래 전문을 사용한다. '
            '간단한 대본 교정·자막 편집에 전체 조사 목차를 강제하지 않는다. '
            '조사 보고서의 순서는 대본 순서가 아니며 낭독 원고는 01/02를 따른다. '
            '보고서의 인용 표시 문형과 낭독 말투는 역할이 다르다. '
            '충분한 조사로 후속 검색을 줄이는 목표는 미확인 사실·변경된 자료를 재확인하지 말라는 뜻이 아니다. '
            '원문의 전문 개념·주장 등급·반례·출처 장부·보고서 산출물을 보존하고, 개념별 실제 IP 예시와 출처를 추가했다. '
            '현재 사용자 지시와 통합본의 적용 범위가 원문의 독립 실행 선언보다 우선한다.\n\n'
            '출처: 캐릭터_디자인_분석_STRICT_MASTER_v2.md (2026-10-23), 원본은 백업 ZIP과 SOURCES.json 참조.\n\n---\n\n' + research,
            encoding='utf-8')
        style = read('downloads/캐디노트_통합_대본_문체_지침_v2.2.txt')
        shorts = read('downloads/쇼츠-대본-지침_v4.4.txt')
        longform = read('downloads/캐디노트_롱폼_대본_지침_v1.1.md')
        content = read('downloads/캐디노트_콘텐츠_지침_v4.md')
        parts = [
            '# 사례·관찰 참고 — 규칙 본문과 분리\n\n'
            '아래는 원문에서 보존한 예문·과거 관찰이다. 현행 실행 규칙은 01~04다. '
            '가상 예문·샘플의 작품 설정·제작 의도·팬 반응을 사실 근거로 재사용하지 않는다. '
            '과거 조회수·시청자·지속률은 기록 당시 표본이며 현재 수치나 성과 인과의 증명이 아니다. '
            '표본에서 강한 표현으로 기술한 원인 추정도 검증된 법칙으로 올리지 않는다. '
            '옛 파일명·모델 이름·CLI 경로는 이관 이력이며 해당 도구가 현재 연결돼 있다는 뜻이 아니다.\n',
            '## A. 문체 판정과 복합 문단 예시\n\n출처: 통합 대본 문체 v2.2, 8~9장. **가상 예문**이다.\n\n' + between(style, '8. 판정 및 수정 예시', '10. 사용자 요청에 따른 출력 규칙'),
            '## B. 쇼츠 샘플\n\n출처: 쇼츠 대본 v4.4, 부록 A/B. 형식과 표현의 사례이며 작품 사실은 재검증한다.\n\n' + between(shorts, '# 부록 A.'),
            '## C. 과거 시청자 관찰\n\n출처: 콘텐츠 v4의 0-1장. 기록 당시 표본이다.\n\n' + between(content, '## 0-1.', '## 1.'),
            '## D. 롱폼 과거 시청 지속 관찰\n\n출처: 롱폼 대본 v1.1의 0-1장. 실행 규칙은 02에 통합했다.\n\n' + between(longform, '## 0-1.', '## 1.'),
            '## E. 연출 레퍼런스와 검수 범위\n\n출처: 기존 Chat 패키지 05_REFERENCE_EVIDENCE.md. 표본·미청취 한계를 포함해 보존한다.\n\n' + read('repository/docs/chat-project/05_REFERENCE_EVIDENCE.md'),
        ]
        (OUT / '06_EXAMPLES_REFERENCE.md').write_text('\n\n---\n\n'.join(parts) + '\n', encoding='utf-8')
    # Validate the delivery set and local references in the authored files.
    for name in FILES:
        if name == 'SOURCES.json':
            continue
        body = (OUT / name).read_text(encoding='utf-8')
        assert body.strip() and '\ufffd' not in body, name
        if name not in ('05_RESEARCH_REFERENCE.md', '06_EXAMPLES_REFERENCE.md'):
            for ref in re.findall(r'`((?:0[1-6]_[A-Z_]+|PROJECT_INSTRUCTIONS|MERGE_NOTES)\.md)`', body):
                assert (OUT / ref).is_file(), (name, ref)
    manifest = {'version': '1.5', 'prepared_date': '2026-11-01',
                'sources': sources, 'backup_sha256': sha(BACKUP.read_bytes()),
                'supplements': [{'file': '../editing-grammar/caption-quality.md',
                                'purpose': '초반 공백·종결부호·긴 자막 후처리; 03에 통합',
                                'sha256': sha((ROOT / 'docs/editing-grammar/caption-quality.md').read_bytes())},
                               {'file': '../editing-grammar/cut-quality.md',
                                'purpose': '컷/프레임 튐 분류와 연속 프레임 검수; 03에 통합',
                                'sha256': sha((ROOT / 'docs/editing-grammar/cut-quality.md').read_bytes())},
                               {'file': 'research_ip_examples.json',
                                'purpose': '공식 자료·원연구에 근거한 실제 IP 개념 예시; 해석 구분 및 출처를 05에 포함',
                                'sha256': sha((OUT / 'research_ip_examples.json').read_bytes())}],
                'outputs': [{'file': name, 'sha256': sha((OUT / name).read_bytes()), 'bytes': (OUT / name).stat().st_size}
                            for name in FILES if name != 'SOURCES.json']}
    (OUT / 'SOURCES.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(OUT / 'Caddynote_Unified_Project.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(OUT / name, name)
    print(json.dumps({'sources': len(sources), 'upload_references': 6,
                      'instructions_characters': len((OUT / 'PROJECT_INSTRUCTIONS.md').read_text(encoding='utf-8')),
                      'bundle': str(OUT / 'Caddynote_Unified_Project.zip')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
