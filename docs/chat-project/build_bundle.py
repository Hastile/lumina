"""Build uploadable reference snapshots from the repository's editing grammar.

Run from any directory. Does not modify originals or Lumina projects.
"""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
GROUPS = {
    "02_COMMON_GRAMMAR.md": ("공통 편집 문법", [
        "docs/editing-grammar/README.md",
        "docs/editing-grammar/캐디노트_편집_지침_v1.0.md",
        "docs/editing-grammar/common-cuts.md",
        "docs/editing-grammar/cut-quality.md",
        "docs/editing-grammar/caption-quality.md",
    ]),
    "03_SHORTS_GRAMMAR.md": ("쇼츠 편집 문법", [
        "docs/shorts-editing/README.md",
        "docs/shorts-editing/caddynote-audience.md",
        "docs/shorts-editing/shorts-edit-review-lessons.md",
        "docs/shorts-editing/shorts-cut-grammar.md",
        "docs/shorts-editing/shorts-caption-effect-grammar.md",
        "docs/shorts-editing/shorts-sfx-grammar.md",
        "docs/shorts-editing/shorts-tmi-grammar.md",
        "docs/shorts-editing/shorts-copy-grammar.md",
        "docs/shorts-editing/shorts-project-per-folder.md",
        "docs/shorts-editing/move-sources-not-copy.md",
        "docs/shorts-editing/shorts-marker-workflow.md",
        "docs/shorts-editing/shorts-upload-title-grammar.md",
    ]),
    "04_LONGFORM_GRAMMAR.md": ("롱폼 편집 문법과 자막", [
        "docs/editing-grammar/longform.md",
        "docs/longform-editing/caption-segmentation.md",
    ]),
    "05_REFERENCE_EVIDENCE.md": ("레퍼런스 근거와 검수 한계", [
        "docs/longform-editing/reference-benchmark/README.md",
        "docs/longform-editing/reference-benchmark/lumina-review/README.md",
        "docs/editing-grammar/references/story-Czmkes4yAOA.md",
        "docs/editing-grammar/references/dugong-frieren-opening.md",
    ]),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def portable_text(path):
    body = path.read_text(encoding="utf-8-sig")
    body = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.S)

    def link(match):
        label, target = match.group(1), match.group(2)
        if re.match(r"[a-zA-Z][\w+.-]*:", target) or target.startswith("#"):
            return match.group(0)
        target_path = target.split("#", 1)[0]
        resolved = (path.parent / target_path).resolve()
        return f"{label}（로컬 참조: `{resolved.as_posix()}`）"

    body = re.sub(r"!?\[([^\]]*)\]\(([^\n)]+)\)", link, body)

    def wiki(match):
        name = match.group(1)
        candidates = list((ROOT / "docs").rglob(name + ".md"))
        if len(candidates) == 1:
            return f"`{candidates[0].as_posix()}`"
        return f"자료명 `{name}` (별도 자료; 필요 시 Bridge로 검색)"

    return re.sub(r"\[\[([^\]]+)\]\]", wiki, body).strip()


def main():
    manifest = {"version": 1, "prepared_date": "2026-10-31", "files": []}
    for output, (title, sources) in GROUPS.items():
        parts = [
            f"# {title} — Chat 참고 파일\n",
            "이 파일은 저장소 원문을 묶은 배포 스냅샷이다. 실행은 "
            "`PROJECT_INSTRUCTIONS.md`와 `01_MCP_WORKFLOW.md`, 현재 MCP 도구 정의를 따른다. "
            "과거 도구명·CLI·개인 메모리 지시는 현재 Chat 기능을 보장하지 않는다. "
            "공통 문법과 사용자 확정 수정이 과거 빈도 통계보다 우선한다. "
            "날짜가 붙은 관찰과 미완료 상태는 당시 기록이며 현재 상태는 다시 확인한다. "
            "원문 링크는 로컬 출처를 표시한다. 이미지와 외부 자료는 별도로 열어야 한다.\n",
        ]
        entries = []
        for source in sources:
            path = ROOT / source
            raw = path.read_bytes()
            entries.append({"path": source, "sha256": sha(raw)})
            parts.append(f"\n---\n\n## 원본: {source}\n\n" + portable_text(path) + "\n")
        result = "\n".join(parts).encode("utf-8")
        (OUT / output).write_bytes(result)
        manifest["files"].append({"output": output, "sha256": sha(result), "sources": entries})
        print(f"{output}: {len(sources)} sources, {len(result):,} bytes")
    (OUT / "SOURCES.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
