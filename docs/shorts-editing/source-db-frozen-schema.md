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

**Upkeep (2026-11-03).** The schema stays frozen; what wears is the data, so it is checked, not migrated.
- `python -m lumina.sourcedb health` (read-only, ~2 s on 1.4M facts) reports, each with its fixing command: media cut by an older cuts version (`recut --stale`), descriptions whose span no longer matches a cut (`describe --misaligned`, cost-capped by `--max-cost`), fact rows whose value/num/ref does not fit their kind, duplicate entity names (`merge --keep ID DUP…`), search documents out of step (`health --fix-docs`), cuts without embeddings, missing files (`--files`), and the backup age.
- `python -m lumina.sourcedb backup` writes a consistent copy (`VACUUM INTO`, safe while the DB is in use) to `~/.lumina/backups`, checks its integrity and keeps the newest 10. Run it before a re-cut, a merge or a bulk describe.
- **cut_id is a search handle, not an identity.** A re-cut replaces a media's cuts and their ids. Plans, notes and handoffs that outlive the session name the file and the source span (`clip_place_shots` returns `path` and `source_seconds`), as `usage` and `fact` already do.
