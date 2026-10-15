"""SQLite storage. One file, no server to run."""
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  trade TEXT NOT NULL DEFAULT 'Plumbing',
  owner_email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  owner_cell TEXT NOT NULL,
  text_number TEXT UNIQUE,
  timezone TEXT NOT NULL DEFAULT 'America/Detroit',
  hours_json TEXT NOT NULL,
  job_minutes INTEGER NOT NULL DEFAULT 60,
  lead_hours INTEGER NOT NULL DEFAULT 2,
  horizon_days INTEGER NOT NULL DEFAULT 7,
  services TEXT NOT NULL DEFAULT '',
  service_area TEXT NOT NULL DEFAULT '',
  review_url TEXT NOT NULL DEFAULT '',
  assistant_on INTEGER NOT NULL DEFAULT 1,
  mode TEXT NOT NULL DEFAULT 'auto',
  skip_known INTEGER NOT NULL DEFAULT 0,
  busy_until TEXT NOT NULL DEFAULT '',
  ics_token TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  phone TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  opted_out INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (business_id, phone)
);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  customer_id INTEGER NOT NULL UNIQUE REFERENCES customers(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'new',
  reason TEXT NOT NULL DEFAULT '',
  problem TEXT NOT NULL DEFAULT '',
  offered_json TEXT NOT NULL DEFAULT '[]',
  pending_json TEXT NOT NULL DEFAULT '',
  strikes INTEGER NOT NULL DEFAULT 0,
  token TEXT NOT NULL UNIQUE,
  last_texted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  call_sid TEXT UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,
  kind TEXT NOT NULL,
  from_phone TEXT NOT NULL DEFAULT '',
  to_phone TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  provider_id TEXT,
  simulated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_phone, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_inbound_sid
  ON messages(provider_id) WHERE direction = 'in' AND provider_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  problem TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'booked',
  source TEXT NOT NULL DEFAULT 'text',
  reminder_sent_at TEXT,
  review_sent_at TEXT,
  done_at TEXT,
  created_at TEXT NOT NULL
);
-- Two jobs can never start at the same moment for the same business.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_slot
  ON jobs(business_id, starts_at) WHERE status = 'booked';

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _add_missing_columns(db):
    """Databases made by an older version get the new columns without losing data."""
    wanted = {
        "businesses": [("mode", "TEXT NOT NULL DEFAULT 'auto'"), ("skip_known", "INTEGER NOT NULL DEFAULT 0"), ("busy_until", "TEXT NOT NULL DEFAULT ''")],
        "conversations": [("pending_json", "TEXT NOT NULL DEFAULT ''")],
    }
    for table, cols in wanted.items():
        have = {r["name"] for r in db.execute("PRAGMA table_info(%s)" % table)}
        for name, decl in cols:
            if name not in have:
                db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    _add_missing_columns(db)


@contextmanager
def tx(db):
    """One all-or-nothing write. BEGIN IMMEDIATE takes the write lock up front,
    so two people booking the same slot at once cannot both succeed."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
