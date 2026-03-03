import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

logger = logging.getLogger("db")


def _get_db_url() -> str:
    """
    Resolve the PostgreSQL connection string from environment variables.
    Tried in order:
      1. DATABASE_URL
      2. POSTGRES_URL
      3. POSTGRES_CONN
      4. Supabase pooler URL built from SUPABASE_DB_URL (if set explicitly)
    Raises RuntimeError with a clear diagnostic message if nothing is available.
    """
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_CONN", "SUPABASE_DB_URL"):
        val = os.environ.get(key, "").strip()
        if val:
            # Normalise postgres:// → postgresql:// (psycopg2 prefers the latter)
            if val.startswith("postgres://"):
                val = "postgresql://" + val[len("postgres://"):]
            return val
    raise RuntimeError(
        "No database connection URL found. "
        "Set DATABASE_URL (or POSTGRES_URL) in your environment / Coolify variables. "
        "Example: DATABASE_URL=postgresql://postgres:[password]@db.[ref].supabase.co:5432/postgres"
    )


def get_conn():
    """Return a new psycopg2 connection. Raises RuntimeError if no DB URL is configured."""
    url = _get_db_url()
    return psycopg2.connect(url)



def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_logs (
                    id SERIAL PRIMARY KEY,
                    phone TEXT,
                    duration INTEGER,
                    transcript TEXT,
                    summary TEXT,
                    recording_url TEXT,
                    sentiment TEXT,
                    estimated_cost_usd NUMERIC(10,5),
                    call_date DATE,
                    call_hour INTEGER,
                    call_day_of_week TEXT,
                    was_booked BOOLEAN DEFAULT FALSE,
                    interrupt_count INTEGER DEFAULT 0,
                    stt_provider TEXT,
                    tts_provider TEXT,
                    audio_codec TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS call_transcripts (
                    id SERIAL PRIMARY KEY,
                    call_room_id TEXT NOT NULL,
                    phone TEXT,
                    role TEXT CHECK (role IN ('user', 'assistant')),
                    content TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS demo_links (
                    id SERIAL PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    label TEXT,
                    language TEXT DEFAULT 'auto',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    is_active BOOLEAN DEFAULT TRUE,
                    total_sessions INTEGER DEFAULT 0
                );
                -- ── Mass Calling Infrastructure Tables ──────────────────────
                CREATE TABLE IF NOT EXISTS sip_trunks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'vobiz',
                    trunk_type TEXT NOT NULL DEFAULT 'outbound',
                    sip_address TEXT NOT NULL DEFAULT '',
                    auth_username TEXT DEFAULT '',
                    auth_password TEXT DEFAULT '',
                    number_pool JSONB DEFAULT '[]'::JSONB,
                    livekit_trunk_id TEXT DEFAULT '',
                    max_concurrent_calls INT DEFAULT 10,
                    max_calls_per_number_per_day INT DEFAULT 150,
                    is_active BOOLEAN DEFAULT TRUE,
                    notes TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS voice_agent_configs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    name TEXT NOT NULL,
                    preset_type TEXT NOT NULL DEFAULT 'custom',
                    sip_trunk_id UUID REFERENCES sip_trunks(id) ON DELETE SET NULL,
                    cli_override TEXT DEFAULT '',
                    llm_model TEXT DEFAULT 'gpt-4o-mini',
                    llm_provider TEXT DEFAULT 'openai',
                    tts_provider TEXT DEFAULT 'sarvam',
                    tts_voice TEXT DEFAULT 'rohan',
                    tts_language TEXT DEFAULT 'hi-IN',
                    stt_provider TEXT DEFAULT 'sarvam',
                    stt_language TEXT DEFAULT 'hi-IN',
                    agent_instructions TEXT DEFAULT '',
                    first_line TEXT DEFAULT '',
                    max_call_duration_seconds INT DEFAULT 300,
                    max_turns INT DEFAULT 25,
                    call_window_start TIME DEFAULT '09:30',
                    call_window_end TIME DEFAULT '19:30',
                    timezone TEXT DEFAULT 'Asia/Kolkata',
                    is_active BOOLEAN DEFAULT TRUE
                );
                CREATE TABLE IF NOT EXISTS campaigns (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'draft',
                    agent_config_id UUID REFERENCES voice_agent_configs(id) ON DELETE SET NULL,
                    sip_trunk_id UUID REFERENCES sip_trunks(id) ON DELETE SET NULL,
                    max_calls_per_minute INT DEFAULT 5,
                    max_retries_per_lead INT DEFAULT 2,
                    retry_delay_hours INT DEFAULT 4,
                    daily_start_time TIME DEFAULT '09:30',
                    daily_end_time TIME DEFAULT '19:30',
                    timezone TEXT DEFAULT 'Asia/Kolkata',
                    total_leads INT DEFAULT 0,
                    called_count INT DEFAULT 0,
                    answered_count INT DEFAULT 0,
                    booked_count INT DEFAULT 0,
                    notes TEXT DEFAULT '',
                    completed_at TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS campaign_leads (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
                    phone TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    custom_data JSONB DEFAULT '{}'::JSONB,
                    status TEXT DEFAULT 'pending',
                    attempts INT DEFAULT 0,
                    last_attempt_at TIMESTAMPTZ,
                    last_result TEXT DEFAULT '',
                    livekit_room_id TEXT DEFAULT '',
                    call_duration_seconds INT DEFAULT 0,
                    booked BOOLEAN DEFAULT FALSE,
                    notes TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS dnc_list (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    phone TEXT UNIQUE NOT NULL,
                    reason TEXT DEFAULT 'manual',
                    source TEXT DEFAULT 'dashboard'
                );
                -- ── Safe migrations for existing tables ─────────────────────
                ALTER TABLE demo_links ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'auto';
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS campaign_id UUID;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS agent_config_id UUID;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS cli_used TEXT DEFAULT '';
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_type TEXT DEFAULT 'inbound';
                -- ── Performance indexes ──────────────────────────────────────
                CREATE INDEX IF NOT EXISTS idx_call_transcripts_room
                    ON call_transcripts (call_room_id);
                CREATE INDEX IF NOT EXISTS idx_call_logs_phone
                    ON call_logs (phone);
                CREATE INDEX IF NOT EXISTS idx_call_logs_created
                    ON call_logs (created_at);
                CREATE INDEX IF NOT EXISTS idx_demo_links_slug
                    ON demo_links (slug);
                CREATE INDEX IF NOT EXISTS idx_leads_campaign_status
                    ON campaign_leads (campaign_id, status);
                CREATE INDEX IF NOT EXISTS idx_leads_phone
                    ON campaign_leads (phone);
                CREATE INDEX IF NOT EXISTS idx_call_logs_campaign
                    ON call_logs (campaign_id);
                CREATE INDEX IF NOT EXISTS idx_dnc_phone
                    ON dnc_list (phone);
            """)
            conn.commit()
    logger.info("[DB] Tables initialized successfully")



def save_call_log(
    phone, duration, transcript, summary,
    recording_url=None, sentiment=None,
    estimated_cost_usd=None, call_date=None,
    call_hour=None, call_day_of_week=None,
    was_booked=False, interrupt_count=0,
    stt_provider=None, tts_provider=None,
    audio_codec=None, caller_name=None,
):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO call_logs (
                        phone, duration, transcript, summary,
                        recording_url, sentiment, estimated_cost_usd,
                        call_date, call_hour, call_day_of_week,
                        was_booked, interrupt_count,
                        stt_provider, tts_provider, audio_codec
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s
                    )
                """, (
                    phone, duration, transcript, summary,
                    recording_url, sentiment, estimated_cost_usd,
                    call_date, call_hour, call_day_of_week,
                    was_booked, interrupt_count,
                    stt_provider, tts_provider, audio_codec,
                ))
                conn.commit()
        logger.info(f"[DB] Call log saved for {phone}")
    except Exception as e:
        logger.error(f"[DB] Failed to save call log: {e}")


def log_transcript_line(call_room_id, phone, role, content):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO call_transcripts
                        (call_room_id, phone, role, content)
                    VALUES (%s, %s, %s, %s)
                """, (call_room_id, phone, role, content))
                conn.commit()
    except Exception as e:
        logger.warning(f"[DB] Transcript line failed: {e}")


def fetch_call_logs(limit: int = 50) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM call_logs ORDER BY created_at DESC LIMIT %s",
                    (limit,)
                )
                rows = cur.fetchall()
                # Convert to plain dicts and normalise field names for UI
                result = []
                for r in rows:
                    d = dict(r)
                    # Map to the field names the UI dashboard expects
                    d["phone_number"] = d.get("phone", "")
                    d["duration_seconds"] = d.get("duration", 0)
                    result.append(d)
                return result
    except Exception as e:
        logger.error(f"[DB] fetch_call_logs failed: {e}")
        return []


def fetch_bookings() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM call_logs WHERE summary ILIKE '%Confirmed%' "
                    "ORDER BY created_at DESC LIMIT 200"
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] fetch_bookings failed: {e}")
        return []


def fetch_stats() -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM call_logs")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM call_logs WHERE summary ILIKE '%Confirmed%'")
                bookings = cur.fetchone()[0]
                cur.execute("SELECT AVG(duration) FROM call_logs WHERE duration IS NOT NULL")
                avg_dur_raw = cur.fetchone()[0]
                avg_dur = round(float(avg_dur_raw)) if avg_dur_raw else 0
                rate = round((bookings / total) * 100) if total else 0
                return {
                    "total_calls": total,
                    "total_bookings": bookings,
                    "avg_duration": avg_dur,
                    "booking_rate": rate,
                }
    except Exception as e:
        logger.error(f"[DB] fetch_stats failed: {e}")
        return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}


# ── Telephony / Mass Calling Helpers ─────────────────────────────────────────

def fetch_trunks() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM sip_trunks ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] fetch_trunks failed: {e}")
        return []


def insert_trunk(data: dict) -> dict:
    cols = list(data.keys())
    vals = list(data.values())
    placeholders = ", ".join(["%s"] * len(cols))
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"INSERT INTO sip_trunks ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                vals
            )
            row = dict(cur.fetchone())
            conn.commit()
    return row


def delete_trunk_by_id(trunk_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sip_trunks WHERE id = %s", (trunk_id,))
            conn.commit()


def fetch_agent_configs() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT vac.*, st.name as trunk_name, st.provider as trunk_provider
                    FROM voice_agent_configs vac
                    LEFT JOIN sip_trunks st ON vac.sip_trunk_id = st.id
                    WHERE vac.is_active = TRUE
                    ORDER BY vac.created_at DESC
                    """
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] fetch_agent_configs failed: {e}")
        return []


def insert_agent_config(data: dict) -> dict:
    ALLOWED = [
        "name", "preset_type", "sip_trunk_id", "cli_override", "llm_model", "llm_provider",
        "tts_provider", "tts_voice", "tts_language", "stt_provider", "stt_language",
        "agent_instructions", "first_line", "max_call_duration_seconds", "max_turns",
        "call_window_start", "call_window_end", "timezone"
    ]
    filtered = {k: v for k, v in data.items() if k in ALLOWED and v is not None}
    cols = list(filtered.keys())
    vals = list(filtered.values())
    placeholders = ", ".join(["%s"] * len(cols))
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"INSERT INTO voice_agent_configs ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                vals
            )
            row = dict(cur.fetchone())
            conn.commit()
    return row


def update_agent_config(config_id: str, data: dict) -> dict:
    ALLOWED = [
        "name", "preset_type", "sip_trunk_id", "llm_model", "llm_provider",
        "tts_provider", "tts_voice", "tts_language", "stt_language",
        "agent_instructions", "first_line", "max_call_duration_seconds", "max_turns",
        "call_window_start", "call_window_end", "timezone"
    ]
    filtered = {k: v for k, v in data.items() if k in ALLOWED and v is not None}
    if not filtered:
        return {}
    sets = ", ".join([f"{k} = %s" for k in filtered.keys()])
    vals = list(filtered.values()) + [config_id]
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"UPDATE voice_agent_configs SET {sets} WHERE id = %s RETURNING *", vals)
            row = dict(cur.fetchone() or {})
            conn.commit()
    return row


def soft_delete_agent_config(config_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE voice_agent_configs SET is_active = FALSE WHERE id = %s", (config_id,))
            conn.commit()


def fetch_campaigns() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT c.*,
                           vac.name as config_name, vac.preset_type,
                           st.name as trunk_name, st.provider as trunk_provider
                    FROM campaigns c
                    LEFT JOIN voice_agent_configs vac ON c.agent_config_id = vac.id
                    LEFT JOIN sip_trunks st ON c.sip_trunk_id = st.id
                    ORDER BY c.created_at DESC
                    """
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] fetch_campaigns failed: {e}")
        return []


def insert_campaign(data: dict) -> dict:
    ALLOWED = [
        "name", "agent_config_id", "sip_trunk_id", "max_calls_per_minute",
        "max_retries_per_lead", "retry_delay_hours", "daily_start_time",
        "daily_end_time", "timezone", "notes"
    ]
    filtered = {k: v for k, v in data.items() if k in ALLOWED and v is not None}
    filtered["status"] = "draft"
    cols = list(filtered.keys())
    vals = list(filtered.values())
    placeholders = ", ".join(["%s"] * len(cols))
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"INSERT INTO campaigns ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                vals
            )
            row = dict(cur.fetchone())
            conn.commit()
    return row


def update_campaign_status(campaign_id: str, status: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status == "completed":
                cur.execute(
                    "UPDATE campaigns SET status = %s, completed_at = NOW() WHERE id = %s",
                    (status, campaign_id)
                )
            else:
                cur.execute(
                    "UPDATE campaigns SET status = %s WHERE id = %s",
                    (status, campaign_id)
                )
            conn.commit()


def delete_campaign_by_id(campaign_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM campaign_leads WHERE campaign_id = %s", (campaign_id,))
            cur.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))
            conn.commit()


def fetch_campaign_leads(campaign_id: str, status: str = None, limit: int = 100, offset: int = 0) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status:
                    cur.execute(
                        "SELECT * FROM campaign_leads WHERE campaign_id = %s AND status = %s "
                        "ORDER BY created_at LIMIT %s OFFSET %s",
                        (campaign_id, status, limit, offset)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM campaign_leads WHERE campaign_id = %s "
                        "ORDER BY created_at LIMIT %s OFFSET %s",
                        (campaign_id, limit, offset)
                    )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] fetch_campaign_leads failed: {e}")
        return []


def insert_leads_bulk(campaign_id: str, leads: list) -> int:
    if not leads:
        return 0
    rows_to_insert = []
    for lead in leads:
        phone = str(lead.get("phone", "")).strip()
        if not phone:
            continue
        rows_to_insert.append((
            campaign_id,
            phone,
            str(lead.get("name", "")),
            str(lead.get("email", "")),
            json.dumps(lead.get("custom_data", {})) if lead.get("custom_data") else "{}",
        ))
    if not rows_to_insert:
        return 0
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                from psycopg2.extras import execute_values
                execute_values(
                    cur,
                    "INSERT INTO campaign_leads (campaign_id, phone, name, email, custom_data) VALUES %s "
                    "ON CONFLICT DO NOTHING",
                    rows_to_insert
                )
                count = cur.rowcount
                cur.execute(
                    "UPDATE campaigns SET total_leads = total_leads + %s WHERE id = %s",
                    (count, campaign_id)
                )
                conn.commit()
        return count
    except Exception as e:
        logger.error(f"[DB] insert_leads_bulk failed: {e}")
        return 0


def fetch_dnc(limit: int = 200, offset: int = 0) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM dnc_list ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (limit, offset)
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] fetch_dnc failed: {e}")
        return []


def add_to_dnc(phone: str, reason: str = "manual", source: str = "dashboard"):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dnc_list (phone, reason, source) VALUES (%s, %s, %s) "
                "ON CONFLICT (phone) DO UPDATE SET reason = EXCLUDED.reason",
                (phone, reason, source)
            )
            conn.commit()


def remove_from_dnc(phone: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dnc_list WHERE phone = %s", (phone,))
            conn.commit()


def fetch_telephony_overview() -> dict:
    try:
        from datetime import date
        today = date.today().isoformat()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM campaigns")
                total_campaigns = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'active'")
                active_campaigns = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM call_logs WHERE call_type = 'outbound' AND DATE(created_at) = %s",
                    (today,)
                )
                calls_today = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM dnc_list")
                dnc_count = cur.fetchone()[0]
        return {
            "total_campaigns": total_campaigns,
            "active_campaigns": active_campaigns,
            "calls_today": calls_today,
            "dnc_count": dnc_count,
        }
    except Exception as e:
        logger.error(f"[DB] fetch_telephony_overview failed: {e}")
        return {"total_campaigns": 0, "active_campaigns": 0, "calls_today": 0, "dnc_count": 0}


def fetch_daily_analytics(days: int = 14) -> list:
    from datetime import date, timedelta
    results = []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for i in range(days - 1, -1, -1):
                    d = (date.today() - timedelta(days=i)).isoformat()
                    cur.execute(
                        """
                        SELECT
                            COUNT(*) as total_calls,
                            SUM(CASE WHEN call_type='outbound' THEN 1 ELSE 0 END) as outbound,
                            SUM(CASE WHEN call_type='inbound'  THEN 1 ELSE 0 END) as inbound,
                            SUM(CASE WHEN was_booked THEN 1 ELSE 0 END) as total_booked,
                            SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
                            SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
                            COALESCE(AVG(duration), 0) as avg_duration_sec
                        FROM call_logs
                        WHERE DATE(created_at) = %s
                        """,
                        (d,)
                    )
                    row = cur.fetchone()
                    results.append({
                        "date":             d,
                        "total_calls":      row[0] or 0,
                        "outbound":         row[1] or 0,
                        "inbound":          row[2] or 0,
                        "total_booked":     row[3] or 0,
                        "positive":         row[4] or 0,
                        "negative":         row[5] or 0,
                        "avg_duration_sec": round(float(row[6] or 0)),
                    })
    except Exception as e:
        logger.error(f"[DB] fetch_daily_analytics failed: {e}")
    return results


import json
