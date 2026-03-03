"""
campaign_scheduler.py
Background scheduler that drives mass outbound campaigns.
Respects time windows, concurrency limits, anti-spam per-CLI caps, DNC.
Uses PostgreSQL directly via psycopg2 (no Supabase SDK required).
"""
import os
import json
import asyncio
import logging
from datetime import datetime, time as dtime

import pytz
from db import get_conn

logger = logging.getLogger("campaign-scheduler")

SCHEDULER_INTERVAL = 10  # seconds between scheduler ticks


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def in_call_window(start_str: str, end_str: str, tz_str: str) -> bool:
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz).time()
    try:
        start = dtime.fromisoformat(str(start_str)[:5])
        end   = dtime.fromisoformat(str(end_str)[:5])
    except Exception:
        start, end = dtime(9, 30), dtime(19, 30)
    return start <= now <= end


def count_today_calls_for_cli(cli: str) -> int:
    today = datetime.utcnow().date().isoformat()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM call_logs WHERE cli_used = %s AND created_at >= %s",
                    (cli, f"{today}T00:00:00")
                )
                return cur.fetchone()[0]
    except Exception as e:
        logger.warning(f"[SCHED] count_today_calls_for_cli error: {e}")
        return 0


def is_dnc(phone: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM dnc_list WHERE phone = %s", (phone,))
                return cur.fetchone() is not None
    except Exception as e:
        logger.warning(f"[SCHED] is_dnc error: {e}")
        return False


def pick_cli(number_pool: list, max_per_day: int) -> str | None:
    """Return a CLI from the pool that hasn't hit its daily limit."""
    for num in number_pool:
        if count_today_calls_for_cli(num) < max_per_day:
            return num
    return None


def get_active_calls_count(campaign_id: str) -> int:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = %s AND status = 'calling'",
                    (campaign_id,)
                )
                return cur.fetchone()[0]
    except Exception as e:
        logger.warning(f"[SCHED] get_active_calls_count error: {e}")
        return 0


def get_eligible_leads(campaign_id: str, limit: int) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, phone, name, email, custom_data, attempts
                    FROM campaign_leads
                    WHERE campaign_id = %s
                      AND status IN ('pending', 'retry')
                      AND attempts <= 3
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (campaign_id, limit)
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[SCHED] get_eligible_leads error: {e}")
        return []


def mark_lead(lead_id: str, status: str, **kwargs):
    try:
        sets = ["status = %s", "last_attempt_at = %s"]
        vals = [status, datetime.utcnow().isoformat()]
        for k, v in kwargs.items():
            sets.append(f"{k} = %s")
            vals.append(v)
        vals.append(lead_id)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE campaign_leads SET {', '.join(sets)} WHERE id = %s",
                    vals
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"[SCHED] mark_lead error: {e}")


def increment_campaign_counters(campaign_id: str, was_answered: bool, was_booked: bool):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE campaigns SET
                        called_count   = called_count + 1,
                        answered_count = answered_count + %s,
                        booked_count   = booked_count   + %s
                    WHERE id = %s
                    """,
                    (1 if was_answered else 0, 1 if was_booked else 0, campaign_id)
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"[SCHED] increment_campaign_counters error: {e}")


def get_active_campaigns() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.*, 
                           row_to_json(vac) as voice_agent_config,
                           row_to_json(st) as sip_trunk
                    FROM campaigns c
                    LEFT JOIN voice_agent_configs vac ON c.agent_config_id = vac.id
                    LEFT JOIN sip_trunks st ON c.sip_trunk_id = st.id
                    WHERE c.status = 'active'
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    # Parse JSONB columns
                    if isinstance(d.get("voice_agent_config"), str):
                        import json as _json
                        d["voice_agent_config"] = _json.loads(d["voice_agent_config"])
                    if isinstance(d.get("sip_trunk"), str):
                        import json as _json
                        d["sip_trunk"] = _json.loads(d["sip_trunk"])
                    rows.append(d)
                return rows
    except Exception as e:
        logger.warning(f"[SCHED] get_active_campaigns error: {e}")
        return []


def has_pending_leads(campaign_id: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = %s AND status IN ('pending', 'retry')",
                    (campaign_id,)
                )
                return (cur.fetchone()[0] or 0) > 0
    except Exception as e:
        logger.warning(f"[SCHED] has_pending_leads error: {e}")
        return False


def mark_campaign_completed(campaign_id: str):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE campaigns SET status = 'completed', completed_at = %s WHERE id = %s",
                    (datetime.utcnow().isoformat(), campaign_id)
                )
                conn.commit()
        logger.info(f"[SCHED] Campaign {campaign_id} marked completed")
    except Exception as e:
        logger.warning(f"[SCHED] mark_campaign_completed error: {e}")


def log_call_for_cli(phone: str, campaign_id: str, cli: str):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO call_logs (phone, call_type, campaign_id, cli_used, duration)
                    VALUES (%s, 'outbound', %s, %s, 0)
                    """,
                    (phone, campaign_id, cli or "")
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"[SCHED] log_call_for_cli error: {e}")


# ─── DISPATCH ────────────────────────────────────────────────────────────────

async def dispatch_lead(lead: dict, campaign: dict, config: dict, trunk: dict):
    phone = lead["phone"]
    ts    = int(datetime.utcnow().timestamp())
    room  = f"camp-{str(campaign['id'])[:8]}-{phone.replace('+','')[-8:]}-{ts}"

    metadata = json.dumps({
        "phone_number":              phone,
        "name":                      lead.get("name", ""),
        "campaign_id":               str(campaign["id"]),
        "call_type":                 config.get("preset_type", "custom") if config else "custom",
        "tts_voice":                 config.get("tts_voice", "rohan") if config else "rohan",
        "tts_language":              config.get("tts_language", "hi-IN") if config else "hi-IN",
        "stt_language":              config.get("stt_language", "hi-IN") if config else "hi-IN",
        "llm_model":                 config.get("llm_model", "gpt-4o-mini") if config else "gpt-4o-mini",
        "agent_instructions":        config.get("agent_instructions", "") if config else "",
        "first_line":                config.get("first_line", "") if config else "",
        "max_call_duration_seconds": config.get("max_call_duration_seconds", 300) if config else 300,
    })

    from livekit import api as lk_api

    lkapi = lk_api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )

    try:
        # 1. Start agent in room
        await lkapi.agent_dispatch.create_dispatch(
            lk_api.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room,
                metadata=metadata,
            )
        )

        # 2. Pick CLI
        number_pool = []
        if trunk and trunk.get("number_pool"):
            raw_pool = trunk["number_pool"]
            if isinstance(raw_pool, list):
                number_pool = raw_pool
            elif isinstance(raw_pool, str):
                try:
                    number_pool = json.loads(raw_pool)
                except Exception:
                    number_pool = []

        max_per_day = int(trunk.get("max_calls_per_number_per_day") or 150) if trunk else 150
        cli = pick_cli(number_pool, max_per_day) or (number_pool[0] if number_pool else None)

        lk_trunk_id = trunk.get("livekit_trunk_id") if trunk else None
        if not lk_trunk_id:
            logger.error(f"[SCHED] No livekit_trunk_id for trunk")
            mark_lead(str(lead["id"]), "failed",
                      last_result="No trunk ID configured",
                      attempts=int(lead.get("attempts", 0)) + 1)
            return

        # 3. SIP dial
        from sip_manager import dispatch_outbound_call
        await dispatch_outbound_call(
            phone=phone,
            room_name=room,
            trunk_id=lk_trunk_id,
            cli=cli,
            participant_name=lead.get("name", "Caller"),
        )

        mark_lead(str(lead["id"]), "calling",
                  livekit_room_id=room,
                  attempts=int(lead.get("attempts", 0)) + 1)
        logger.info(f"[SCHED] Dispatched {phone} → {room}")

        log_call_for_cli(phone, str(campaign["id"]), cli or "")

    except Exception as e:
        logger.error(f"[SCHED] Dispatch failed for {phone}: {e}")
        mark_lead(str(lead["id"]), "failed",
                  last_result=str(e),
                  attempts=int(lead.get("attempts", 0)) + 1)
    finally:
        await lkapi.aclose()


# ─── SCHEDULER LOOP ──────────────────────────────────────────────────────────

async def scheduler_loop():
    logger.info("[SCHED] Campaign scheduler loop started")

    while True:
        try:
            campaigns = get_active_campaigns()

            for campaign in campaigns:
                config = campaign.get("voice_agent_config") or {}
                trunk  = campaign.get("sip_trunk") or {}

                # Time window check
                if not in_call_window(
                    campaign.get("daily_start_time", "09:30"),
                    campaign.get("daily_end_time",   "19:30"),
                    campaign.get("timezone",          "Asia/Kolkata"),
                ):
                    continue

                # Concurrency check
                active = get_active_calls_count(str(campaign["id"]))
                slots  = max(0, int(campaign.get("max_calls_per_minute") or 5) - active)
                if slots <= 0:
                    continue

                # Get eligible leads
                leads = get_eligible_leads(str(campaign["id"]), limit=slots)
                if not leads:
                    if not has_pending_leads(str(campaign["id"])):
                        mark_campaign_completed(str(campaign["id"]))
                    continue

                # Dispatch each eligible lead
                interval = 60.0 / max(int(campaign.get("max_calls_per_minute") or 5), 1)
                for lead in leads:
                    if is_dnc(lead["phone"]):
                        mark_lead(str(lead["id"]), "dnc")
                        continue
                    await dispatch_lead(lead, campaign, config, trunk)
                    await asyncio.sleep(interval)

        except Exception as e:
            logger.error(f"[SCHED] Loop error: {e}")

        await asyncio.sleep(SCHEDULER_INTERVAL)


def start_campaign_scheduler():
    """Call this from your FastAPI startup event."""
    asyncio.create_task(scheduler_loop())
