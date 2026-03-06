import os
import json
import logging
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from datetime import datetime

logger = logging.getLogger("db")


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── 1. Core tables (CREATE IF NOT EXISTS) ────────────────────────
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
                    caller_name TEXT,
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

                CREATE TABLE IF NOT EXISTS call_dnc (
                    phone TEXT PRIMARY KEY,
                    reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS sip_trunks (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'vobiz',
                    sip_uri TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    password TEXT,
                    caller_id_number TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id UUID PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT FALSE,
                    stt_provider TEXT DEFAULT 'sarvam',
                    stt_language TEXT DEFAULT 'hi-IN',
                    llm_provider TEXT DEFAULT 'openai',
                    llm_model TEXT DEFAULT 'gpt-4o-mini',
                    tts_provider TEXT DEFAULT 'sarvam',
                    tts_voice TEXT DEFAULT 'rohan',
                    tts_language TEXT DEFAULT 'hi-IN',
                    first_line TEXT,
                    system_prompt TEXT,
                    agent_instructions TEXT,
                    max_turns INTEGER DEFAULT 20,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS campaigns (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    phone_numbers TEXT NOT NULL DEFAULT '',
                    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
                    sip_trunk_id INTEGER REFERENCES sip_trunks(id) ON DELETE SET NULL,
                    calls_per_minute INTEGER DEFAULT 5,
                    max_concurrent_calls INTEGER DEFAULT 5,
                    retry_failed BOOLEAN DEFAULT TRUE,
                    max_retries INTEGER DEFAULT 2,
                    notes TEXT,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
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

                CREATE TABLE IF NOT EXISTS leads (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
                    phone TEXT NOT NULL,
                    name TEXT,
                    email TEXT,
                    custom_data JSONB,
                    status TEXT DEFAULT 'pending',
                    call_attempts INTEGER DEFAULT 0,
                    last_call_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS bookings (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    call_room_id TEXT,
                    caller_name TEXT,
                    caller_phone TEXT,
                    caller_email TEXT,
                    start_time TIMESTAMPTZ NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # ── 1.5. Fix campaign_targets type mismatch ───────────────────────
            cur.execute("""
                DO $$
                BEGIN
                    -- Drop and recreate campaign_targets only if campaign_id is wrong type
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'campaign_targets'
                        AND column_name = 'campaign_id'
                        AND data_type = 'integer'
                    ) AND EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'campaigns'
                        AND column_name = 'id'
                        AND data_type = 'uuid'
                    ) THEN
                        DROP TABLE IF EXISTS campaign_targets CASCADE;
                    END IF;
                END $$;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS campaign_targets (
                    id SERIAL PRIMARY KEY,
                    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
                    phone TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER DEFAULT 0,
                    last_attempt_at TIMESTAMPTZ,
                    scheduled_time TIMESTAMPTZ
                );
            """)

            # ── 2. Indexes ────────────────────────────────────────────────────
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_call_logs_phone ON call_logs (phone);
                CREATE INDEX IF NOT EXISTS idx_call_logs_created ON call_logs (created_at);
                CREATE INDEX IF NOT EXISTS idx_demo_links_slug ON demo_links (slug);
                CREATE INDEX IF NOT EXISTS idx_leads_campaign_status ON leads (campaign_id, status);
                CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads (phone);
                CREATE INDEX IF NOT EXISTS idx_bookings_phone ON bookings (caller_phone);
            """)

            # ── 3. Safe migrations (ALTER IF NOT EXISTS) ──────────────────────
            cur.execute("""
                -- call_logs new columns
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS audio_codec TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS stt_provider TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS tts_provider TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS estimated_cost_usd NUMERIC(10,5);
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_hour INTEGER;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_day_of_week TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS interrupt_count INTEGER DEFAULT 0;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_name TEXT;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS campaign_id INTEGER;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS lead_id UUID;
                ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS room_id TEXT;

                -- sip_trunks columns
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS sip_uri TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS username TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS password TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS caller_id_number TEXT;
                ALTER TABLE sip_trunks ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

                -- campaigns new columns
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS phone_numbers TEXT DEFAULT '';
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sip_trunk_id INTEGER;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_concurrent_calls INTEGER DEFAULT 5;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS notes TEXT;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS agent_id UUID;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS calls_per_minute INTEGER DEFAULT 5;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS retry_failed BOOLEAN DEFAULT TRUE;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 2;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
                ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

                -- agents new columns
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS stt_provider TEXT DEFAULT 'sarvam';
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_provider TEXT DEFAULT 'openai';
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_model TEXT DEFAULT 'gpt-4o-mini';
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS tts_provider TEXT DEFAULT 'sarvam';
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_prompt TEXT;
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_turns INTEGER DEFAULT 20;
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS tts_language TEXT DEFAULT 'hi-IN';

                -- demo_links
                ALTER TABLE demo_links ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'auto';
            """)

            conn.commit()
    logger.info("[DB] Tables and schema initialized successfully")


# ══════════════════════════════════════════════════════════════════════════════
# SIP TRUNKS
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# AGENTS
# ══════════════════════════════════════════════════════════════════════════════

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


def get_agent_by_id(agent_id: str) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM agents WHERE id = %s", (agent_id,))
                res = cur.fetchone()
                return dict(res) if res else None
    except Exception as e:
        logger.error(f"[DB] get_agent_by_id failed: {e}")
        return None


def create_agent(agent_id, name,
                 stt_provider="sarvam", stt_language="hi-IN",
                 llm_provider="openai", llm_model="gpt-4o-mini",
                 tts_provider="sarvam", tts_voice="rohan", tts_language="hi-IN",
                 first_line="", system_prompt="", agent_instructions="",
                 max_turns=20) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO agents (
                    id, name,
                    stt_provider, stt_language,
                    llm_provider, llm_model,
                    tts_provider, tts_voice, tts_language,
                    first_line, system_prompt, agent_instructions, max_turns
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                agent_id, name,
                stt_provider, stt_language,
                llm_provider, llm_model,
                tts_provider, tts_voice, tts_language,
                first_line, system_prompt, agent_instructions, max_turns
            ))
            conn.commit()
            return dict(cur.fetchone())


def update_agent(agent_id, data: dict) -> bool:
    allowed_fields = [
        "name", "stt_provider", "stt_language",
        "llm_provider", "llm_model",
        "tts_provider", "tts_voice", "tts_language",
        "first_line", "system_prompt", "agent_instructions",
        "max_turns"
    ]
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
                cur.execute(
                    f"UPDATE agents SET {', '.join(updates)} WHERE id = %s",
                    tuple(values)
                )
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


# ══════════════════════════════════════════════════════════════════════════════
# CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════════════

def get_campaigns() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT c.*,
                           a.name AS agent_name,
                           s.name AS trunk_name
                    FROM campaigns c
                    LEFT JOIN agents a ON c.agent_id = a.id
                    LEFT JOIN sip_trunks s ON c.sip_trunk_id = s.id
                    ORDER BY c.created_at DESC
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] get_campaigns failed: {e}")
        return []


def get_campaign_full(campaign_id) -> dict:
    """Single campaign with agent + trunk joined."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT c.*,
                           a.name AS agent_name,
                           a.stt_provider, a.stt_language,
                           a.llm_provider, a.llm_model,
                           a.tts_provider, a.tts_voice, a.tts_language,
                           a.first_line, a.system_prompt, a.agent_instructions,
                           s.name AS trunk_name,
                           s.sip_uri AS trunk_sip_uri
                    FROM campaigns c
                    LEFT JOIN agents a ON c.agent_id = a.id
                    LEFT JOIN sip_trunks s ON c.sip_trunk_id = s.id
                    WHERE c.id = %s
                """, (campaign_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] get_campaign_full failed: {e}")
        return None


def create_campaign(name, phone_numbers="", sip_trunk_id=None, max_concurrent_calls=5,
                    notes=None, agent_id=None, calls_per_minute=5,
                    retry_failed=True, max_retries=2) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO campaigns (
                        name, phone_numbers, sip_trunk_id, max_concurrent_calls, notes,
                        agent_id, calls_per_minute, retry_failed, max_retries
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, (
                    name, phone_numbers, sip_trunk_id, max_concurrent_calls, notes,
                    agent_id, calls_per_minute, retry_failed, max_retries
                ))
                conn.commit()
                return dict(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] create_campaign failed: {e}")
        return {}


def update_campaign_status(campaign_id, status: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if status == "active":
                    cur.execute(
                        "UPDATE campaigns SET status = %s, started_at = NOW() WHERE id = %s",
                        (status, campaign_id)
                    )
                elif status == "completed":
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
                return True
    except Exception as e:
        logger.error(f"[DB] update_campaign_status failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# LEADS
# ══════════════════════════════════════════════════════════════════════════════

def create_lead(campaign_id, phone: str, name: str = "",
                email: str = "", custom_data: dict = None) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                lead_id = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO leads (id, campaign_id, phone, name, email, custom_data, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                    RETURNING *
                """, (lead_id, campaign_id, phone, name, email,
                      Json(custom_data or {})))
                conn.commit()
                return dict(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] create_lead failed: {e}")
        return {}


def get_leads(campaign_id, status: str = None, limit: int = 500) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status:
                    cur.execute(
                        "SELECT * FROM leads WHERE campaign_id = %s AND status = %s "
                        "ORDER BY created_at ASC LIMIT %s",
                        (campaign_id, status, limit)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM leads WHERE campaign_id = %s "
                        "ORDER BY created_at ASC LIMIT %s",
                        (campaign_id, limit)
                    )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] get_leads failed: {e}")
        return []


def get_pending_leads(campaign_id, limit: int = 1) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM leads
                    WHERE campaign_id = %s AND status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT %s
                """, (campaign_id, limit))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] get_pending_leads failed: {e}")
        return []


def update_lead_status(lead_id: str, status: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if status == "calling":
                    cur.execute("""
                        UPDATE leads
                        SET status = %s,
                            call_attempts = call_attempts + 1,
                            last_call_at = NOW()
                        WHERE id = %s
                    """, (status, lead_id))
                else:
                    cur.execute(
                        "UPDATE leads SET status = %s WHERE id = %s",
                        (status, lead_id)
                    )
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] update_lead_status failed: {e}")
        return False


def requeue_failed_leads(campaign_id) -> int:
    """Reset failed leads back to pending for retry."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE leads
                    SET status = 'pending'
                    WHERE campaign_id = %s
                      AND status = 'failed'
                      AND call_attempts < (
                          SELECT max_retries FROM campaigns WHERE id = %s
                      )
                """, (campaign_id, campaign_id))
                count = cur.rowcount
                conn.commit()
                return count
    except Exception as e:
        logger.error(f"[DB] requeue_failed_leads failed: {e}")
        return 0


def get_leads_stats(campaign_id) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                        COUNT(*) FILTER (WHERE status = 'calling') AS calling,
                        COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                        COUNT(*) FILTER (WHERE status = 'failed') AS failed
                    FROM leads
                    WHERE campaign_id = %s
                """, (campaign_id,))
                row = cur.fetchone()
                return {
                    "total": row[0],
                    "pending": row[1],
                    "calling": row[2],
                    "completed": row[3],
                    "failed": row[4],
                }
    except Exception as e:
        logger.error(f"[DB] get_leads_stats failed: {e}")
        return {"total": 0, "pending": 0, "calling": 0, "completed": 0, "failed": 0}


# ══════════════════════════════════════════════════════════════════════════════
# BOOKINGS
# ══════════════════════════════════════════════════════════════════════════════

def save_booking(call_room_id: str, caller_name: str, caller_phone: str,
                 caller_email: str = "", start_time: str = "", notes: str = "") -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bookings
                        (id, call_room_id, caller_name, caller_phone, caller_email, start_time, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    str(uuid.uuid4()), call_room_id, caller_name,
                    caller_phone, caller_email, start_time, notes
                ))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] save_booking failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# CALL LOGS
# ══════════════════════════════════════════════════════════════════════════════

def save_call_log(
    phone, duration, transcript, summary,
    recording_url=None, sentiment=None,
    estimated_cost_usd=None, call_date=None,
    call_hour=None, call_day_of_week=None,
    was_booked=False, interrupt_count=0,
    stt_provider=None, tts_provider=None,
    audio_codec=None, caller_name=None,
    campaign_id=None, lead_id=None, room_id=None,
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
                        stt_provider, tts_provider, audio_codec, caller_name,
                        campaign_id, lead_id, room_id
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                """, (
                    phone, duration, transcript, summary,
                    recording_url, sentiment, estimated_cost_usd,
                    call_date, call_hour, call_day_of_week,
                    was_booked, interrupt_count,
                    stt_provider, tts_provider, audio_codec, caller_name,
                    campaign_id, lead_id, room_id,
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
                    INSERT INTO call_transcripts (call_room_id, phone, role, content)
                    VALUES (%s, %s, %s, %s)
                """, (call_room_id, phone, role, content))
                conn.commit()
    except Exception as e:
        logger.warning(f"[DB] Transcript line failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# DNC
# ══════════════════════════════════════════════════════════════════════════════

def is_in_dnc(phone: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM call_dnc WHERE phone = %s", (phone,))
                return bool(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] Failed to check DNC for {phone}: {e}")
        return False


def add_to_dnc(phone: str, reason: str = None) -> bool:
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
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM call_dnc WHERE phone = %s", (phone,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"[DB] Failed to remove {phone} from DNC: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS / REPORTING
# ══════════════════════════════════════════════════════════════════════════════

def fetch_call_logs(limit: int = 50) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM call_logs ORDER BY created_at DESC LIMIT %s",
                    (limit,)
                )
                rows = cur.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
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
