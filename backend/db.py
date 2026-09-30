"""AnnaChain — storage.

SQLite by default, because it needs no install and the demo has to work on a
laptop in a hostel room. The schema is the same one PostgreSQL + TimescaleDB
gets at the finale; `records` is the hypertable candidate (device_id, ts).

Nothing is written here that has not passed all eight checks in checks.py.

`records.raw` holds the exact 84 bytes the device sent, and it is the only
source of truth. Every other column in that table is a denormalised index for
charting and querying; verify() re-derives all of them from `raw` and treats
any disagreement as tampering.
"""
import os, sqlite3, threading, time
from pathlib import Path

DB_PATH = Path(os.environ.get("ANNACHAIN_DB") or Path(__file__).with_name("annachain.db"))
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  device_id   INTEGER PRIMARY KEY,
  key_hex     TEXT NOT NULL,
  label       TEXT,
  registered  REAL NOT NULL,
  last_seen   REAL,
  last_ack    INTEGER NOT NULL DEFAULT 0,
  tip_digest  TEXT,
  anchor_next INTEGER NOT NULL DEFAULT 0,
  truck       TEXT,         -- nodes sharing a truck are compared against each other
  cal_date    REAL,         -- when this sensor was last calibrated
  cal_months  INTEGER NOT NULL DEFAULT 12,   -- EN 13486 verification interval
  cal_ref     TEXT,         -- who did it, and against what
  last_ts     INTEGER       -- time of the last accepted record, for check 5
);

-- Every key a device has had, and the first sequence number it signs. A key is
-- never overwritten: old records stay verifiable under the key that signed them.
CREATE TABLE IF NOT EXISTS device_keys (
  device_id INTEGER NOT NULL,
  from_seq  INTEGER NOT NULL,
  key_hex   TEXT NOT NULL,
  at        REAL NOT NULL,
  PRIMARY KEY (device_id, from_seq)
);

-- Who changed what about a device. A silent key swap is indistinguishable from
-- an attack, so no key changes without a row here.
CREATE TABLE IF NOT EXISTS audit (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        REAL NOT NULL,
  who       TEXT NOT NULL,
  action    TEXT NOT NULL,
  device_id INTEGER,
  detail    TEXT
);

CREATE TABLE IF NOT EXISTS users (
  username  TEXT PRIMARY KEY,
  salt      TEXT NOT NULL,
  pwhash    TEXT NOT NULL,
  role      TEXT NOT NULL DEFAULT 'operator',
  created   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token    TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  expires  REAL NOT NULL
);

-- Checkpoints along the journey. Each one becomes a GS1 EPCIS event.
CREATE TABLE IF NOT EXISTS checkpoints (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  shipment_id TEXT NOT NULL,
  device_id   INTEGER,
  ts          INTEGER NOT NULL,
  place       TEXT NOT NULL,
  biz_step    TEXT NOT NULL,
  disposition TEXT,
  party       TEXT,
  seq         INTEGER,
  note        TEXT
);

-- The chain state at a known sequence number, so a 90-day record does not have
-- to be re-walked from record 1 on every verification.
CREATE TABLE IF NOT EXISTS chain_marks (
  device_id INTEGER NOT NULL,
  seq       INTEGER NOT NULL,
  digest    TEXT NOT NULL,
  root      TEXT NOT NULL,
  at        REAL NOT NULL,
  PRIMARY KEY (device_id, seq)
);

CREATE TABLE IF NOT EXISTS shipments (
  shipment_id TEXT PRIMARY KEY,
  device_id   INTEGER NOT NULL,
  commodity   TEXT,
  origin      TEXT,
  destination TEXT,
  started     REAL,
  status      TEXT DEFAULT 'in transit',
  min_c       REAL DEFAULT 2.0,
  max_c       REAL DEFAULT 8.0
);

-- the hypertable at the finale: one row per reading, never updated
CREATE TABLE IF NOT EXISTS records (
  device_id INTEGER NOT NULL,
  seq       INTEGER NOT NULL,
  ts        INTEGER NOT NULL,
  temp_c    REAL,
  rh_pct    REAL,
  c2h4_ppb  INTEGER,
  flags     INTEGER NOT NULL,
  batt_pct  INTEGER,
  digest    TEXT NOT NULL,
  prev      TEXT NOT NULL,
  sig       TEXT NOT NULL,
  received  REAL NOT NULL,
  lag_s     INTEGER NOT NULL,   -- how late it arrived: 0 live, large after an outage
  raw       BLOB NOT NULL,      -- the 84 bytes the device sent: the evidence
  PRIMARY KEY (device_id, seq)
);
CREATE INDEX IF NOT EXISTS records_ts ON records(device_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  seq       INTEGER,
  ts        INTEGER,
  kind      TEXT NOT NULL,
  severity  TEXT NOT NULL,
  message   TEXT NOT NULL,
  created   REAL NOT NULL,
  acked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gaps (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  from_seq  INTEGER NOT NULL,
  to_seq    INTEGER NOT NULL,
  declared  REAL NOT NULL,
  mac       TEXT            -- the device's signature over the notice
);

CREATE TABLE IF NOT EXISTS rejects (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER,
  seq       INTEGER,
  reason    TEXT NOT NULL,
  at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS anchors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id   INTEGER NOT NULL,
  from_seq    INTEGER NOT NULL,
  to_seq      INTEGER NOT NULL,
  merkle_root TEXT NOT NULL,
  at          REAL NOT NULL,
  chain_txid  TEXT              -- filled when Hyperledger Fabric is wired in
);

CREATE TABLE IF NOT EXISTS ingest_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       REAL NOT NULL,
  n        INTEGER NOT NULL,
  accepted INTEGER NOT NULL
);
"""


def conn():
    # Reconnect if DB_PATH has moved (the tests point it at a fresh file).
    if getattr(_local, "path", None) != DB_PATH:
        _local.path = DB_PATH
        _local.c = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.c.row_factory = sqlite3.Row
        _local.c.execute("PRAGMA journal_mode=WAL")
        _local.c.execute("PRAGMA synchronous=NORMAL")
    return _local.c


TABLES = ("records", "alerts", "gaps", "rejects", "anchors",
          "ingest_log", "shipments", "devices", "checkpoints", "chain_marks",
          "device_keys")
# `audit` survives a reset on purpose: it is the record of who did what.
# `users` and `sessions` are deliberately not wiped by a reset: losing your
# login because you reset the demo data is a bad afternoon.


def _migrate(c):
    """Add columns that older databases will not have. Cheaper than asking
    anyone to delete their data to pick up a schema change."""
    have = {r["name"] for r in c.execute("PRAGMA table_info(devices)")}
    for col, decl in (("truck", "TEXT"),
                      ("cal_date", "REAL"),
                      ("cal_months", "INTEGER NOT NULL DEFAULT 12"),
                      ("cal_ref", "TEXT"),
                      ("last_ts", "INTEGER")):
        if col not in have:
            c.execute(f"ALTER TABLE devices ADD COLUMN {col} {decl}")

    # Older databases threw the evidence away. The column is added, but rows
    # stored before it existed have no raw bytes and cannot be verified; verify()
    # says so for each of them rather than passing them.
    have = {r["name"] for r in c.execute("PRAGMA table_info(records)")}
    if "raw" not in have:
        c.execute("ALTER TABLE records ADD COLUMN raw BLOB")
    have = {r["name"] for r in c.execute("PRAGMA table_info(gaps)")}
    if "mac" not in have:
        c.execute("ALTER TABLE gaps ADD COLUMN mac TEXT")

    # Devices enrolled before key history existed: their current key has signed
    # everything so far.
    c.execute("INSERT OR IGNORE INTO device_keys(device_id,from_seq,key_hex,at) "
              "SELECT device_id, 1, key_hex, registered FROM devices")


def init(reset: bool = False):
    """Create the schema, and optionally empty it.

    A reset empties the tables rather than deleting the file. Deleting it would
    leave every other thread's already-open connection pointing at an unlinked
    inode — they carry on writing to a file nobody can read, and the symptom is
    a dashboard that shows nothing while the API insists all is well.
    """
    c = conn()
    c.executescript(SCHEMA)
    _migrate(c)
    if reset:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
        try:
            c.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass
    c.commit()


# ── devices ──────────────────────────────────────────────────────────────
def enrol_device(device_id: int, key_hex: str, who: str, label: str = None,
                 truck: str = None):
    """A new device. Refuses to touch one that already exists: changing an
    existing device's key is rotate_key(), and only that."""
    c = conn()
    now = time.time()
    c.execute(
        "INSERT INTO devices(device_id,key_hex,label,registered,truck) VALUES(?,?,?,?,?)",
        (device_id, key_hex, label or f"node {device_id:08X}", now, truck))
    c.execute("INSERT INTO device_keys(device_id,from_seq,key_hex,at) VALUES(?,?,?,?)",
              (device_id, 1, key_hex, now))
    audit(who, "enrol", device_id, f"key sha256 {_fp(key_hex)}", commit=False)
    c.commit()


def update_device_info(device_id: int, label: str = None, truck: str = None):
    c = conn()
    c.execute("UPDATE devices SET label=COALESCE(?, label), truck=COALESCE(?, truck) "
              "WHERE device_id=?", (label, truck, device_id))
    c.commit()


def rotate_key(device_id: int, key_hex: str, who: str):
    """The new key signs from the next sequence number on. The old one is kept,
    so everything it signed still verifies."""
    c = conn()
    d = device(device_id)
    from_seq = (d["last_ack"] or 0) + 1
    c.execute("INSERT OR REPLACE INTO device_keys(device_id,from_seq,key_hex,at) "
              "VALUES(?,?,?,?)", (device_id, from_seq, key_hex, time.time()))
    c.execute("UPDATE devices SET key_hex=? WHERE device_id=?", (key_hex, device_id))
    audit(who, "rotate_key", device_id,
          f"from seq {from_seq}: {_fp(d['key_hex'])} -> {_fp(key_hex)}", commit=False)
    c.commit()
    return from_seq


def keys_for(device_id: int):
    """[(from_seq, key bytes)], oldest first."""
    return [(r["from_seq"], bytes.fromhex(r["key_hex"])) for r in conn().execute(
        "SELECT from_seq, key_hex FROM device_keys WHERE device_id=? ORDER BY from_seq",
        (device_id,))]


def _fp(key_hex: str) -> str:
    """A fingerprint for the audit log. The key itself is never written there."""
    import hashlib
    return hashlib.sha256(bytes.fromhex(key_hex)).hexdigest()[:16]


def audit(who: str, action: str, device_id: int = None, detail: str = None,
          commit: bool = True):
    c = conn()
    c.execute("INSERT INTO audit(at,who,action,device_id,detail) VALUES(?,?,?,?,?)",
              (time.time(), who, action, device_id, detail))
    if commit:
        c.commit()


def devices_on_truck(truck: str):
    return conn().execute(
        "SELECT * FROM devices WHERE truck=? ORDER BY device_id", (truck,)
    ).fetchall()


def readings_in_bucket(truck: str, t0: int, t1: int):
    """Every node on this truck that has a reading in [t0, t1).

    Records the node itself flagged as a sensor fault are left out: a sensor
    that admitted it failed is not evidence about what the other nodes see."""
    return conn().execute(
        "SELECT r.device_id, r.temp_c, r.seq, r.ts FROM records r "
        "JOIN devices d ON d.device_id = r.device_id "
        "WHERE d.truck = ? AND r.ts >= ? AND r.ts < ? AND (r.flags & 32) = 0 "
        "ORDER BY r.device_id",
        (truck, t0, t1),
    ).fetchall()


def device(device_id: int):
    return conn().execute(
        "SELECT * FROM devices WHERE device_id=?", (device_id,)
    ).fetchone()


def devices():
    return conn().execute("SELECT * FROM devices ORDER BY device_id").fetchall()


def set_tip(device_id: int, last_ack: int, tip_digest: str, anchor_next: int = 0,
            last_ts: int = None):
    c = conn()
    c.execute(
        "UPDATE devices SET last_ack=?, tip_digest=?, anchor_next=?, last_seen=?, "
        "last_ts=COALESCE(?, last_ts) WHERE device_id=?",
        (last_ack, tip_digest, anchor_next, time.time(), last_ts, device_id),
    )
    c.commit()


# ── records ──────────────────────────────────────────────────────────────
def insert_record(r: dict):
    conn().execute(
        "INSERT OR IGNORE INTO records"
        "(device_id,seq,ts,temp_c,rh_pct,c2h4_ppb,flags,batt_pct,"
        " digest,prev,sig,received,lag_s,raw) "
        "VALUES(:device,:seq,:ts,:temp_c,:rh_pct,:c2h4_ppb,:flags,:battery_pct,"
        " :digest,:prev,:sig,:received,:lag_s,:raw)",
        r,
    )


def records_for_verify(device_id: int, after_seq: int = 0):
    """Every column verify() compares, oldest first."""
    return conn().execute(
        "SELECT device_id,seq,ts,temp_c,rh_pct,c2h4_ppb,flags,batt_pct,"
        "digest,prev,sig,raw FROM records WHERE device_id=? AND seq>? ORDER BY seq",
        (device_id, after_seq)).fetchall()


def records_raw(device_id: int, offset: int, limit: int):
    return conn().execute(
        "SELECT seq, raw FROM records WHERE device_id=? ORDER BY seq LIMIT ? OFFSET ?",
        (device_id, limit, offset)).fetchall()


def commit():
    conn().commit()


def records(device_id: int, limit: int = 500, since_seq: int = 0):
    return conn().execute(
        "SELECT * FROM records WHERE device_id=? AND seq>? "
        "ORDER BY seq DESC LIMIT ?",
        (device_id, since_seq, limit),
    ).fetchall()


def records_window(device_id: int, limit: int = 1500):
    """Oldest-first, for charting. These are the index columns, not the evidence:
    whatever is drawn from them is only as good as verify() says it is."""
    rows = conn().execute(
        "SELECT seq,ts,temp_c,rh_pct,c2h4_ppb,flags,batt_pct,lag_s FROM records "
        "WHERE device_id=? ORDER BY seq DESC LIMIT ?",
        (device_id, limit),
    ).fetchall()
    return list(reversed(rows))


def record_count(device_id: int = None):
    if device_id is None:
        return conn().execute("SELECT COUNT(*) n FROM records").fetchone()["n"]
    return conn().execute(
        "SELECT COUNT(*) n FROM records WHERE device_id=?", (device_id,)
    ).fetchone()["n"]


# ── alerts, gaps, rejects, anchors ───────────────────────────────────────
def add_alert(device_id, seq, ts, kind, severity, message):
    conn().execute(
        "INSERT INTO alerts(device_id,seq,ts,kind,severity,message,created) "
        "VALUES(?,?,?,?,?,?,?)",
        (device_id, seq, ts, kind, severity, message, time.time()),
    )


def alerts(limit: int = 60, device_id: int = None, unacked_only: bool = False):
    where, args = [], []
    if device_id is not None:
        where.append("device_id=?"); args.append(device_id)
    if unacked_only:
        where.append("acked=0")
    q = "SELECT * FROM alerts"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return conn().execute(q, args).fetchall()


def ack_alert(alert_id: int):
    c = conn()
    c.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
    c.commit()


def add_gap(device_id, from_seq, to_seq, mac):
    c = conn()
    c.execute(
        "INSERT INTO gaps(device_id,from_seq,to_seq,declared,mac) VALUES(?,?,?,?,?)",
        (device_id, from_seq, to_seq, time.time(), mac),
    )
    c.commit()


def gaps(device_id: int = None):
    if device_id is None:
        return conn().execute("SELECT * FROM gaps ORDER BY id DESC").fetchall()
    return conn().execute(
        "SELECT * FROM gaps WHERE device_id=? ORDER BY id DESC", (device_id,)
    ).fetchall()


def add_reject(device_id, seq, reason):
    conn().execute(
        "INSERT INTO rejects(device_id,seq,reason,at) VALUES(?,?,?,?)",
        (device_id, seq, reason, time.time()),
    )
    conn().commit()


def rejects(limit: int = 40):
    return conn().execute(
        "SELECT * FROM rejects ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def add_anchor(device_id, from_seq, to_seq, root):
    c = conn()
    c.execute(
        "INSERT INTO anchors(device_id,from_seq,to_seq,merkle_root,at) "
        "VALUES(?,?,?,?,?)",
        (device_id, from_seq, to_seq, root, time.time()),
    )
    c.commit()


def anchors(device_id: int = None, limit: int = 20):
    if device_id is None:
        return conn().execute(
            "SELECT * FROM anchors ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return conn().execute(
        "SELECT * FROM anchors WHERE device_id=? ORDER BY id DESC LIMIT ?",
        (device_id, limit),
    ).fetchall()


def log_ingest(n, accepted):
    c = conn()
    c.execute(
        "INSERT INTO ingest_log(at,n,accepted) VALUES(?,?,?)",
        (time.time(), n, accepted),
    )
    c.commit()


def last_ingest():
    return conn().execute(
        "SELECT * FROM ingest_log ORDER BY id DESC LIMIT 1"
    ).fetchone()


# ── shipments ────────────────────────────────────────────────────────────
def upsert_shipment(shipment_id, device_id, **kw):
    c = conn()
    c.execute(
        "INSERT INTO shipments(shipment_id,device_id,commodity,origin,destination,"
        "started,min_c,max_c) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(shipment_id) DO UPDATE SET device_id=excluded.device_id",
        (
            shipment_id, device_id,
            kw.get("commodity", "table grapes"),
            kw.get("origin", "Nashik"),
            kw.get("destination", "JNPT"),
            kw.get("started", time.time()),
            kw.get("min_c", 2.0),
            kw.get("max_c", 8.0),
        ),
    )
    c.commit()


def shipment_for(device_id: int):
    return conn().execute(
        "SELECT * FROM shipments WHERE device_id=? ORDER BY started DESC LIMIT 1",
        (device_id,),
    ).fetchone()


# ── calibration (check 7) ────────────────────────────────────────────────
def set_calibration(device_id: int, cal_date: float, months: int, ref: str):
    c = conn()
    c.execute("UPDATE devices SET cal_date=?, cal_months=?, cal_ref=? "
              "WHERE device_id=?", (cal_date, months, ref, device_id))
    c.commit()


# ── users and sessions ───────────────────────────────────────────────────
def create_user(username, salt, pwhash, role="operator"):
    c = conn()
    c.execute("INSERT INTO users(username,salt,pwhash,role,created) "
              "VALUES(?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET "
              "salt=excluded.salt, pwhash=excluded.pwhash, role=excluded.role",
              (username, salt, pwhash, role, time.time()))
    c.commit()


def user(username):
    return conn().execute("SELECT * FROM users WHERE username=?",
                          (username,)).fetchone()


def user_count():
    return conn().execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def put_session(token, username, expires):
    c = conn()
    c.execute("INSERT OR REPLACE INTO sessions(token,username,expires) "
              "VALUES(?,?,?)", (token, username, expires))
    c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    c.commit()


def get_session(token):
    return conn().execute(
        "SELECT * FROM sessions WHERE token=? AND expires > ?",
        (token, time.time())).fetchone()


def drop_session(token):
    c = conn()
    c.execute("DELETE FROM sessions WHERE token=?", (token,))
    c.commit()


# ── checkpoints ──────────────────────────────────────────────────────────
def add_checkpoint(shipment_id, device_id, ts, place, biz_step,
                   disposition=None, party=None, seq=None, note=None):
    c = conn()
    cur = c.execute(
        "INSERT INTO checkpoints(shipment_id,device_id,ts,place,biz_step,"
        "disposition,party,seq,note) VALUES(?,?,?,?,?,?,?,?,?)",
        (shipment_id, device_id, ts, place, biz_step,
         disposition, party, seq, note))
    c.commit()
    return cur.lastrowid


def checkpoints(shipment_id):
    return conn().execute(
        "SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY ts",
        (shipment_id,)).fetchall()


# ── chain verification marks ─────────────────────────────────────────────
def put_mark(device_id, seq, digest, root):
    c = conn()
    c.execute("INSERT OR REPLACE INTO chain_marks(device_id,seq,digest,root,at) "
              "VALUES(?,?,?,?,?)", (device_id, seq, digest, root, time.time()))
    c.commit()


def last_mark(device_id):
    return conn().execute(
        "SELECT * FROM chain_marks WHERE device_id=? ORDER BY seq DESC LIMIT 1",
        (device_id,)).fetchone()


def clear_marks(device_id):
    c = conn()
    c.execute("DELETE FROM chain_marks WHERE device_id=?", (device_id,))
    c.commit()


# ── shipments ────────────────────────────────────────────────────────────
def shipments():
    return conn().execute(
        "SELECT s.*, d.truck, d.label FROM shipments s "
        "LEFT JOIN devices d ON d.device_id = s.device_id "
        "ORDER BY s.started DESC").fetchall()


def shipment(shipment_id):
    return conn().execute("SELECT * FROM shipments WHERE shipment_id=?",
                          (shipment_id,)).fetchone()
