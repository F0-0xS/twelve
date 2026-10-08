"""
Twelve – Database layer (SQLite)
"""
from __future__ import annotations
import sqlite3
import uuid
import os
from datetime import datetime, timedelta, date

DB_PATH = os.environ.get(
    "TWELVE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "twelve.db"),
)

SLOT_HOURS = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23]  # 12 créneaux toutes les 2h

MISSED_OPTIONS = [
    ("Dormait", "😴"),
    ("En réunion", "🤫"),
    ("Oubli", "💀"),
    ("Moment privé", "👀"),
    ("Plus de batterie", "🔋"),
    ("En trajet", "🚗"),
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS challenges (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            challenge_date TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS participants (
            id TEXT PRIMARY KEY,
            challenge_id TEXT NOT NULL,
            name TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            FOREIGN KEY (challenge_id) REFERENCES challenges(id)
        );

        CREATE TABLE IF NOT EXISTS contributions (
            id TEXT PRIMARY KEY,
            challenge_id TEXT NOT NULL,
            participant_id TEXT NOT NULL,
            slot_index INTEGER NOT NULL CHECK(slot_index BETWEEN 0 AND 11),
            type TEXT NOT NULL CHECK(type IN ('photo','missed')),
            photo_url TEXT,
            missed_reason TEXT,
            missed_emoji TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(challenge_id, participant_id, slot_index),
            FOREIGN KEY (challenge_id) REFERENCES challenges(id),
            FOREIGN KEY (participant_id) REFERENCES participants(id)
        );

        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            challenge_id TEXT NOT NULL,
            participant_id TEXT,
            endpoint TEXT NOT NULL,
            subscription_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(challenge_id, endpoint),
            FOREIGN KEY (challenge_id) REFERENCES challenges(id)
        );
        """)


# ─── Challenges ──────────────────────────────────────────────────────────────

def create_challenge(name: str, challenge_date: str | None = None, utc_offset_minutes: int = 0) -> dict:
    cid = uuid.uuid4().hex[:8]
    now = datetime.utcnow().isoformat()
    # Prefer the client-supplied local date; fall back to server date
    if not challenge_date:
        challenge_date = date.today().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO challenges (id, name, created_at, challenge_date, utc_offset_minutes) VALUES (?,?,?,?,?)",
            (cid, name, now, challenge_date, utc_offset_minutes),
        )
    return get_challenge(cid)


def get_challenge(challenge_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM challenges WHERE id=?", (challenge_id,)
        ).fetchone()
        return dict(row) if row else None


# ─── Participants ─────────────────────────────────────────────────────────────

def get_participants(challenge_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM participants WHERE challenge_id=? ORDER BY joined_at",
            (challenge_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_participant(participant_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM participants WHERE id=?", (participant_id,)
        ).fetchone()
        return dict(row) if row else None


def update_participant_name(participant_id: str, new_name: str) -> bool:
    new_name = new_name.strip()[:30]
    if not new_name:
        return False
    with get_db() as conn:
        conn.execute(
            "UPDATE participants SET name=? WHERE id=?",
            (new_name, participant_id),
        )
    return True


def join_challenge(challenge_id: str, name: str, utc_offset_minutes: int = 0) -> dict | None:
    """Returns participant dict, or None if challenge is full."""
    participants = get_participants(challenge_id)
    if len(participants) >= 6:
        return None
    pid = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO participants (id, challenge_id, name, joined_at, utc_offset_minutes) VALUES (?,?,?,?,?)",
            (pid, challenge_id, name, now, utc_offset_minutes),
        )
    return {"id": pid, "challenge_id": challenge_id, "name": name, "joined_at": now, "utc_offset_minutes": utc_offset_minutes}


# ─── Contributions ────────────────────────────────────────────────────────────

def get_contributions(challenge_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM contributions WHERE challenge_id=? ORDER BY slot_index, created_at",
            (challenge_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_contribution(challenge_id: str, participant_id: str, slot_index: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM contributions WHERE challenge_id=? AND participant_id=? AND slot_index=?",
            (challenge_id, participant_id, slot_index),
        ).fetchone()
        return dict(row) if row else None


def add_contribution(
    challenge_id: str,
    participant_id: str,
    slot_index: int,
    type: str,
    photo_url: str | None = None,
    missed_reason: str | None = None,
    missed_emoji: str | None = None,
) -> dict:
    cid = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO contributions
               (id, challenge_id, participant_id, slot_index, type,
                photo_url, missed_reason, missed_emoji, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (cid, challenge_id, participant_id, slot_index, type,
             photo_url, missed_reason, missed_emoji, now),
        )
    return {
        "id": cid, "challenge_id": challenge_id, "participant_id": participant_id,
        "slot_index": slot_index, "type": type, "photo_url": photo_url,
        "missed_reason": missed_reason, "missed_emoji": missed_emoji, "created_at": now,
    }


# ─── Push subscriptions ──────────────────────────────────────────────────────

def save_push_subscription(challenge_id: str, participant_id: str | None, subscription_json: str) -> str:
    import json as _json
    sub = _json.loads(subscription_json)
    endpoint = sub.get("endpoint", "")
    sub_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO push_subscriptions
               (id, challenge_id, participant_id, endpoint, subscription_json, created_at)
               VALUES (?,?,?,?,?,?)""",
            (sub_id, challenge_id, participant_id, endpoint, subscription_json, now),
        )
    return sub_id


def get_push_subscriptions(challenge_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM push_subscriptions WHERE challenge_id=?",
            (challenge_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_active_challenges(from_date: str = None) -> list[dict]:
    """Challenges on or after from_date — used by the hourly cron job.
    Defaults to today; pass yesterday to catch recap notifications that fire after UTC midnight."""
    if from_date is None:
        from_date = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM challenges WHERE challenge_date >= ?", (from_date,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_push_subscription(endpoint: str):
    with get_db() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))


# ─── Slot helpers ─────────────────────────────────────────────────────────────

def slot_label(slot_index: int) -> str:
    h = SLOT_HOURS[slot_index]
    return f"{h:02d}h00"


def slot_datetime(challenge_date: str, slot_index: int) -> datetime:
    d = datetime.strptime(challenge_date, "%Y-%m-%d")
    return d.replace(hour=SLOT_HOURS[slot_index], minute=0, second=0, microsecond=0)


def get_slot_status(slot_index: int, challenge_date: str) -> str:
    """Returns: upcoming | open | past"""
    slot_dt = slot_datetime(challenge_date, slot_index)
    now = datetime.now()
    if now < slot_dt:
        return "upcoming"
    if now < slot_dt + timedelta(minutes=60):
        return "open"
    return "past"


def has_notified(challenge_id: str, event_key: str) -> bool:
    """Return True if a push notification was already sent for this (challenge, event_key)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM notification_log WHERE challenge_id=? AND event_key=?",
            (challenge_id, event_key),
        ).fetchone()
        return row is not None


def mark_notified(challenge_id: str, event_key: str):
    """Record that a push notification was sent for this (challenge, event_key)."""
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notification_log (challenge_id, event_key, sent_at) VALUES (?,?,?)",
            (challenge_id, event_key, now),
        )


def migrate_db():
    """Run all DB migrations."""
    with get_db() as conn:
        # Add utc_offset_minutes to challenges if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(challenges)").fetchall()]
        if 'utc_offset_minutes' not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN utc_offset_minutes INTEGER DEFAULT 0")

        # Add utc_offset_minutes to participants if missing
        pcols = [r[1] for r in conn.execute("PRAGMA table_info(participants)").fetchall()]
        if 'utc_offset_minutes' not in pcols:
            conn.execute("ALTER TABLE participants ADD COLUMN utc_offset_minutes INTEGER DEFAULT 0")

        # notification_log: idempotence table for push notifications
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                challenge_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(challenge_id, event_key)
            )
        """)

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='contributions'"
        ).fetchone()
        # Recreate table if the constraint doesn't match what we want
        if row and 'BETWEEN 0 AND 11' not in row['sql']:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS contributions_v2 (
                id TEXT PRIMARY KEY,
                challenge_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                slot_index INTEGER NOT NULL CHECK(slot_index BETWEEN 0 AND 11),
                type TEXT NOT NULL CHECK(type IN ('photo','missed')),
                photo_url TEXT,
                missed_reason TEXT,
                missed_emoji TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(challenge_id, participant_id, slot_index),
                FOREIGN KEY (challenge_id) REFERENCES challenges(id),
                FOREIGN KEY (participant_id) REFERENCES participants(id)
            );
            INSERT OR IGNORE INTO contributions_v2 SELECT * FROM contributions WHERE slot_index BETWEEN 0 AND 11;
            DROP TABLE contributions;
            ALTER TABLE contributions_v2 RENAME TO contributions;
            """)


def leave_challenge(challenge_id: str, participant_id: str) -> bool:
    """Remove a participant and all their contributions from a challenge."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM participants WHERE id=? AND challenge_id=?",
            (participant_id, challenge_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM contributions WHERE challenge_id=? AND participant_id=?",
            (challenge_id, participant_id),
        )
        conn.execute(
            "DELETE FROM participants WHERE id=? AND challenge_id=?",
            (participant_id, challenge_id),
        )
    return True


def build_timeline(challenge: dict, participants: list[dict], contributions: list[dict]) -> list[dict]:
    """Returns a list of slot dicts enriched with contributions and statuses."""
    n = len(SLOT_HOURS)
    contribs_by_slot: dict[int, list] = {i: [] for i in range(n)}
    for c in contributions:
        idx = c["slot_index"]
        if idx in contribs_by_slot:
            contribs_by_slot[idx].append(c)

    participant_map = {p["id"]: p for p in participants}

    slots = []
    for i in range(n):
        status = get_slot_status(i, challenge["challenge_date"])
        slot_contribs = contribs_by_slot[i]

        # Figure out who contributed and who hasn't
        contributed_ids = {c["participant_id"] for c in slot_contribs}
        missing = [p for p in participants if p["id"] not in contributed_ids]

        slots.append({
            "index": i,
            "hour": SLOT_HOURS[i],
            "label": slot_label(i),
            "status": status,
            "contributions": slot_contribs,
            "missing_participants": missing,
            "participant_map": participant_map,
        })
    return slots
