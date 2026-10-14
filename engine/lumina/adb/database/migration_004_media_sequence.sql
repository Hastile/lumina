-- Migration 004: split-child media ordering.
--
-- A screen recording is one file that contains many consecutive shots. The
-- ingest web app can export each detected cut as its own file (so the shot
-- can be dropped into Premiere directly instead of being trimmed there every
-- time), and those exported files are ordinary media rows - but they are not
-- independent media. They are one recording taken apart, and the order they
-- came out in *is* the sequence.
--
-- Without these two columns that ordering only survived in file names, which
-- means it did not survive at all: renaming a file, or ingesting the parent
-- twice, silently loses which shot came before which. parent_media_id points
-- a child at the recording it was cut from; sequence_index is its position in
-- that recording, 0-based and matching the scene_cut.cut_index it came from.
--
-- Both are NULL for ordinary media (a standalone image, a recording that was
-- never split), so nothing existing changes meaning. Search is deliberately
-- NOT filtered on parent_media_id: a split child is a real, placeable file and
-- should be findable. Use canonical_media_id (migration 003) when a row should
-- be hidden from search, not this.

ALTER TABLE media ADD COLUMN parent_media_id INTEGER REFERENCES media(media_id);

ALTER TABLE media ADD COLUMN sequence_index INTEGER;

CREATE INDEX IF NOT EXISTS idx_media_parent ON media(parent_media_id, sequence_index);
