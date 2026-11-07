-- schema version 1, see PLAN.md section 5

CREATE TABLE collections (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,             -- spotify_playlist | spotify_liked | spotify_album | singles | csv
  origin TEXT NOT NULL,           -- account | url | file | builtin
  external_id TEXT,               -- spotify playlist or album id, user id for liked, null for csv and singles
  source_ref TEXT,                -- pasted url or csv file path
  name TEXT,
  owner_id TEXT,
  is_readable INTEGER NOT NULL DEFAULT 1,
  snapshot_id TEXT,               -- spotify snapshot_id or sha256 of the csv, set only when content was read
  import_map_json TEXT,           -- csv column mapping, reused on re-import
  image_url TEXT,
  track_count INTEGER,            -- count reported by the source
  selected INTEGER NOT NULL DEFAULT 0,
  last_synced_at TEXT,
  UNIQUE (kind, external_id)
);

-- there is only one builtin singles collection
CREATE UNIQUE INDEX collections_one_singles ON collections (kind) WHERE kind = 'singles';

CREATE TABLE tracks (
  id INTEGER PRIMARY KEY,
  spotify_id TEXT UNIQUE,
  isrc TEXT,
  title TEXT NOT NULL,
  artists_json TEXT,
  album TEXT,
  album_artists_json TEXT,
  release_date TEXT,
  duration_ms INTEGER,
  track_number INTEGER,
  disc_number INTEGER,
  cover_url TEXT,
  explicit INTEGER,
  match_key TEXT
);

CREATE INDEX tracks_isrc ON tracks (isrc);
CREATE INDEX tracks_match_key ON tracks (match_key);

CREATE TABLE collection_tracks (
  collection_id INTEGER NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks (id),
  position INTEGER NOT NULL,
  added_at TEXT,
  PRIMARY KEY (collection_id, position)
);

CREATE INDEX collection_tracks_track ON collection_tracks (track_id);

CREATE TABLE downloads (
  track_id INTEGER PRIMARY KEY REFERENCES tracks (id),
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | searching | downloading | done | not_found | failed
  stage TEXT NOT NULL DEFAULT 'flac',       -- flac | fallback
  file_path TEXT,
  format TEXT,
  sample_rate INTEGER,
  bit_depth INTEGER,
  duration_ms INTEGER,
  length_verified INTEGER,
  source_user TEXT,
  source_path TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT,
  finished_at TEXT,
  stats_json TEXT
);
