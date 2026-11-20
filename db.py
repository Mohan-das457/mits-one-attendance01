"""Database access layer for MITS ONE Admin Login Activity Monitoring.
Integrates with Neon PostgreSQL using psycopg2.

Key guarantees:
- All database queries use parameterized SQL.
- Database connection errors are caught gracefully and will NEVER break
  or block normal student attendance login.
- Timestamps stored in UTC with timezone; converted to IST (UTC+5:30) for display.
- Sensitive student data (GEMS passwords, auth tokens, attendance data) is NEVER stored.
"""
import contextlib
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("mits_one.db")

# Indian Standard Time (IST) timezone (+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    PSYCOPG2_AVAILABLE = False
    logger.warning("psycopg2 is not installed. Database tracking will be disabled until installed.")


def get_db_url() -> str:
    """Retrieve DATABASE_URL from environment."""
    return os.environ.get("DATABASE_URL", "").strip()


def is_db_configured() -> bool:
    """Check if database is configured and driver is available."""
    return bool(PSYCOPG2_AVAILABLE and get_db_url())


@contextlib.contextmanager
def get_db_connection():
    """Context manager for obtaining a short-lived PostgreSQL connection.
    Ideal for serverless (Vercel) environments where connections should not leak.
    """
    if not is_db_configured():
        yield None
        return

    db_url = get_db_url()
    conn = None
    try:
        # Enforce sslmode=require for Neon PostgreSQL
        conn_kwargs = {"connect_timeout": 5}
        if "sslmode=" not in db_url.lower():
            conn_kwargs["sslmode"] = "require"

        conn = psycopg2.connect(db_url, **conn_kwargs)
        yield conn
    except Exception as e:
        logger.error("Database connection error: %s", e)
        yield None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def utc_to_ist_display(dt) -> str:
    """Format UTC datetime into human-friendly IST string: '20 Nov 2026, 04:30:15 PM IST'."""
    if not dt:
        return "—"
    try:
        if isinstance(dt, str):
            # Attempt to parse ISO string
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist_dt = dt.astimezone(IST)
        return ist_dt.strftime("%d %b %Y, %I:%M:%S %p")
    except Exception:
        return str(dt)


def utc_to_ist_iso(dt) -> str:
    """Format UTC datetime into ISO-8601 IST string."""
    if not dt:
        return ""
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).isoformat()
    except Exception:
        return str(dt)


def init_db():
    """Automatically create tables and indexes if they do not exist.
    Fails safely without raising if connection fails.
    """
    if not is_db_configured():
        logger.info("DATABASE_URL not set or psycopg2 missing. Skipping DB initialization.")
        return False

    create_login_events_sql = """
    CREATE TABLE IF NOT EXISTS login_events (
        id SERIAL PRIMARY KEY,
        roll_number VARCHAR(64) NOT NULL,
        student_name VARCHAR(255),
        login_status VARCHAR(32) NOT NULL,
        failure_reason TEXT,
        browser VARCHAR(128),
        device VARCHAR(128),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_login_events_created_at ON login_events(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_login_events_roll ON login_events(roll_number);
    CREATE INDEX IF NOT EXISTS idx_login_events_status ON login_events(login_status);
    """

    create_user_sessions_sql = """
    CREATE TABLE IF NOT EXISTS user_sessions (
        id SERIAL PRIMARY KEY,
        roll_number VARCHAR(64) NOT NULL,
        student_name VARCHAR(255),
        session_id VARCHAR(128) UNIQUE NOT NULL,
        login_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        logout_at TIMESTAMP WITH TIME ZONE,
        status VARCHAR(32) DEFAULT 'active'
    );
    CREATE INDEX IF NOT EXISTS idx_user_sessions_session_id ON user_sessions(session_id);
    CREATE INDEX IF NOT EXISTS idx_user_sessions_status_last_seen ON user_sessions(status, last_seen);
    CREATE INDEX IF NOT EXISTS idx_user_sessions_roll ON user_sessions(roll_number);
    """

    try:
        with get_db_connection() as conn:
            if not conn:
                return False
            with conn.cursor() as cur:
                cur.execute(create_login_events_sql)
                cur.execute(create_user_sessions_sql)
            conn.commit()
            logger.info("Database tables verified/created successfully.")
            return True
    except Exception as e:
        logger.error("Error initializing database tables: %s", e)
        return False


# ── Student Login Tracking ───────────────────────────────────────────────────

def record_login_event(roll_number: str, student_name: str | None, login_status: str,
                       failure_reason: str | None, browser: str, device: str) -> int | None:
    """Record a student login attempt (SUCCESS or FAILED) into login_events table.
    Guaranteed never to throw exceptions to caller.
    """
    if not is_db_configured():
        return None

    sql = """
    INSERT INTO login_events (roll_number, student_name, login_status, failure_reason, browser, device, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
    RETURNING id;
    """
    try:
        with get_db_connection() as conn:
            if not conn:
                return None
            with conn.cursor() as cur:
                cur.execute(sql, (
                    str(roll_number).strip().upper(),
                    str(student_name).strip() if student_name else None,
                    str(login_status).strip().upper(),
                    str(failure_reason).strip() if failure_reason else None,
                    browser,
                    device
                ))
                new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
    except Exception as e:
        logger.error("Failed to record login event for %s: %s", roll_number, e)
        return None


def create_user_session(roll_number: str, student_name: str | None, session_id: str) -> bool:
    """Create a new session record in user_sessions table after successful authentication."""
    if not is_db_configured() or not session_id:
        return False

    sql = """
    INSERT INTO user_sessions (roll_number, student_name, session_id, login_at, last_seen, status)
    VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'active')
    ON CONFLICT (session_id) DO UPDATE
    SET last_seen = CURRENT_TIMESTAMP, status = 'active';
    """
    try:
        with get_db_connection() as conn:
            if not conn:
                return False
            with conn.cursor() as cur:
                cur.execute(sql, (
                    str(roll_number).strip().upper(),
                    str(student_name).strip() if student_name else None,
                    session_id
                ))
            conn.commit()
            return True
    except Exception as e:
        logger.error("Failed to create user session for %s: %s", roll_number, e)
        return False


def heartbeat_session(session_id: str) -> bool:
    """Update last_seen timestamp of an active session."""
    if not is_db_configured() or not session_id:
        return False

    sql = """
    UPDATE user_sessions
    SET last_seen = CURRENT_TIMESTAMP
    WHERE session_id = %s AND status = 'active';
    """
    try:
        with get_db_connection() as conn:
            if not conn:
                return False
            with conn.cursor() as cur:
                cur.execute(sql, (session_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error("Failed to heartbeat session %s: %s", session_id, e)
        return False


def logout_session(session_id: str) -> bool:
    """Mark a session as logged out / inactive."""
    if not is_db_configured() or not session_id:
        return False

    sql = """
    UPDATE user_sessions
    SET logout_at = CURRENT_TIMESTAMP,
        last_seen = CURRENT_TIMESTAMP,
        status = 'inactive'
    WHERE session_id = %s;
    """
    try:
        with get_db_connection() as conn:
            if not conn:
                return False
            with conn.cursor() as cur:
                cur.execute(sql, (session_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error("Failed to logout session %s: %s", session_id, e)
        return False


# ── Admin Dashboard Queries ──────────────────────────────────────────────────

def get_admin_stats() -> dict:
    """Compute aggregate monitoring statistics:
    - total login attempts
    - successful logins
    - failed logins
    - unique students (count of distinct roll numbers with success)
    - currently active sessions (status='active' AND last_seen >= NOW() - 5 min)
    - students logged in today (unique successful roll numbers today in IST)
    """
    defaults = {
        "total_attempts": 0,
        "successful_logins": 0,
        "failed_logins": 0,
        "unique_students": 0,
        "active_sessions": 0,
        "students_today": 0,
        "db_configured": is_db_configured()
    }
    if not is_db_configured():
        return defaults

    sql = """
    SELECT
        COUNT(*) AS total_attempts,
        COUNT(*) FILTER (WHERE login_status = 'SUCCESS') AS successful_logins,
        COUNT(*) FILTER (WHERE login_status = 'FAILED') AS failed_logins,
        COUNT(DISTINCT CASE WHEN login_status = 'SUCCESS' THEN roll_number END) AS unique_students,
        COUNT(DISTINCT CASE 
            WHEN login_status = 'SUCCESS' AND (created_at AT TIME ZONE 'Asia/Kolkata')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date 
            THEN roll_number END
        ) AS students_today
    FROM login_events;
    """

    active_sessions_sql = """
    SELECT COUNT(*) AS active_count
    FROM user_sessions
    WHERE status = 'active'
      AND last_seen >= (CURRENT_TIMESTAMP - INTERVAL '5 minutes');
    """

    try:
        with get_db_connection() as conn:
            if not conn:
                return defaults
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                row = cur.fetchone() or {}
                cur.execute(active_sessions_sql)
                active_row = cur.fetchone() or {}

            return {
                "total_attempts": int(row.get("total_attempts") or 0),
                "successful_logins": int(row.get("successful_logins") or 0),
                "failed_logins": int(row.get("failed_logins") or 0),
                "unique_students": int(row.get("unique_students") or 0),
                "active_sessions": int(active_row.get("active_count") or 0),
                "students_today": int(row.get("students_today") or 0),
                "db_configured": True
            }
    except Exception as e:
        logger.error("Failed to fetch admin stats: %s", e)
        return defaults


def get_login_activity(page: int = 1, limit: int = 20, search: str = "",
                       status: str = "", date_filter: str = "", sort: str = "latest") -> dict:
    """Retrieve paginated login activity records with search, status, and date filters.
    Includes last_active timestamp from the corresponding user session if available.
    """
    empty_result = {
        "events": [],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": 0,
            "totalPages": 0
        }
    }
    if not is_db_configured():
        return empty_result

    page = max(1, page)
    limit = max(1, min(100, limit))
    offset = (page - 1) * limit

    conditions = []
    params = []

    if search:
        search_term = f"%{search.strip()}%"
        conditions.append("(le.roll_number ILIKE %s OR le.student_name ILIKE %s)")
        params.extend([search_term, search_term])

    if status and status.upper() in ("SUCCESS", "FAILED"):
        conditions.append("le.login_status = %s")
        params.append(status.upper())

    if date_filter:
        # Validate YYYY-MM-DD
        try:
            datetime.strptime(date_filter.strip(), "%Y-%m-%d")
            conditions.append("(le.created_at AT TIME ZONE 'Asia/Kolkata')::date = %s::date")
            params.append(date_filter.strip())
        except ValueError:
            pass

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_clause = "ORDER BY le.created_at DESC" if sort != "oldest" else "ORDER BY le.created_at ASC"

    # Query total count
    count_sql = f"SELECT COUNT(*) FROM login_events le {where_clause};"

    # Query events with latest session activity
    data_sql = f"""
    SELECT
        le.id,
        le.roll_number,
        le.student_name,
        le.login_status,
        le.failure_reason,
        le.browser,
        le.device,
        le.created_at,
        (
            SELECT us.last_seen
            FROM user_sessions us
            WHERE us.roll_number = le.roll_number
              AND us.login_at <= le.created_at + INTERVAL '5 minutes'
              AND us.login_at >= le.created_at - INTERVAL '5 minutes'
            ORDER BY us.last_seen DESC
            LIMIT 1
        ) AS last_active
    FROM login_events le
    {where_clause}
    {order_clause}
    LIMIT %s OFFSET %s;
    """

    try:
        with get_db_connection() as conn:
            if not conn:
                return empty_result
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Count
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()["count"]

                # Data
                data_params = list(params) + [limit, offset]
                cur.execute(data_sql, tuple(data_params))
                rows = cur.fetchall()

            events = []
            for r in rows:
                events.append({
                    "id": r["id"],
                    "roll_number": r["roll_number"],
                    "student_name": r["student_name"] or "—",
                    "login_status": r["login_status"],
                    "failure_reason": r["failure_reason"] or "",
                    "browser": r["browser"] or "Unknown",
                    "device": r["device"] or "Unknown",
                    "created_at_display": utc_to_ist_display(r["created_at"]),
                    "created_at_iso": utc_to_ist_iso(r["created_at"]),
                    "last_active_display": utc_to_ist_display(r["last_active"]) if r["last_active"] else "—",
                    "last_active_iso": utc_to_ist_iso(r["last_active"]) if r["last_active"] else ""
                })

            total_pages = (total + limit - 1) // limit if total > 0 else 0

            return {
                "events": events,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "totalPages": total_pages
                }
            }
    except Exception as e:
        logger.error("Failed to query login activity: %s", e)
        return empty_result


def get_active_sessions() -> list:
    """Retrieve all sessions with heartbeat received within the last 5 minutes."""
    if not is_db_configured():
        return []

    sql = """
    SELECT
        id,
        roll_number,
        student_name,
        session_id,
        login_at,
        last_seen,
        ROUND(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen))) AS idle_seconds,
        ROUND(EXTRACT(EPOCH FROM (last_seen - login_at)) / 60) AS duration_minutes
    FROM user_sessions
    WHERE status = 'active'
      AND last_seen >= (CURRENT_TIMESTAMP - INTERVAL '5 minutes')
    ORDER BY last_seen DESC;
    """
    try:
        with get_db_connection() as conn:
            if not conn:
                return []
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchall()

            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "roll_number": r["roll_number"],
                    "student_name": r["student_name"] or "—",
                    "session_id": r["session_id"][:12] + "...",
                    "login_at_display": utc_to_ist_display(r["login_at"]),
                    "last_seen_display": utc_to_ist_display(r["last_seen"]),
                    "idle_seconds": int(r["idle_seconds"] or 0),
                    "duration_minutes": max(1, int(r["duration_minutes"] or 0))
                })
            return result
    except Exception as e:
        logger.error("Failed to fetch active sessions: %s", e)
        return []


def get_student_history(roll_number: str) -> dict:
    """Retrieve complete login and session history for a specific student."""
    empty_result = {"roll_number": roll_number, "student_name": "—", "events": [], "sessions": []}
    if not is_db_configured() or not roll_number:
        return empty_result

    roll = roll_number.strip().upper()

    events_sql = """
    SELECT id, student_name, login_status, failure_reason, browser, device, created_at
    FROM login_events
    WHERE roll_number = %s
    ORDER BY created_at DESC
    LIMIT 100;
    """

    sessions_sql = """
    SELECT id, session_id, login_at, last_seen, logout_at, status
    FROM user_sessions
    WHERE roll_number = %s
    ORDER BY login_at DESC
    LIMIT 50;
    """

    try:
        with get_db_connection() as conn:
            if not conn:
                return empty_result
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(events_sql, (roll,))
                event_rows = cur.fetchall()

                cur.execute(sessions_sql, (roll,))
                session_rows = cur.fetchall()

            # Pick the most recent known name
            student_name = "—"
            for ev in event_rows:
                if ev.get("student_name"):
                    student_name = ev["student_name"]
                    break

            events = [
                {
                    "id": r["id"],
                    "login_status": r["login_status"],
                    "failure_reason": r["failure_reason"] or "",
                    "browser": r["browser"] or "Unknown",
                    "device": r["device"] or "Unknown",
                    "created_at_display": utc_to_ist_display(r["created_at"])
                }
                for r in event_rows
            ]

            sessions = [
                {
                    "id": r["id"],
                    "session_id": r["session_id"][:12] + "...",
                    "login_at_display": utc_to_ist_display(r["login_at"]),
                    "last_seen_display": utc_to_ist_display(r["last_seen"]),
                    "logout_at_display": utc_to_ist_display(r["logout_at"]) if r["logout_at"] else "—",
                    "status": "active" if (r["status"] == "active" and r["last_seen"] and (datetime.now(timezone.utc) - r["last_seen"]).total_seconds() < 300) else "inactive"
                }
                for r in session_rows
            ]

            return {
                "roll_number": roll,
                "student_name": student_name,
                "events": events,
                "sessions": sessions
            }
    except Exception as e:
        logger.error("Failed to fetch student history for %s: %s", roll_number, e)
        return empty_result


def get_all_activity_for_export() -> list:
    """Retrieve full login events history for CSV export."""
    if not is_db_configured():
        return []

    sql = """
    SELECT
        roll_number,
        student_name,
        login_status,
        failure_reason,
        browser,
        device,
        created_at
    FROM login_events
    ORDER BY created_at DESC
    LIMIT 5000;
    """
    try:
        with get_db_connection() as conn:
            if not conn:
                return []
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchall()

            export_data = []
            for r in rows:
                export_data.append({
                    "roll_number": r["roll_number"],
                    "student_name": r["student_name"] or "",
                    "login_status": r["login_status"],
                    "failure_reason": r["failure_reason"] or "",
                    "browser": r["browser"] or "",
                    "device": r["device"] or "",
                    "login_time_ist": utc_to_ist_display(r["created_at"]),
                    "created_at_utc": r["created_at"].isoformat() if r["created_at"] else ""
                })
            return export_data
    except Exception as e:
        logger.error("Failed to fetch export data: %s", e)
        return []
