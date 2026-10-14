-- Staging schema for the ingest web app (database/ingest.db).
--
-- Deliberately a SEPARATE database file from source.db. source.db is the
-- curated store: everything in it is searchable through the FTS5 tables, and
-- source_candidate_finder will happily return any row that is there. An
-- upload that has been scene-detected but not yet reviewed is exactly the
-- kind of row that must never come back from a search - the descriptions have
-- not been checked by anyone yet, and the cut boundaries are still ffmpeg's
-- raw opinion.
--
-- Keeping staging out of source.db means a half-finished job cannot pollute
-- search results no matter what state it is left in, and abandoning a job is
-- just deleting rows from a file nothing else reads. The commit step
-- (ingest_store.commit_job) is the only path from here into source.db, and it
-- goes through media_ingest.ingest_cuts_to_db like every other writer.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS ingest_job (
  job_id          TEXT PRIMARY KEY,                -- uuid4 hex, also the upload folder name
  original_name   TEXT NOT NULL,
  media_type      TEXT NOT NULL CHECK(media_type IN ('image','video')),
  file_path       TEXT NOT NULL,                   -- absolute path to the stored upload
  file_hash       TEXT,
  duration_ms     INTEGER,                         -- NULL for images
  fps             REAL,
  width           INTEGER,
  height          INTEGER,
  -- uploaded  : bytes on disk, nothing analyzed yet
  -- analyzed  : scene detection done, cuts staged, descriptions still missing
  -- described : every kept cut has a description
  -- committed : written through to source.db; job is read-only from here
  --
  -- 'describing' is retained but unwritten: nothing in this app runs a long
  -- description pass any more, so there is no state between "cuts exist" and
  -- "they are all described". Dropping the value would rewrite the CHECK for
  -- nothing.
  status          TEXT NOT NULL DEFAULT 'uploaded'
                  CHECK(status IN ('uploaded','analyzing','analyzed','describing','described','committed','failed')),
  status_detail   TEXT,                            -- last error / progress message
  -- Source identification. Held here (not in source.db) until commit, because
  -- the whole point of the review step is that every field stays editable.
  work_title      TEXT,
  source_category TEXT,
  is_official     INTEGER,
  origin_ref      TEXT,
  subtitle        TEXT,
  episode         INTEGER,
  source_offset_ms INTEGER,
  summary_text    TEXT,
  identity_note   TEXT,                            -- why this is what it is, for the reviewer
  srt_path        TEXT,
  -- Who filled the identity in. 'session' is an MCP session writing through
  -- ingest_bridge; 'human' is someone typing in the review pane. metadata_note
  -- is a one-line trace of what a session did, which is what replaced the
  -- progress messages the old in-app passes used to leave behind.
  metadata_source TEXT NOT NULL DEFAULT 'pending'
                  CHECK(metadata_source IN ('pending','session','human')),
  metadata_note   TEXT,
  metadata_at     INTEGER,
  -- Set once committed so re-opening a finished job can link back to what it
  -- became, and so a re-commit updates rather than duplicates.
  committed_media_id INTEGER,
  created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  updated_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000)
);
CREATE INDEX IF NOT EXISTS idx_job_status ON ingest_job(status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_hash ON ingest_job(file_hash) WHERE file_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingest_cut (
  cut_id        INTEGER PRIMARY KEY,
  job_id        TEXT NOT NULL REFERENCES ingest_job(job_id) ON DELETE CASCADE,
  cut_index     INTEGER NOT NULL,
  start_ms      INTEGER NOT NULL,
  end_ms        INTEGER NOT NULL,
  -- ffmpeg's original boundaries, never overwritten by an edit. Keeping them
  -- is what makes "how far did I drag this off the detector's answer" a
  -- question the UI can answer, and what makes a revert possible without
  -- re-running detection over the whole file.
  detected_start_ms INTEGER,
  detected_end_ms   INTEGER,
  -- 'high' | 'low' from media_ingest.build_cut_boundaries. 'low' means the
  -- detector found a cluster of events (a fade/dissolve) instead of one clean
  -- spike, so the exact boundary is approximate - the UI flags these as the
  -- ones worth checking by eye.
  boundary_confidence TEXT,
  boundary_snapped    INTEGER NOT NULL DEFAULT 0,  -- snapped onto an SRT cue edge
  transition_span_ms  INTEGER,
  frame_path       TEXT,                           -- representative still
  frame_ms         INTEGER,
  description_text TEXT,
  shot_type        TEXT,
  -- Provenance of description_text, so a reviewer can tell at a glance which
  -- sentences they have already been through themselves. Nothing downstream
  -- branches on it; it is for the person doing the reviewing.
  --
  -- 'legacy_model' is what the pre-session Qwen/claude-cli passes wrote. It
  -- is kept distinct from 'session' rather than folded into it because those
  -- sentences were never reviewed, and calling them 'session' would launder
  -- unchecked output into the provenance this column exists to report.
  description_source TEXT NOT NULL DEFAULT 'pending'
                     CHECK(description_source IN ('pending','session','human','legacy_model')),
  annotation_note TEXT,
  -- 'keep' rows are committed; 'drop' rows are retained so an accidental
  -- discard is undoable inside the job, but are skipped at commit time.
  disposition   TEXT NOT NULL DEFAULT 'keep' CHECK(disposition IN ('keep','drop')),
  exported_path TEXT,                              -- set by split-to-sequence export
  UNIQUE(job_id, cut_index)
);
CREATE INDEX IF NOT EXISTS idx_cut_job ON ingest_cut(job_id, cut_index);

-- Who proposed each tag.
--
-- description_source records this for the prose and metadata_source for the
-- job identity, but tags had nothing - and a tag is what search actually
-- joins on, so the field where a wrong value does the most damage was the one
-- field carrying no record of its evidence. 'model' exists so a local
-- classifier or VLM can propose tags without its guesses becoming
-- indistinguishable from reviewed decisions, the same separation
-- description_source keeps with 'legacy_model'.
CREATE TABLE IF NOT EXISTS ingest_cut_tag (
  cut_id   INTEGER NOT NULL REFERENCES ingest_cut(cut_id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  value    TEXT NOT NULL,
  source   TEXT NOT NULL DEFAULT 'pending'
           CHECK(source IN ('pending','session','human','model')),
  PRIMARY KEY(cut_id, category, value)
);

-- What a classifier recognised in this cut's frames, in its own vocabulary.
--
-- Deliberately not ingest_cut_tag. These are Danbooru strings with confidence
-- scores, and this database's tag vocabulary is a closed set of Korean values
-- on seven axes; promoting one to the other would make a classifier's guess
-- indistinguishable from a reviewed decision. It is evidence, shown next to
-- the frame so a description can be checked against something that looked at
-- the same pixels, and it survives clearing a cut's description because it is
-- a fact about the frame rather than anybody's writing.
CREATE TABLE IF NOT EXISTS ingest_cut_signal (
  cut_id INTEGER NOT NULL REFERENCES ingest_cut(cut_id) ON DELETE CASCADE,
  kind   TEXT NOT NULL CHECK(kind IN ('general','character','rating')),
  value  TEXT NOT NULL,
  score  REAL NOT NULL,
  PRIMARY KEY(cut_id, kind, value)
);

CREATE TABLE IF NOT EXISTS ingest_cut_dialogue (
  dialogue_id INTEGER PRIMARY KEY,
  cut_id      INTEGER NOT NULL REFERENCES ingest_cut(cut_id) ON DELETE CASCADE,
  start_ms    INTEGER,
  end_ms      INTEGER,
  speaker     TEXT,
  text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cutdlg_cut ON ingest_cut_dialogue(cut_id);

CREATE TABLE IF NOT EXISTS ingest_schema_version (
  version    INTEGER PRIMARY KEY,
  applied_at INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000)
);
