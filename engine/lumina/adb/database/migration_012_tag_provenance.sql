-- Adds cut_tag.source - who proposed each tag on a committed cut.
--
-- scene_cut.description_text has its provenance recorded upstream
-- (ingest_cut.description_source) and the job identity has metadata_source,
-- but tags had nothing anywhere. Tags are what tag_search indexes and what a
-- character search actually joins on, so the field where a wrong value does
-- the most damage was the one field carrying no record of its evidence.
--
-- The vocabulary matches ingest_cut_tag.source. 'pending' means the
-- provenance was never recorded - every row that predates this column, plus
-- anything written by a path that does not say (the exemplar matcher, reverse
-- image search). It is deliberately not 'human': claiming a person reviewed
-- something nobody recorded is exactly the laundering this column exists to
-- prevent.

ALTER TABLE cut_tag ADD COLUMN source TEXT NOT NULL DEFAULT 'pending'
  CHECK(source IN ('pending','session','human','model'));
