import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

logger = logging.getLogger("db")


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


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
                CREATE TABLE IF NOT EXISTS agents (
                    id UUID PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT FALSE,
                    stt_language TEXT,
                    tts_language TEXT,
                    tts_voice TEXT,
                    llm_model TEXT,
                    first_line TEXT,
                    agent_instructions TEXT,
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
                CREATE TABLE IF NOT EXISTS call_dnc (
                    phone TEXT PRIMARY KEY,
                    reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS sip_trunks (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    sip_uri TEXT NOT NULL,
                    username TEXT,
                    password TEXT,
                    caller_id_number TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS campaigns (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    phone_numbers TEXT NOT NULL DEFAULT '',
                    sip_trunk_id INTEGER REFERENCES sip_trunks(id),
                    max_concurrent_calls INTEGER DEFAULT 5,
                    notes TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS campaign_targets (
                    id SERIAL PRIMARY KEY,
                    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
                    phone TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER DEFAULT 0,
                    last_attempt_at TIMESTAMPTZ,
                    scheduled_time TIMESTAMPTZ
                );

                CREATE INDEX IF NOT EXISTS idx_call_logs_phone
                    ON call_logs (phone);
                CREATE INDEX IF NOT EXISTS idx_call_logs_created
                    ON call_logs (created_at);
                CREATE INDEX IF NOT EXISTS idx_demo_links_slug
                    ON demo_links (slug);

                -- Safe migrations: add new columns to existing tables
                ALTER TABLE demo_links ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'auto';
                
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS audio_codec TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS stt_provider TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS tts_provider TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS estimated_cost_usd NUMERIC(10,5);
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_hour INTEGER;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_day_of_week TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS interrupt_count INTEGER DEFAULT 0;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_name TEXT;
                
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS sip_uri TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS username TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS password TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS caller_id_number TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
                
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS phone_numbers TEXT DEFAULT '';
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sip_trunk_id INTEGER;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_concurrent_calls INTEGER DEFAULT 5;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS notes TEXT;
            """)
            conn.commit()
    logger.info("[DB] Tables initialized successfully")


# ── SIP Trunks ────────────────────────────────────────────────────────────────

def get_sip_trunks() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM sip_trunks WHERE is_active = TRUE ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] get_sip_trunks failed: {e}")
        return []


def create_sip_trunk(name, provider, sip_uri, username=None, password=None, caller_id_number=None) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO sip_trunks (name, provider, sip_uri, username, password, caller_id_number)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (name, provider, sip_uri, username, password, caller_id_number))
            conn.commit()
            return dict(cur.fetchone())


def delete_sip_trunk(trunk_id: int) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE sip_trunks SET is_active = FALSE WHERE id = %s", (trunk_id,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] delete_sip_trunk failed: {e}")
        return False

# ── Agents ────────────────────────────────────────────────────────────────────

def get_agents() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM agents ORDER BY created_at ASC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] get_agents failed: {e}")
        return []

def get_active_agent() -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM agents WHERE is_active = TRUE LIMIT 1")
                res = cur.fetchone()
                return dict(res) if res else None
    except Exception as e:
        logger.error(f"[DB] get_active_agent failed: {e}")
        return None

def create_agent(agent_id, name, stt_language, tts_language, tts_voice, llm_model, first_line, agent_instructions) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO agents (id, name, stt_language, tts_language, tts_voice, llm_model, first_line, agent_instructions)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (agent_id, name, stt_language, tts_language, tts_voice, llm_model, first_line, agent_instructions))
            conn.commit()
            return dict(cur.fetchone())

def update_agent(agent_id, data: dict) -> bool:
    allowed_fields = ["name", "stt_language", "tts_language", "tts_voice", "llm_model", "first_line", "agent_instructions"]
    updates = []
    values = []
    for k, v in data.items():
        if k in allowed_fields:
            updates.append(f"{k} = %s")
            values.append(v)
    if not updates:
        return False
    values.append(agent_id)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = %s", tuple(values))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] update_agent failed: {e}")
        return False

def delete_agent(agent_id: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agents WHERE id = %s", (agent_id,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] delete_agent failed: {e}")
        return False

def activate_agent(agent_id: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE agents SET is_active = FALSE")
                cur.execute("UPDATE agents SET is_active = TRUE WHERE id = %s", (agent_id,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] activate_agent failed: {e}")
        return False

# ── Campaigns ─────────────────────────────────────────────────────────────────

def get_campaigns() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM campaigns ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] get_campaigns failed: {e}")
        return []


def create_campaign(name, phone_numbers, sip_trunk_id=None, max_concurrent_calls=5, notes=None) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO campaigns (name, phone_numbers, sip_trunk_id, max_concurrent_calls, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                """, (name, phone_numbers, sip_trunk_id, max_concurrent_calls, notes))
                conn.commit()
                return dict(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] create_campaign failed: {e}")
        return {}


def update_campaign_status(campaign_id: int, status: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE campaigns SET status = %s WHERE id = %s", (status, campaign_id))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] update_campaign_status failed: {e}")
        return False


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


def is_in_dnc(phone: str) -> bool:
    """Check if a phone number is in the Do-Not-Call list."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM call_dnc WHERE phone = %s", (phone,))
                return bool(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] Failed to check DNC for {phone}: {e}")
        return False

def add_to_dnc(phone: str, reason: str = None) -> bool:
    """Add a phone number to the Do-Not-Call list."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO call_dnc (phone, reason)
                    VALUES (%s, %s)
                    ON CONFLICT (phone) DO NOTHING
                """, (phone, reason))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] Failed to add {phone} to DNC: {e}")
        return False

def remove_from_dnc(phone: str) -> bool:
    """Remove a phone number from the Do-Not-Call list."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM call_dnc WHERE phone = %s", (phone,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] Failed to remove {phone} from DNC: {e}")
        return False

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
