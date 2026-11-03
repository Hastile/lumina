---
name: source-db-frozen-schema
description: "The Lumina source DB schema is frozen at v1 by the user's rule; changes go into data (fact kinds, lexicon), never columns"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3899aeb6-a6c5-49f4-97f8-45adc1ac9f74
  modified: 2026-10-22T08:10:46.668Z
---

The user does not want to pay maintenance for schema changes (2026-10-22: "나중에 구조 바꿀 여지 되면 유지보수 비용이 높아짐. 난 그러기는 싫음"). They approved freezing `engine/lumina/sourcedb/schema.sql` at user_version 1 (commit c99ca45). A test pins its hash.

- New metadata = a new `fact_kind` row (`KINDS` in db.py) or a lexicon row. Never a column or a table.
- The user rejected a fade column as unneeded. The DB's purpose is metadata that lets an AI search and understand scenes, not edit rules.
- Do not trust local models for understanding; the user said they are not smart enough. Local models measure: scene cuts, luma, pHash, WD tagger, SigLIP, Whisper. Claude Sonnet writes descriptions through headless `claude -p` on contact sheets.
- Every machine fact is `proposed`. It becomes `accepted` only when two producers agree, or when the caption it was used under names it, or when a person accepts it.

**Why:** a schema change means migrations and code in many places, and the old adb-mcp DB had piled up 12 migrations.

**How to apply:** if a need seems to require a schema change, first express it as a fact kind, an entity, or JSON in `usage.props`, and ask the user before touching schema.sql. Related: [[shorts-automation-pipeline]], [[elevenlabs-tts]].
