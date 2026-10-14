-- Adds FTS5 full-text search over scene_cut.description_text and dialogue.text.
-- External-content tables (no data duplication) kept in sync via triggers.

CREATE VIRTUAL TABLE cut_search USING fts5(
  description_text,
  content='scene_cut',
  content_rowid='cut_id'
);

CREATE TRIGGER scene_cut_ai AFTER INSERT ON scene_cut BEGIN
  INSERT INTO cut_search(rowid, description_text) VALUES (new.cut_id, new.description_text);
END;
CREATE TRIGGER scene_cut_ad AFTER DELETE ON scene_cut BEGIN
  INSERT INTO cut_search(cut_search, rowid, description_text) VALUES ('delete', old.cut_id, old.description_text);
END;
CREATE TRIGGER scene_cut_au AFTER UPDATE ON scene_cut BEGIN
  INSERT INTO cut_search(cut_search, rowid, description_text) VALUES ('delete', old.cut_id, old.description_text);
  INSERT INTO cut_search(rowid, description_text) VALUES (new.cut_id, new.description_text);
END;

CREATE VIRTUAL TABLE dialogue_search USING fts5(
  text,
  content='dialogue',
  content_rowid='dialogue_id'
);

CREATE TRIGGER dialogue_ai AFTER INSERT ON dialogue BEGIN
  INSERT INTO dialogue_search(rowid, text) VALUES (new.dialogue_id, new.text);
END;
CREATE TRIGGER dialogue_ad AFTER DELETE ON dialogue BEGIN
  INSERT INTO dialogue_search(dialogue_search, rowid, text) VALUES ('delete', old.dialogue_id, old.text);
END;
CREATE TRIGGER dialogue_au AFTER UPDATE ON dialogue BEGIN
  INSERT INTO dialogue_search(dialogue_search, rowid, text) VALUES ('delete', old.dialogue_id, old.text);
  INSERT INTO dialogue_search(rowid, text) VALUES (new.dialogue_id, new.text);
END;
