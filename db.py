# db.py — Pure PostgreSQL via psycopg2, no Supabase
import os
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

import pytz

logger = logging.getLogger("db")

# ── Connection Pool ───────────────────────────────────────────────────────────
_pool = None


def _build_dsn() -> str:
    """Resolve the Postgres DSN from env vars. Tries several common names."""
    for key in ("DATABASE_URL", "POSTGRES_URL", "DB_URL", "POSTGRES_CONN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            # psycopg2 needs postgresql://, not postgres://
            if val.startswith("postgres://"):
                val = "postgresql://" + val[len("postgres://"):]
            return val
    # Fall back to individual parts
    host     = os.environ.get("POSTGRES_HOST", "localhost")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ.get("POSTGRES_DB",   "postgres")
    user     = os.environ.get("POSTGRES_USER",     "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _get_pool():
    global _pool
    if _pool is None:
        from psycopg2 import pool as pg_pool
        dsn = _build_dsn()
        _pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=dsn)
        logger.info("[DB] Connection pool created")
    return _pool


@contextmanager
def _get_conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# Public alias used by some older code paths
def get_conn():
    """Return a raw psycopg2 connection (caller must close/commit)."""
    from psycopg2 import connect
    return connect(_build_dsn())


def _get_db_url() -> str:
    """Return the resolved DSN (used by /api/db-status)."""
    return _build_dsn()


# ── Startup / Health ──────────────────────────────────────────────────────────

def init_db():
    """
    Verify DB is reachable and the core tables exist.
    The actual schema must be applied via the SQL migration in the README.
    This no longer tries to CREATE tables — that caused psycopg2 multi-statement bugs.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                # Confirm the call_logs table exists
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='call_logs'"
                )
                exists = cur.fetchone()[0]
                if not exists:
                    logger.warning(
                        "[DB] 'call_logs' table NOT found. "
                        "Run the SQL migration from the README first!"
                    )
                else:
                    cur.execute("SELECT COUNT(*) FROM call_logs")
                    count = cur.fetchone()[0]
                    logger.info(f"[DB] init_db OK — call_logs has {count} rows")
    except Exception as e:
        logger.error(f"[DB] init_db FAILED: {e}")
        raise


# ── Call Logs ─────────────────────────────────────────────────────────────────

def save_call_log(
    phone: str = "unknown",
    duration: int = 0,
    transcript: str = "",
    summary: str = "",
    recording_url: str = "",
    sentiment: str = "unknown",
    interrupt_count: int = 0,
    estimated_cost_usd: float = 0.0,
    call_date=None,
    call_hour: int = 0,
    call_day_of_week: str = "",
    was_booked: bool = False,
    stt_provider: str = "sarvam",
    tts_provider: str = "sarvam",
    call_type: str = "inbound",
    cli_used: str = "",
    caller_name: str = "",
    llm_model: str = "gpt-4o-mini",
    audio_codec: str = "",
    **kwargs,
) -> dict | None:
    ist = pytz.timezone("Asia/Kolkata")
    if not call_date:
        call_date = datetime.now(ist).date().isoformat()
    if not call_hour:
        call_hour = datetime.now(ist).hour

    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO call_logs (
                        phone, caller_name, duration, call_type,
                        call_date, call_hour, call_day_of_week,
                        was_booked, sentiment, summary, transcript,
                        recording_url, estimated_cost_usd,
                        stt_provider, tts_provider, llm_model,
                        cli_used, interrupt_count
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                    RETURNING id, created_at
                """, (
                    phone or "unknown", caller_name or "", int(duration or 0), call_type or "inbound",
                    call_date, int(call_hour or 0), call_day_of_week or "",
                    bool(was_booked), sentiment or "unknown", summary or "", transcript or "",
                    recording_url or "", float(estimated_cost_usd or 0),
                    stt_provider or "sarvam", tts_provider or "sarvam", llm_model or "gpt-4o-mini",
                    cli_used or "", int(interrupt_count or 0),
                ))
                row = cur.fetchone()
                inserted_id = str(row[0]) if row else None

        # Upsert CRM contact (non-fatal)
        try:
            upsert_crm_contact(phone=phone, name=caller_name or "")
        except Exception as crm_err:
            logger.warning(f"[DB] CRM upsert skipped: {crm_err}")

        logger.info(f"[DB] save_call_log OK — id={inserted_id} phone={phone} duration={duration}s booked={was_booked}")
        return {"id": inserted_id}
    except Exception as e:
        logger.error(f"[DB] save_call_log FAILED: {e}")
        raise


def fetch_call_logs(limit: int = 100, offset: int = 0) -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, created_at, phone, caller_name, duration,
                        call_type, was_booked, sentiment, summary,
                        recording_url, estimated_cost_usd, stt_provider,
                        tts_provider, llm_model, interrupt_count, call_date
                    FROM call_logs
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """, (limit, offset))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for r in rows:
            if r.get("created_at"): r["created_at"]  = r["created_at"].isoformat()
            if r.get("call_date"):  r["call_date"]    = str(r["call_date"])
            if r.get("id"):         r["id"]           = str(r["id"])
            # Aliases for backward-compat with the UI
            r["phone_number"]       = r.get("phone", "")
            r["duration_seconds"]   = r.get("duration", 0)

        logger.info(f"[DB] fetch_call_logs returned {len(rows)} rows")
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_call_logs FAILED: {e}")
        return []


def fetch_stats() -> dict:
    """Alias for fetch_dashboard_stats — keeps old callers working."""
    return fetch_dashboard_stats()


def fetch_dashboard_stats() -> dict:
    try:
        ist = pytz.timezone("Asia/Kolkata")
        today_str    = datetime.now(ist).date().isoformat()
        week_ago_str = (datetime.now(ist) - timedelta(days=7)).date().isoformat()

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM call_logs")
                total_calls = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM call_logs WHERE call_date = %s", (today_str,))
                calls_today = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM call_logs WHERE call_date >= %s", (week_ago_str,))
                calls_this_week = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM call_logs WHERE was_booked = true")
                bookings_made = cur.fetchone()[0]

                cur.execute("SELECT COALESCE(AVG(duration), 0) FROM call_logs WHERE duration IS NOT NULL")
                avg_duration = round(float(cur.fetchone()[0]))

                cur.execute("SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM call_logs")
                total_cost = round(float(cur.fetchone()[0]), 4)

        booking_rate = round(bookings_made / max(total_calls, 1) * 100, 1)

        return {
            "total_calls":      total_calls,
            "total_bookings":   bookings_made,
            "avg_duration":     avg_duration,
            "booking_rate":     booking_rate,
            "calls_today":      calls_today,
            "calls_this_week":  calls_this_week,
            "total_cost_usd":   total_cost,
            "db_error":         None,
        }
    except Exception as e:
        logger.error(f"[DB] fetch_dashboard_stats FAILED: {e}")
        return {
            "total_calls": None, "total_bookings": None,
            "avg_duration": None, "booking_rate": None,
            "db_error": str(e),
        }


# ── Transcript Lines ──────────────────────────────────────────────────────────

def log_transcript_line(call_room_id: str, phone: str, role: str, content: str):
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO transcript_lines (room_id, phone, role, content)
                    VALUES (%s, %s, %s, %s)
                """, (call_room_id, phone, role, content))
    except Exception as e:
        logger.warning(f"[DB] log_transcript_line failed (non-critical): {e}")


# ── CRM Contacts ──────────────────────────────────────────────────────────────

def upsert_crm_contact(phone: str, name: str = ""):
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO crm_contacts (phone, name, last_call, total_calls)
                    VALUES (%s, %s, NOW(), 1)
                    ON CONFLICT (phone) DO UPDATE SET
                        last_call   = NOW(),
                        total_calls = crm_contacts.total_calls + 1,
                        name        = CASE WHEN %s != '' THEN %s ELSE crm_contacts.name END
                """, (phone, name, name, name))
    except Exception as e:
        logger.warning(f"[DB] upsert_crm_contact failed: {e}")


def fetch_crm_contacts(limit: int = 200) -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, created_at, phone, name, email,
                           last_call, total_calls, was_booked, notes
                    FROM crm_contacts
                    ORDER BY last_call DESC NULLS LAST
                    LIMIT %s
                """, (limit,))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("id"):         r["id"]         = str(r["id"])
            if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
            if r.get("last_call"):  r["last_call"]  = r["last_call"].isoformat()
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_crm_contacts FAILED: {e}")
        return []


# ── Demo Links ────────────────────────────────────────────────────────────────

def fetch_demo_links() -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM demo_links ORDER BY created_at DESC")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_demo_links FAILED: {e}")
        return []


# ── Mass Calling helpers (stubs — tables created by SQL migration) ─────────────

def fetch_trunks() -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM sip_trunks ORDER BY created_at DESC")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("id"):         r["id"]         = str(r["id"])
            if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
            if r.get("number_pool") is None: r["number_pool"] = []
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_trunks FAILED: {e}")
        return []


def insert_trunk(data: dict) -> dict:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                import json
                cur.execute("""
                    INSERT INTO sip_trunks
                        (name, provider, trunk_type, sip_address,
                         auth_username, auth_password, number_pool,
                         max_concurrent_calls, notes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    data.get("name",""), data.get("provider","vobiz"),
                    data.get("trunk_type","outbound"), data.get("sip_address",""),
                    data.get("auth_username",""), data.get("auth_password",""),
                    json.dumps(data.get("number_pool",[])),
                    int(data.get("max_concurrent_calls",10)), data.get("notes",""),
                ))
                row = cur.fetchone()
        return {"id": str(row[0]) if row else None}
    except Exception as e:
        logger.error(f"[DB] insert_trunk FAILED: {e}")
        raise


def delete_trunk(trunk_id: str):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sip_trunks WHERE id = %s", (trunk_id,))


def fetch_agent_configs() -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM voice_agent_configs ORDER BY created_at DESC")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("id"):              r["id"]              = str(r["id"])
            if r.get("sip_trunk_id"):    r["sip_trunk_id"]    = str(r["sip_trunk_id"])
            if r.get("created_at"):      r["created_at"]      = r["created_at"].isoformat()
            if r.get("call_window_start"): r["call_window_start"] = str(r["call_window_start"])
            if r.get("call_window_end"):   r["call_window_end"]   = str(r["call_window_end"])
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_agent_configs FAILED: {e}")
        return []


def insert_agent_config(data: dict) -> dict:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO voice_agent_configs
                        (name, preset_type, sip_trunk_id, cli_override,
                         llm_model, tts_provider, tts_voice, tts_language,
                         stt_provider, stt_language, agent_instructions, first_line,
                         max_call_duration_seconds, max_turns,
                         call_window_start, call_window_end, timezone)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    data.get("name",""), data.get("preset_type","custom"),
                    data.get("sip_trunk_id") or None, data.get("cli_override",""),
                    data.get("llm_model","gpt-4o-mini"), data.get("tts_provider","sarvam"),
                    data.get("tts_voice","rohan"), data.get("tts_language","hi-IN"),
                    data.get("stt_provider","sarvam"), data.get("stt_language","hi-IN"),
                    data.get("agent_instructions",""), data.get("first_line",""),
                    int(data.get("max_call_duration_seconds",300)), int(data.get("max_turns",25)),
                    data.get("call_window_start","09:30"), data.get("call_window_end","19:30"),
                    data.get("timezone","Asia/Kolkata"),
                ))
                row = cur.fetchone()
        return {"id": str(row[0]) if row else None}
    except Exception as e:
        logger.error(f"[DB] insert_agent_config FAILED: {e}")
        raise


def fetch_campaigns() -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM campaigns ORDER BY created_at DESC")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            for k in ("id","agent_config_id","sip_trunk_id"):
                if r.get(k): r[k] = str(r[k])
            if r.get("created_at"):   r["created_at"]   = r["created_at"].isoformat()
            if r.get("completed_at"): r["completed_at"] = r["completed_at"].isoformat()
            if r.get("daily_start_time"): r["daily_start_time"] = str(r["daily_start_time"])
            if r.get("daily_end_time"):   r["daily_end_time"]   = str(r["daily_end_time"])
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_campaigns FAILED: {e}")
        return []


def insert_campaign(data: dict) -> dict:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO campaigns
                        (name, agent_config_id, sip_trunk_id,
                         max_calls_per_minute, max_retries_per_lead,
                         daily_start_time, daily_end_time, timezone, notes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    data.get("name",""), data.get("agent_config_id") or None,
                    data.get("sip_trunk_id") or None,
                    int(data.get("max_calls_per_minute",5)),
                    int(data.get("max_retries_per_lead",2)),
                    data.get("daily_start_time","09:30"),
                    data.get("daily_end_time","19:30"),
                    data.get("timezone","Asia/Kolkata"),
                    data.get("notes",""),
                ))
                row = cur.fetchone()
        return {"id": str(row[0]) if row else None}
    except Exception as e:
        logger.error(f"[DB] insert_campaign FAILED: {e}")
        raise


def update_campaign_status(campaign_id: str, status: str):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE campaigns SET status=%s WHERE id=%s",
                (status, campaign_id)
            )


def fetch_campaign_leads(campaign_id: str, limit: int = 500) -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM campaign_leads WHERE campaign_id=%s ORDER BY created_at LIMIT %s",
                    (campaign_id, limit)
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            for k in ("id","campaign_id"):
                if r.get(k): r[k] = str(r[k])
            if r.get("created_at"):      r["created_at"]      = r["created_at"].isoformat()
            if r.get("last_attempt_at"): r["last_attempt_at"] = r["last_attempt_at"].isoformat()
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_campaign_leads FAILED: {e}")
        return []


def insert_leads_bulk(campaign_id: str, leads: list) -> int:
    if not leads:
        return 0
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                from psycopg2.extras import execute_values
                execute_values(
                    cur,
                    "INSERT INTO campaign_leads (campaign_id, phone, name, email) VALUES %s ON CONFLICT DO NOTHING",
                    [(campaign_id, l.get("phone",""), l.get("name",""), l.get("email","")) for l in leads],
                )
                # Update total_leads counter
                cur.execute(
                    "UPDATE campaigns SET total_leads = (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id=%s) WHERE id=%s",
                    (campaign_id, campaign_id)
                )
        return len(leads)
    except Exception as e:
        logger.error(f"[DB] insert_leads_bulk FAILED: {e}")
        raise


def fetch_dnc(limit: int = 500) -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM dnc_list ORDER BY created_at DESC LIMIT %s", (limit,))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("id"):         r["id"]         = str(r["id"])
            if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_dnc FAILED: {e}")
        return []


def add_to_dnc(phone: str, reason: str = "manual", source: str = "dashboard"):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dnc_list (phone, reason, source) VALUES (%s,%s,%s) ON CONFLICT (phone) DO NOTHING",
                (phone, reason, source)
            )


def remove_from_dnc(phone: str):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dnc_list WHERE phone=%s", (phone,))


def is_on_dnc(phone: str) -> bool:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM dnc_list WHERE phone=%s LIMIT 1", (phone,))
                return cur.fetchone() is not None
    except Exception:
        return False


def fetch_telephony_overview() -> dict:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM campaigns")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM campaigns WHERE status='running'")
                active = cur.fetchone()[0]
                ist = pytz.timezone("Asia/Kolkata")
                today = datetime.now(ist).date().isoformat()
                cur.execute(
                    "SELECT COUNT(*) FROM call_logs WHERE call_date=%s AND call_type='outbound'",
                    (today,)
                )
                calls_today = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM dnc_list")
                dnc_count = cur.fetchone()[0]
        return {
            "total_campaigns": total,
            "active_campaigns": active,
            "calls_today": calls_today,
            "dnc_count": dnc_count,
        }
    except Exception as e:
        logger.error(f"[DB] fetch_telephony_overview FAILED: {e}")
        return {"total_campaigns":0,"active_campaigns":0,"calls_today":0,"dnc_count":0}


def fetch_daily_analytics(days: int = 14) -> list:
    try:
        ist = pytz.timezone("Asia/Kolkata")
        start = (datetime.now(ist) - timedelta(days=days)).date().isoformat()
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT call_date, COUNT(*) as total_calls, SUM(CASE WHEN was_booked THEN 1 ELSE 0 END) as booked
                    FROM call_logs
                    WHERE call_date >= %s
                    GROUP BY call_date
                    ORDER BY call_date
                """, (start,))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("call_date"): r["call_date"] = str(r["call_date"])
        return rows
    except Exception as e:
        logger.error(f"[DB] fetch_daily_analytics FAILED: {e}")
        return []
