-- Adds FTS5 full-text search over a cut's tags.
--
-- Why this was missing and why it matters: cut_search indexes
-- scene_cut.description_text and dialogue_search indexes dialogue.text, and
-- that was the whole of retrieval. Character names do not live in either -
-- they are attached as `subject` tags, by the WD tagger, by the booru hits
-- reverse search returns, and by the exemplar matcher. So a search for a
-- character matched only the cuts where the name happened to make it into
-- the prose, and missed every cut where it was recorded properly.
--
-- Unlike the other two, this cannot be an external-content table: tags reach
-- a cut through the cut_tag join, so there is no column on one row to point
-- at. It is a regular FTS5 table holding one document per cut - the tag
-- values joined - which also means plain DELETE works, and rebuilding one
-- cut's document on every tag change stays a two-statement trigger.
--
-- Duplication is real but tiny (tag text is short, and it is stored once per
-- cut rather than once per cut_tag row).

CREATE VIRTUAL TABLE tag_search USING fts5(tag_text);

-- Synonyms are indexed under both names. merge_tag_into deliberately leaves
-- cut_tag rows pointing at the synonym and resolves at read time, so
-- indexing only the stored value would make a search for the canonical name
-- miss every cut tagged before the merge - and a search for the synonym miss
-- everything tagged after it.
CREATE VIEW cut_tag_text AS
  SELECT ct.cut_id AS cut_id,
         group_concat(
           t.value || CASE
             WHEN c.value IS NOT NULL AND c.value <> t.value THEN ' ' || c.value
             ELSE ''
           END, ' ') AS tag_text
    FROM cut_tag ct
    JOIN tag t ON t.tag_id = ct.tag_id
    LEFT JOIN tag c ON c.tag_id = t.canonical_tag_id
   GROUP BY ct.cut_id;

CREATE TRIGGER cut_tag_ai AFTER INSERT ON cut_tag BEGIN
  DELETE FROM tag_search WHERE rowid = new.cut_id;
  INSERT INTO tag_search(rowid, tag_text)
    SELECT cut_id, tag_text FROM cut_tag_text WHERE cut_id = new.cut_id;
END;

CREATE TRIGGER cut_tag_ad AFTER DELETE ON cut_tag BEGIN
  DELETE FROM tag_search WHERE rowid = old.cut_id;
  INSERT INTO tag_search(rowid, tag_text)
    SELECT cut_id, tag_text FROM cut_tag_text WHERE cut_id = old.cut_id;
END;

-- A tag's value changing (or being merged into a canonical) rewrites every
-- cut carrying it, not just one.
CREATE TRIGGER tag_au AFTER UPDATE ON tag BEGIN
  DELETE FROM tag_search
   WHERE rowid IN (SELECT cut_id FROM cut_tag WHERE tag_id = new.tag_id);
  INSERT INTO tag_search(rowid, tag_text)
    SELECT cut_id, tag_text FROM cut_tag_text
     WHERE cut_id IN (SELECT cut_id FROM cut_tag WHERE tag_id = new.tag_id);
END;

-- Backfill for rows that predate the index.
INSERT INTO tag_search(rowid, tag_text) SELECT cut_id, tag_text FROM cut_tag_text;
