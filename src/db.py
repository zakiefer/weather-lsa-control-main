import json
import logging
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(os.path.join(__file__, os.pardir)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

DB_PATH = os.getenv("DATABASE_URL") or os.path.join(DATA_DIR, "app.db")


def _connect_sqlite(path: str) -> Any:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_conn() -> Any:
    # Simple: use SQLite unless DATABASE_URL specifies Postgres (not required by default)
    dsn = os.getenv("DATABASE_URL")
    if dsn and (dsn.startswith("postgres://") or dsn.startswith("postgresql://")):
        try:
            import psycopg2  # type: ignore

            return psycopg2.connect(dsn)
        except Exception as e:
            logging.warning(
                "Postgres DSN provided but psycopg2 not available or failed to connect: %s. Falling back to SQLite.",
                e,
            )
    return _connect_sqlite(DB_PATH)


def ensure_schema() -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        # alerts
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                issued_at TEXT,
                areas TEXT,
                severity TEXT,
                hash TEXT UNIQUE,
                cap_id TEXT,
                effective_at TEXT,
                poly_hash TEXT
            )
            """
        )
        # actions
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                alert_id INTEGER,
                customer_id TEXT,
                campaign_id TEXT,
                field TEXT,
                old TEXT,
                new TEXT,
                status TEXT,
                error TEXT
            )
            """
        )
        # notifications
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                channel TEXT,
                recipient TEXT,
                message_sid TEXT,
                status TEXT
            )
            """
        )
        # locks
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS locks (
                area_id TEXT PRIMARY KEY,
                hold_until TEXT
            )
            """
        )
        # instance lock table (for SQLite or as a fallback)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS instance_lock (
                name TEXT PRIMARY KEY,
                acquired_at TEXT DEFAULT (datetime('now')),
                owner_pid INTEGER
            )
            """
        )
        # region to campaign mapping
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS region_map (
                area_id TEXT PRIMARY KEY, -- e.g., county FIPS
                campaign_id TEXT NOT NULL,
                customer_id TEXT
            )
            """
        )
        conn.commit()
        # Backfill: add CAP columns if missing and index
        try:
            cur.execute("ALTER TABLE alerts ADD COLUMN cap_id TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE alerts ADD COLUMN effective_at TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE alerts ADD COLUMN poly_hash TEXT")
        except Exception:
            pass
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_cap_effective ON alerts(cap_id, effective_at)")
        except Exception:
            pass

        # Action dedupe: suppress repeats for a time window per alert+scope+action
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS action_dedupe (
                alert_id INTEGER,
                target_scope TEXT,
                action TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (alert_id, target_scope, action)
            )
            """
        )
        # Per-area cooldown to avoid rapid flip-flops
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS area_cooldown (
                area_id TEXT PRIMARY KEY,
                last_action TEXT,
                changed_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        # Circuit breaker state
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS circuit_breaker (
                name TEXT PRIMARY KEY,
                failure_count INTEGER DEFAULT 0,
                tripped_until TEXT,
                last_error TEXT,
                last_notified TEXT
            )
            """
        )
        # audit log (append-only)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                who TEXT,
                what TEXT,
                why TEXT,
                old_value TEXT,
                new_value TEXT,
                request_id TEXT,
                customer_id TEXT,
                campaign_id TEXT,
                alert_id INTEGER,
                outcome TEXT, -- ok|error|dry_run|aborted|circuit_open
                error TEXT,
                extras TEXT
            )
            """
        )
        conn.commit()
        # App-level key/value config (for runtime overrides)
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
        except Exception:
            pass
    finally:
        # Also ensure the mutation queue schema exists so callers relying on ensure_schema() have a complete DB.
        try:
            ensure_queue_schema()
        except Exception:
            # Best-effort; queue usage will call ensure_queue_schema() again as needed.
            pass
        conn.close()


def upsert_alert(
    hash_value: str, source: str, issued_at: Optional[str], areas: Optional[str], severity: Optional[str]
) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM alerts WHERE hash = ?", (hash_value,))
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            "INSERT INTO alerts (source, issued_at, areas, severity, hash) VALUES (?, ?, ?, ?, ?)",
            (source, issued_at, areas, severity, hash_value),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_latest_alert_for_cap(cap_id: str) -> tuple[int | None, str | None]:
    """Return (id, effective_at) for the latest alert stored for this CAP id."""
    if not cap_id:
        return (None, None)
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute(
            (
                "SELECT id, effective_at FROM alerts "
                "WHERE cap_id = ? AND effective_at IS NOT NULL "
                "ORDER BY effective_at DESC LIMIT 1"
            ),
            (cap_id,),
        ).fetchone()
        if not row:
            return (None, None)
        return (int(row[0]), str(row[1]))
    finally:
        conn.close()


def upsert_alert_cap(
    cap_id: str,
    effective_at: Optional[str],
    poly_hash: Optional[str],
    source: str,
    areas: Optional[str],
    severity: Optional[str],
) -> int:
    """Insert an alert keyed by (cap_id, effective_at, poly_hash) via a computed hash; return id."""
    key = json.dumps({"cap": cap_id or "", "eff": effective_at or "", "poly": poly_hash or ""}, sort_keys=True)
    import hashlib as _hl

    h = _hl.sha256(key.encode("utf-8")).hexdigest()
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT id FROM alerts WHERE hash = ?", (h,)).fetchone()
        if row:
            return int(row[0])
        cur.execute(
            (
                "INSERT INTO alerts (source, issued_at, areas, severity, hash, cap_id, effective_at, poly_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (source, effective_at, areas, severity, h, cap_id, effective_at, poly_hash),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def record_action(
    alert_id: Optional[int],
    customer_id: str,
    campaign_id: str,
    field: str,
    old: Optional[str],
    new: Optional[str],
    status: str,
    error: Optional[str] = None,
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO actions (alert_id, customer_id, campaign_id, field, old, new, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (alert_id, customer_id, campaign_id, field, old, new, status, error),
        )
        conn.commit()
    finally:
        conn.close()


def record_notification(channel: str, recipient: str, message_sid: Optional[str], status: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO notifications (channel, recipient, message_sid, status) VALUES (?, ?, ?, ?)",
            (channel, recipient, message_sid, status),
        )
        conn.commit()
    finally:
        conn.close()


def export_audit(days: int = 7, fmt: str = "jsonl", output_path: Optional[str] = None) -> str:
    """Export last N days of alerts, actions, and notifications. Returns output file path."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso = since.astimezone(timezone.utc).isoformat()
    conn = get_conn()
    try:
        cur = conn.cursor()
        # Filter actions/notifications/audit by created_at >= since
        actions = cur.execute(
            "SELECT id, created_at, alert_id, customer_id, campaign_id, field, old, new, status, error FROM actions WHERE datetime(created_at) >= datetime(?)",
            (since_iso,),
        ).fetchall()
        notifications = cur.execute(
            "SELECT id, created_at, channel, recipient, message_sid, status FROM notifications WHERE datetime(created_at) >= datetime(?)",
            (since_iso,),
        ).fetchall()
        audits = cur.execute(
            "SELECT id, created_at, who, what, why, old_value, new_value, request_id, customer_id, campaign_id, alert_id, outcome, error, extras FROM audit_log WHERE datetime(created_at) >= datetime(?)",
            (since_iso,),
        ).fetchall()
        # Fetch only alerts referenced by actions/audits
        alert_ids: set[int] = set()
        for r in actions:
            if r[2]:
                try:
                    alert_ids.add(int(r[2]))
                except Exception:
                    pass
        for r in audits:
            if r[10]:
                try:
                    alert_ids.add(int(r[10]))
                except Exception:
                    pass
        alerts = []
        if alert_ids:
            qmarks = ",".join(["?"] * len(alert_ids))
            alerts = cur.execute(
                f"SELECT id, source, issued_at, areas, severity, hash FROM alerts WHERE id IN ({qmarks})",
                tuple(alert_ids),
            ).fetchall()
    finally:
        conn.close()

    date_tag = datetime.now().strftime("%Y%m%d")
    if not output_path:
        ext = "jsonl" if fmt == "jsonl" else "csv"
        output_path = os.path.join(LOG_DIR, f"audit-{date_tag}-last{days}.{ext}")

    if fmt == "jsonl":
        with open(output_path, "w", encoding="utf-8") as f:
            for r in alerts:
                f.write(
                    json.dumps(
                        {
                            "type": "alert",
                            "id": r[0],
                            "source": r[1],
                            "issued_at": r[2],
                            "areas": r[3],
                            "severity": r[4],
                            "hash": r[5],
                        }
                    )
                    + "\n"
                )
            for r in actions:
                f.write(
                    json.dumps(
                        {
                            "type": "action",
                            "id": r[0],
                            "created_at": r[1],
                            "alert_id": r[2],
                            "customer_id": r[3],
                            "campaign_id": r[4],
                            "field": r[5],
                            "old": r[6],
                            "new": r[7],
                            "status": r[8],
                            "error": r[9],
                        }
                    )
                    + "\n"
                )
            for r in notifications:
                f.write(
                    json.dumps(
                        {
                            "type": "notification",
                            "id": r[0],
                            "created_at": r[1],
                            "channel": r[2],
                            "recipient": r[3],
                            "message_sid": r[4],
                            "status": r[5],
                        }
                    )
                    + "\n"
                )
            for r in audits:
                f.write(
                    json.dumps(
                        {
                            "type": "audit",
                            "id": r[0],
                            "created_at": r[1],
                            "who": r[2],
                            "what": r[3],
                            "why": r[4],
                            "old_value": r[5],
                            "new_value": r[6],
                            "request_id": r[7],
                            "customer_id": r[8],
                            "campaign_id": r[9],
                            "alert_id": r[10],
                            "outcome": r[11],
                            "error": r[12],
                            "extras": json.loads(r[13]) if r[13] else None,
                        }
                    )
                    + "\n"
                )
    else:
        import csv

        base = os.path.splitext(output_path)[0]
        alerts_csv = base + "-alerts.csv"
        actions_csv = base + "-actions.csv"
        notifications_csv = base + "-notifications.csv"
        audits_csv = base + "-audits.csv"
        with open(alerts_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "source", "issued_at", "areas", "severity", "hash"])
            for r in alerts:
                w.writerow([r[0], r[1], r[2], r[3], r[4], r[5]])
        with open(actions_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                ["id", "created_at", "alert_id", "customer_id", "campaign_id", "field", "old", "new", "status", "error"]
            )
            for r in actions:
                w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]])
        with open(notifications_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "created_at", "channel", "recipient", "message_sid", "status"])
            for r in notifications:
                w.writerow([r[0], r[1], r[2], r[3], r[4], r[5]])
        with open(audits_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "id",
                    "created_at",
                    "who",
                    "what",
                    "why",
                    "old_value",
                    "new_value",
                    "request_id",
                    "customer_id",
                    "campaign_id",
                    "alert_id",
                    "outcome",
                    "error",
                    "extras_json",
                ]
            )
            for r in audits:
                w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13]])
    logging.info("Audit exported to %s", output_path)
    return output_path


def _audit_jsonl_path(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now()
    date_tag = d.strftime("%Y%m%d")
    return os.path.join(LOG_DIR, f"audit-{date_tag}.jsonl")


def record_audit_log(
    who: str,
    what: str,
    why: str | None = None,
    *,
    old_value: str | None = None,
    new_value: str | None = None,
    request_id: str | None = None,
    customer_id: str | None = None,
    campaign_id: str | None = None,
    alert_id: int | None = None,
    outcome: str | None = None,
    error: str | None = None,
    extras: dict | None = None,
) -> None:
    """Append to audit_log table and to a daily JSONL file.

    This is append-only. Extras is stored as JSON.
    """
    ensure_schema()
    payload = {
        "who": who,
        "what": what,
        "why": why or "",
        "old_value": old_value,
        "new_value": new_value,
        "request_id": request_id or "",
        "customer_id": customer_id or "",
        "campaign_id": campaign_id or "",
        "alert_id": alert_id,
        "outcome": outcome or "",
        "error": error or "",
        "extras": extras or {},
    }
    # DB insert
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO audit_log (who, what, why, old_value, new_value, request_id, customer_id, campaign_id, alert_id, outcome, error, extras) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["who"],
                payload["what"],
                payload["why"],
                payload["old_value"],
                payload["new_value"],
                payload["request_id"],
                payload["customer_id"],
                payload["campaign_id"],
                payload["alert_id"],
                payload["outcome"],
                payload["error"],
                json.dumps(payload["extras"]) if payload["extras"] else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    # JSONL append (best-effort)
    try:
        event = dict(payload)
        event["time"] = datetime.now(timezone.utc).isoformat()
        with open(_audit_jsonl_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def upsert_region_mapping(area_id: str, campaign_id: str, customer_id: Optional[str] = None) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO region_map (area_id, campaign_id, customer_id) VALUES (?, ?, ?) ON CONFLICT(area_id) DO UPDATE SET campaign_id=excluded.campaign_id, customer_id=excluded.customer_id",
            (area_id, campaign_id, customer_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_region_mapping(area_id: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM region_map WHERE area_id = ?", (area_id,))
        conn.commit()
    finally:
        conn.close()


def list_region_mappings() -> list:
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute("SELECT area_id, campaign_id, customer_id FROM region_map ORDER BY area_id").fetchall()
        result = []
        for r in rows:
            result.append({"area_id": r[0], "campaign_id": r[1], "customer_id": r[2]})
        return result
    finally:
        conn.close()


def get_campaigns_for_areas(area_ids: Iterable[str]) -> list:
    ids = list(set(area_ids))
    if not ids:
        return []
    qmarks = ",".join(["?"] * len(ids))
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            f"SELECT DISTINCT campaign_id, COALESCE(customer_id, '') FROM region_map WHERE area_id IN ({qmarks})", ids
        ).fetchall()
        result = []
        for r in rows:
            result.append((r[0], r[1] or None))
        return result
    finally:
        conn.close()


def is_duplicate_action(alert_id: int | None, target_scope: str, action: str, window_minutes: int) -> bool:
    if not alert_id:
        return False
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM action_dedupe WHERE alert_id = ? AND target_scope = ? AND action = ? AND datetime(created_at) >= datetime('now', ?) LIMIT 1",
            (alert_id, target_scope, action, f"-{int(window_minutes)} minutes"),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def record_action_dedupe(alert_id: int | None, target_scope: str, action: str) -> None:
    if not alert_id:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO action_dedupe (alert_id, target_scope, action, created_at) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(alert_id, target_scope, action) DO UPDATE SET created_at = excluded.created_at",
            (alert_id, target_scope, action),
        )
        conn.commit()
    finally:
        conn.close()


def any_area_blocks_pause(area_ids: Iterable[str], cooldown_minutes: int) -> tuple[bool, str | None]:
    ids = list(set(area_ids))
    if not ids:
        return (False, None)
    conn = get_conn()
    try:
        cur = conn.cursor()
        for aid in ids:
            row = cur.execute(
                "SELECT last_action, changed_at FROM area_cooldown WHERE area_id = ?",
                (aid,),
            ).fetchone()
            if not row:
                continue
            last_action, changed_at = row[0], row[1]
            # If last action was ENABLED and we're within cooldown, block pausing
            if last_action == "ENABLED":
                # Compare timestamps using SQLite
                chk = cur.execute(
                    "SELECT 1 WHERE datetime(?) >= datetime('now', ?)",
                    (changed_at, f"-{int(cooldown_minutes)} minutes"),
                ).fetchone()
                # chk is None if changed_at is older than the window (OK to pause)
                if chk:  # within cooldown window
                    return (True, aid)
        return (False, None)
    finally:
        conn.close()


def update_area_cooldown(area_ids: Iterable[str], action: str) -> None:
    ids = list(set(area_ids))
    if not ids:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        for aid in ids:
            cur.execute(
                "INSERT INTO area_cooldown (area_id, last_action, changed_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(area_id) DO UPDATE SET last_action = excluded.last_action, changed_at = excluded.changed_at",
                (aid, action),
            )
        conn.commit()
    finally:
        conn.close()


# --- Instance lock (single worker) ---
def _is_postgres_conn(conn) -> bool:
    return conn.__class__.__module__.startswith("psycopg2")


def acquire_instance_lock(name: str):
    """Try to acquire a cross-process lock. Returns {ok, token|None}. Keep token to release.

    Postgres: uses pg_try_advisory_lock(hashtext(name))
    SQLite/other: row in instance_lock
    """
    conn = get_conn()
    try:
        if _is_postgres_conn(conn):
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,))
            r = cur.fetchone()
            ok = bool(r and r[0])
            if ok:
                return {"ok": True, "token": {"engine": "pg", "name": name, "conn": conn}}
            conn.close()
            return {"ok": False, "token": None}
        # SQLite fallback
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO instance_lock (name, owner_pid) VALUES (?, ?)", (name, os.getpid()))
            conn.commit()
            return {"ok": True, "token": {"engine": "table", "name": name, "conn": conn}}
        except Exception:
            conn.close()
            return {"ok": False, "token": None}
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return {"ok": False, "token": None}


def release_instance_lock(token) -> None:
    if not token:
        return
    name = token.get("name")
    conn = token.get("conn")
    engine = token.get("engine")
    try:
        if engine == "pg":
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
            conn.close()
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM instance_lock WHERE name = ?", (name,))
            conn.commit()
            conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


# --- Mutation queue ---
def ensure_queue_schema() -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mutation_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                alert_id INTEGER,
                customer_id TEXT,
                campaign_id TEXT,
                action TEXT,
                new_status TEXT,
                status TEXT DEFAULT 'queued', -- queued|running|done|error
                attempt_count INTEGER DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                last_error TEXT,
                not_before TEXT DEFAULT (datetime('now'))
            )
            """
        )
        # Try to add not_before if missing (SQLite tolerant: will error once then ignore)
        try:
            cur.execute("ALTER TABLE mutation_queue ADD COLUMN not_before TEXT DEFAULT (datetime('now'))")
        except Exception:
            pass
        # Backfill any existing rows that may have NULL not_before so they are eligible immediately
        try:
            cur.execute("UPDATE mutation_queue SET not_before = datetime('now') WHERE not_before IS NULL")
        except Exception:
            pass
        conn.commit()
    finally:
        conn.close()


def enqueue_mutation(alert_id, customer_id, campaign_id, action, new_status) -> int:
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mutation_queue (alert_id, customer_id, campaign_id, action, new_status, status, not_before) VALUES (?, ?, ?, ?, ?, 'queued', datetime('now'))",
            (alert_id, customer_id, campaign_id, action, new_status),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def fetch_next_mutation():
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT id, alert_id, customer_id, campaign_id, action, new_status, attempt_count "
            "FROM mutation_queue "
            "WHERE status = 'queued' AND (not_before IS NULL OR datetime(not_before) <= datetime('now')) "
            "ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "alert_id": row[1],
            "customer_id": row[2],
            "campaign_id": row[3],
            "action": row[4],
            "new_status": row[5],
            "attempt_count": row[6],
        }
    finally:
        conn.close()


def mark_mutation_started(mid: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE mutation_queue SET status='running', attempt_count = attempt_count + 1, started_at = datetime('now') WHERE id = ?",
            (mid,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_mutation_done(mid: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE mutation_queue SET status='done', finished_at = datetime('now'), last_error = NULL WHERE id = ?",
            (mid,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_mutation_error(mid: int, error: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE mutation_queue SET status='error', finished_at = datetime('now'), last_error = ? WHERE id = ?",
            (error, mid),
        )
        conn.commit()
    finally:
        conn.close()


def get_queue_length() -> int:
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT COUNT(*) FROM mutation_queue WHERE status = 'queued'").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def requeue_mutation(mid: int, delay_seconds: int, error: str | None = None) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE mutation_queue SET status='queued', not_before = datetime('now', ?), last_error = COALESCE(?, last_error) WHERE id = ?",
            (f"+{int(delay_seconds)} seconds", error, mid),
        )
        conn.commit()
    finally:
        conn.close()


def get_queue_stats() -> dict:
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        counts = {}
        for st in ("queued", "running", "done", "error"):
            row = cur.execute("SELECT COUNT(*) FROM mutation_queue WHERE status = ?", (st,)).fetchone()
            counts[st] = int(row[0] if row else 0)
        return counts
    finally:
        conn.close()


def list_queued_mutations(limit: int = 10) -> list[dict]:
    """Return the oldest queued mutations up to limit.

    Each row includes: id, created_at, customer_id, campaign_id, action, new_status, attempt_count, last_error.
    """
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            (
                "SELECT id, created_at, customer_id, campaign_id, action, new_status, attempt_count, last_error "
                "FROM mutation_queue WHERE status = 'queued' "
                "ORDER BY created_at, id LIMIT ?"
            ),
            (int(limit),),
        ).fetchall()
        return [
            {
                "id": int(r[0]),
                "created_at": r[1],
                "customer_id": r[2],
                "campaign_id": r[3],
                "action": r[4],
                "new_status": r[5],
                "attempt_count": int(r[6] or 0),
                "last_error": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_recent_errors(limit: int = 10) -> list[dict]:
    """Return up to limit recent errors from audit_log and mutation_queue.

    Rows are dicts with keys: source (audit|queue), created_at, message, context.
    """
    ensure_schema()
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        audit_rows = cur.execute(
            (
                "SELECT created_at, what, outcome, error "
                "FROM audit_log WHERE error IS NOT NULL AND error <> '' "
                "ORDER BY id DESC LIMIT ?"
            ),
            (int(limit),),
        ).fetchall()
        queue_rows = cur.execute(
            (
                "SELECT created_at, action, new_status, last_error "
                "FROM mutation_queue WHERE status = 'error' AND last_error IS NOT NULL AND last_error <> '' "
                "ORDER BY id DESC LIMIT ?"
            ),
            (int(limit),),
        ).fetchall()
        # Convert and merge
        items: list[dict] = []
        for r in audit_rows:
            items.append(
                {
                    "source": "audit",
                    "created_at": r[0],
                    "message": r[3],
                    "context": {"what": r[1], "outcome": r[2]},
                }
            )
        for r in queue_rows:
            items.append(
                {
                    "source": "queue",
                    "created_at": r[0],
                    "message": r[3],
                    "context": {"action": r[1], "new_status": r[2]},
                }
            )
        # Sort by created_at desc (string datetime format is ISO-like, safe for ordering)
        items.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
        return items[: int(limit)]
    finally:
        conn.close()


def list_area_cooldowns(limit: int = 10) -> list[dict]:
    """Return most recently changed area cooldown entries up to limit."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            (
                "SELECT area_id, last_action, changed_at FROM area_cooldown "
                "ORDER BY datetime(changed_at) DESC, area_id LIMIT ?"
            ),
            (int(limit),),
        ).fetchall()
        return [{"area_id": r[0], "last_action": r[1], "changed_at": r[2]} for r in rows]
    finally:
        conn.close()


def summarize_error_codes(days: int = 7, limit: int = 5) -> list[dict]:
    """Return top error codes/messages over the last N days.

    Each item: {key: str, count: int}
    key is either an HTTP status code like '500' or a token like 'DEVELOPER_TOKEN_NOT_APPROVED' or 'other'.
    """
    ensure_schema()
    ensure_queue_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        # Collect recent error texts from audit and queue
        audit_errs = cur.execute(
            (
                "SELECT error FROM audit_log WHERE error IS NOT NULL AND error <> '' "
                "AND datetime(created_at) >= datetime('now', ?)"
            ),
            (f"-{int(days)} days",),
        ).fetchall()
        queue_errs = cur.execute(
            (
                "SELECT last_error FROM mutation_queue WHERE status='error' AND last_error IS NOT NULL AND last_error <> '' "
                "AND datetime(COALESCE(finished_at, created_at)) >= datetime('now', ?)"
            ),
            (f"-{int(days)} days",),
        ).fetchall()

        def _classify(msg: str) -> str:
            m = None
            try:
                import re

                # Prefer explicit Ads token error token if present
                if "DEVELOPER_TOKEN_NOT_APPROVED" in msg:
                    return "DEVELOPER_TOKEN_NOT_APPROVED"
                # HTTP status code (4xx/5xx) first hit
                m = re.search(r"\b([45]\\d{2})\b", msg)
                if m:
                    return m.group(1)
                # Common auth marker
                if "UNAUTHENTICATED" in msg or "unauthorized" in msg.lower():
                    return "UNAUTHENTICATED"
            except Exception:
                pass
            return "other"

        counts: dict[str, int] = {}
        for row in audit_errs or []:
            key = _classify(str(row[0]))
            counts[key] = counts.get(key, 0) + 1
        for row in queue_errs or []:
            key = _classify(str(row[0]))
            counts[key] = counts.get(key, 0) + 1
        items = [{"key": k, "count": v} for k, v in counts.items()]
        items.sort(key=lambda x: x.get("count", 0), reverse=True)
        return items[: int(limit)]
    finally:
        conn.close()


def count_mutations_done_today() -> int:
    """Return the number of mutations marked done today (UTC)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT COUNT(*) FROM mutation_queue WHERE status = 'done' AND date(finished_at) = date('now')"
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def list_latest_caps(limit: int = 50) -> list[dict]:
    """Return latest alert row per CAP id, ordered by effective_at desc.

    Rows include: id, cap_id, effective_at, areas, severity.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT a1.id, a1.cap_id, a1.effective_at, a1.areas, a1.severity
            FROM alerts a1
            JOIN (
                SELECT cap_id, MAX(effective_at) AS max_eff
                FROM alerts
                WHERE cap_id IS NOT NULL AND effective_at IS NOT NULL
                GROUP BY cap_id
            ) latest
            ON latest.cap_id = a1.cap_id AND latest.max_eff = a1.effective_at
            ORDER BY a1.effective_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [
            {
                "id": int(r[0]),
                "cap_id": r[1],
                "effective_at": r[2],
                "areas": r[3],
                "severity": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def is_daily_mutation_limit_reached(limit: int) -> bool:
    """Return True if limit > 0 and today's done count >= limit."""
    try:
        lim = int(limit)
    except Exception:
        lim = 0
    if lim <= 0:
        return False
    return count_mutations_done_today() >= lim


def is_breaker_open(name: str) -> tuple[bool, str | None]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT tripped_until FROM circuit_breaker WHERE name = ?", (name,)).fetchone()
        if not row:
            return (False, None)
        until = row[0]
        if not until:
            return (False, None)
        chk = cur.execute("SELECT 1 WHERE datetime(?) > datetime('now')", (until,)).fetchone()
        return (bool(chk), until)
    finally:
        conn.close()


def record_breaker_result(name: str, ok: bool, threshold: int, cooldown_minutes: int, error: str | None = None) -> dict:
    """Update circuit breaker with a success/failure and return state.

    Returns: {open: bool, tripped_now: bool, failure_count: int, until: str|None}
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT failure_count, tripped_until FROM circuit_breaker WHERE name = ?",
            (name,),
        ).fetchone()
        failure_count = 0
        tripped_until = None
        if row:
            failure_count = int(row[0] or 0)
            tripped_until = row[1]
        # Check if currently open
        open_now = False
        if tripped_until:
            chk = cur.execute("SELECT 1 WHERE datetime(?) > datetime('now')", (tripped_until,)).fetchone()
            open_now = bool(chk)
        if ok:
            failure_count = 0
            tripped_until = None
            cur.execute(
                "INSERT INTO circuit_breaker (name, failure_count, tripped_until, last_error) VALUES (?, 0, NULL, NULL) "
                "ON CONFLICT(name) DO UPDATE SET failure_count=excluded.failure_count, tripped_until=excluded.tripped_until, last_error=excluded.last_error",
                (name,),
            )
            conn.commit()
            return {"open": False, "tripped_now": False, "failure_count": 0, "until": None}
        # Failure path
        failure_count += 1
        if not open_now and failure_count >= max(1, int(threshold)):
            # Trip
            cur.execute(
                "INSERT INTO circuit_breaker (name, failure_count, tripped_until, last_error, last_notified) VALUES (?, ?, datetime('now', ?), ?, NULL) "
                "ON CONFLICT(name) DO UPDATE SET failure_count=excluded.failure_count, tripped_until=excluded.tripped_until, last_error=excluded.last_error, last_notified=excluded.last_notified",
                (name, failure_count, f"+{int(cooldown_minutes)} minutes", error or ""),
            )
            conn.commit()
            return {"open": True, "tripped_now": True, "failure_count": failure_count, "until": None}
        # Increment but not tripped
        cur.execute(
            "INSERT INTO circuit_breaker (name, failure_count, tripped_until, last_error) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET failure_count=excluded.failure_count, last_error=excluded.last_error",
            (name, failure_count, tripped_until, error or ""),
        )
        conn.commit()
        return {"open": open_now, "tripped_now": False, "failure_count": failure_count, "until": tripped_until}
    finally:
        conn.close()


def mark_breaker_notified(name: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE circuit_breaker SET last_notified = datetime('now') WHERE name = ?",
            (name,),
        )
        conn.commit()
    finally:
        conn.close()


def should_notify_breaker(name: str, cooldown_minutes: int) -> bool:
    """Return True if we should send a breaker notification now.

    Sends if never notified or last_notified older than cooldown window.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT last_notified FROM circuit_breaker WHERE name = ?",
            (name,),
        ).fetchone()
        if not row or not row[0]:
            return True
        last = row[0]
        chk = cur.execute(
            "SELECT 1 WHERE datetime(?) <= datetime('now', ?)",
            (last, f"-{int(cooldown_minutes)} minutes"),
        ).fetchone()
        return bool(chk)
    finally:
        conn.close()


# --- App config (key/value) ---
def get_config_value(key: str) -> str | None:
    try:
        ensure_schema()
        conn = get_conn()
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
            return str(row[0]) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception:
        return None


def set_config_value(key: str, value: str) -> None:
    try:
        ensure_schema()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def delete_config_value(key: str) -> None:
    try:
        ensure_schema()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM app_config WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
