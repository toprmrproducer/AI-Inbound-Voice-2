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
                CREATE INDEX IF NOT EXISTS idx_call_transcripts_room
                    ON call_transcripts (call_room_id);
                CREATE INDEX IF NOT EXISTS idx_call_logs_phone
                    ON call_logs (phone);
                CREATE INDEX IF NOT EXISTS idx_call_logs_created
                    ON call_logs (created_at);
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
