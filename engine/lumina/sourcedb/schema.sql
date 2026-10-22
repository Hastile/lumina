-- Lumina source DB: what is in every picture and recording, so an AI can
-- search and understand scenes without looking at pixels.
--
-- Frozen at user_version 1. The rule that keeps it frozen: columns hold only
-- facts that every row has and whose meaning cannot change (identity, file
-- properties, time spans, edit properties). Everything an analyzer, a model
-- or a person says about the content is a row in `fact`, named by a kind in
-- `fact_kind`. A new analyzer, a new model version or a new kind of
-- metadata adds rows, never columns.
--
-- Times are integer ticks (lumina.timebase, 254,016,000,000 per second).
-- Every common frame rate divides it, so a cut edge stored here lands on the
-- same frame on the timeline - no millisecond rounding at 23.976 or 29.97.
--
-- Metadata is anchored to media time, not to cut ids. Cuts are only the
-- current segmentation used as search units; re-running scene detection
-- replaces them without losing a reviewed fact or a usage record.

-- The vocabulary of `fact.kind`. Adding a kind is an INSERT, not a schema
-- change; the foreign key stops a typo from silently creating a new one.
CREATE TABLE fact_kind (
  kind       TEXT PRIMARY KEY,
  value_type TEXT NOT NULL CHECK (value_type IN ('text', 'num', 'ref', 'json')),
  searchable INTEGER NOT NULL DEFAULT 0 CHECK (searchable IN (0, 1)),  -- goes into cut_doc
  note       TEXT NOT NULL
);

-- Named things a fact can point at: works, characters, people, studios.
-- `type` is the fact kind that refers to it, so the same foreign key that
-- validates facts validates entity types.
CREATE TABLE entity (
  entity_id   INTEGER PRIMARY KEY,
  type        TEXT NOT NULL REFERENCES fact_kind (kind),
  name        TEXT NOT NULL,             -- the channel's Korean name
  aliases     TEXT NOT NULL DEFAULT '',  -- other names, one per line (original, English, nicknames)
  catalog_ref TEXT,                      -- 'source:key' in an outside catalog, e.g. 'danbooru:hatsune_miku'
  UNIQUE (type, name)
);

-- One row per distinct content. Identity is the hash, so moving or copying
-- a file never detaches its metadata.
CREATE TABLE media (
  media_id       INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL UNIQUE,
  kind           TEXT NOT NULL CHECK (kind IN ('video', 'image', 'audio')),
  width          INTEGER,
  height         INTEGER,
  duration_ticks INTEGER,                -- NULL for images
  fps_num        INTEGER,                -- NULL for images and audio
  fps_den        INTEGER,
  -- A file cut out of a longer one (a split recording, a crop): where it
  -- starts inside the parent, so its times map back to the original.
  parent_id      INTEGER REFERENCES media (media_id),
  parent_offset_ticks INTEGER,
  -- Near-duplicate of another media (same picture, other format or size).
  dup_of         INTEGER REFERENCES media (media_id),
  -- staged = analysed but not yet searchable; live = searchable;
  -- dropped = kept so it is not re-added; missing = no file on disk any more.
  status         TEXT NOT NULL DEFAULT 'staged'
                 CHECK (status IN ('staged', 'live', 'dropped', 'missing')),
  added_ms       INTEGER NOT NULL
);
CREATE INDEX idx_media_status ON media (status);
CREATE INDEX idx_media_parent ON media (parent_id);

-- Every path a media is known at. Size and mtime let a rescan skip
-- unchanged files without hashing them again.
CREATE TABLE media_file (
  path      TEXT PRIMARY KEY,
  media_id  INTEGER NOT NULL REFERENCES media (media_id) ON DELETE CASCADE,
  size      INTEGER NOT NULL,
  mtime_ms  INTEGER NOT NULL,
  seen_ms   INTEGER NOT NULL             -- last scan that found it
);
CREATE INDEX idx_media_file_media ON media_file (media_id);

-- The search units: shots of a video, or the whole of an image.
-- [start_ticks, end_ticks) in media time; an image is one cut at 0, 0.
CREATE TABLE cut (
  cut_id         INTEGER PRIMARY KEY,
  media_id       INTEGER NOT NULL REFERENCES media (media_id) ON DELETE CASCADE,
  idx            INTEGER NOT NULL,
  start_ticks    INTEGER NOT NULL,
  end_ticks      INTEGER NOT NULL,
  keyframe_ticks INTEGER NOT NULL,       -- the frame its thumbnail, hash and embedding come from
  phash          INTEGER,                -- 64-bit DCT hash of the keyframe
  UNIQUE (media_id, idx)
);

-- Everything said about the content. t0/t1 NULL = the whole media.
--
-- Re-running a producer replaces its own non-rejected rows. A rejected row
-- is never deleted by a producer: it is what stops the same mistake from
-- coming back on the next run. Rows written by a person have producer
-- 'human' and are never replaced automatically.
CREATE TABLE fact (
  fact_id    INTEGER PRIMARY KEY,
  media_id   INTEGER NOT NULL REFERENCES media (media_id) ON DELETE CASCADE,
  t0_ticks   INTEGER,
  t1_ticks   INTEGER,
  kind       TEXT NOT NULL REFERENCES fact_kind (kind),
  value      TEXT,                       -- text, or JSON for json kinds
  num        REAL,                       -- number, or confidence for text and ref kinds
  ref_id     INTEGER REFERENCES entity (entity_id),
  producer   TEXT NOT NULL,              -- 'name@version', or 'human'
  status     TEXT NOT NULL DEFAULT 'proposed'
             CHECK (status IN ('proposed', 'accepted', 'rejected')),
  basis      TEXT,                       -- why: the evidence or the check it passed
  created_ms INTEGER NOT NULL,
  CHECK ((t0_ticks IS NULL) = (t1_ticks IS NULL))
);
CREATE INDEX idx_fact_media ON fact (media_id, t0_ticks);
CREATE INDEX idx_fact_kind_value ON fact (kind, value);
CREATE INDEX idx_fact_kind_num ON fact (kind, num);
CREATE INDEX idx_fact_ref ON fact (ref_id) WHERE ref_id IS NOT NULL;
CREATE INDEX idx_fact_producer ON fact (producer, media_id);

-- Image embeddings at a frame. Keyed by model, so a better model is new rows
-- beside the old ones. float16, little-endian.
CREATE TABLE embedding (
  media_id INTEGER NOT NULL REFERENCES media (media_id) ON DELETE CASCADE,
  t_ticks  INTEGER NOT NULL,
  model    TEXT NOT NULL,
  vec      BLOB NOT NULL,
  PRIMARY KEY (media_id, t_ticks, model)
);

-- Where a media was used in an edit: the strongest evidence of what a shot
-- is good for, written by the person who chose it.
CREATE TABLE usage (
  usage_id      INTEGER PRIMARY KEY,
  media_id      INTEGER NOT NULL REFERENCES media (media_id) ON DELETE CASCADE,
  project       TEXT NOT NULL,           -- project file path
  sequence      TEXT NOT NULL,
  track         TEXT,
  src_in_ticks  INTEGER NOT NULL,        -- media time
  src_out_ticks INTEGER NOT NULL,
  rec_in_ticks  INTEGER NOT NULL,        -- sequence time
  speed         REAL NOT NULL DEFAULT 1,
  scale         REAL,                    -- percent, at the in point
  pos_x         REAL,
  pos_y         REAL,
  caption       TEXT,                    -- captions on screen while it plays
  props         TEXT,                    -- JSON: keyframes, crop, effects
  approved      INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1)),  -- the edit was published
  imported_ms   INTEGER NOT NULL
);
CREATE INDEX idx_usage_media ON usage (media_id, src_in_ticks);
CREATE INDEX idx_usage_project ON usage (project, sequence);

-- Which analysis stage has run on which media, at which version.
CREATE TABLE job (
  media_id   INTEGER NOT NULL REFERENCES media (media_id) ON DELETE CASCADE,
  stage      TEXT NOT NULL,
  version    TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('done', 'failed')),
  error      TEXT,
  updated_ms INTEGER NOT NULL,
  PRIMARY KEY (media_id, stage)
);

-- Translations from a tagger's vocabulary to the channel's words.
-- kind NULL = deliberately not mapped.
CREATE TABLE lexicon (
  src     TEXT NOT NULL,                 -- 'danbooru'
  src_tag TEXT NOT NULL,
  kind    TEXT REFERENCES fact_kind (kind),
  value   TEXT,
  origin  TEXT NOT NULL CHECK (origin IN ('curated', 'model', 'rejected')),
  PRIMARY KEY (src, src_tag)
);

-- One search document per cut: its media's whole-media facts, the facts
-- overlapping its span, entity names with aliases, and the captions it was
-- used under. Rebuilt by code when any of those change.
CREATE VIRTUAL TABLE cut_doc USING fts5 (doc, tokenize = 'unicode61 remove_diacritics 2');
