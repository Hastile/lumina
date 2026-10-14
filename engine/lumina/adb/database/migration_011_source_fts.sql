-- Adds FTS5 full-text search over source.work_title and source.subtitle.
--
-- Why this was missing and why it matters: retrieval matched description,
-- dialogue and tags, and none of those carry the work. A cut from 이세계
-- 콰르텟 was unreachable by a search for "콰르텟" even though the title was
-- sitting on its source row - measured on the first committed job.
--
-- CLAUDE.md's own shot-naming rule ("Name the work, the character, the
-- moment") assumes the work is part of the query, so leaving it out of the
-- index made the documented way of searching the one way that could not work.
--
-- External content (no duplication) over `source`, whose source_id is the
-- rowid. One source covers many cuts, so the finder joins source -> media ->
-- scene_cut and every cut of a matching work becomes a candidate. That is
-- weak evidence on its own and deliberately so: the finder's combined-
-- evidence bonus is what lifts a cut that matched the work *and* something
-- specific above one that only shares a title.

CREATE VIRTUAL TABLE source_search USING fts5(
  work_title,
  subtitle,
  content='source',
  content_rowid='source_id'
);

CREATE TRIGGER source_ai AFTER INSERT ON source BEGIN
  INSERT INTO source_search(rowid, work_title, subtitle)
    VALUES (new.source_id, new.work_title, new.subtitle);
END;
CREATE TRIGGER source_ad AFTER DELETE ON source BEGIN
  INSERT INTO source_search(source_search, rowid, work_title, subtitle)
    VALUES ('delete', old.source_id, old.work_title, old.subtitle);
END;
CREATE TRIGGER source_au AFTER UPDATE ON source BEGIN
  INSERT INTO source_search(source_search, rowid, work_title, subtitle)
    VALUES ('delete', old.source_id, old.work_title, old.subtitle);
  INSERT INTO source_search(rowid, work_title, subtitle)
    VALUES (new.source_id, new.work_title, new.subtitle);
END;

-- Backfill for rows that predate the index.
INSERT INTO source_search(rowid, work_title, subtitle)
  SELECT source_id, work_title, subtitle FROM source;
