import os
import time
import logging
from typing import Optional
from supabase import create_client, Client

logger = logging.getLogger("db")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ─── Retry logic for transient SSL/network errors ────────────────────────────
_MAX_RETRIES = 3
_RETRY_DELAYS = [1.0, 2.0, 4.0]

def _is_retryable(err_str: str) -> bool:
    transient = ("525", "ssl", "timeout", "connection", "network", "502", "503", "504")
    return any(k in err_str.lower() for k in transient)

def _is_schema_error(err_str: str) -> bool:
    return "PGRST204" in err_str or "schema cache" in err_str.lower()

# ─── Supabase client ──────────────────────────────────────────────────────────
_client_instance = None
def get_supabase() -> Optional[Client]:
    global _client_instance
    if _client_instance is not None:
        return _client_instance
        
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        _client_instance = create_client(url, key)
        return _client_instance
    except Exception as e:
        logger.error(f"Failed to init Supabase client: {e}")
        return None

# For backward compatibility internally if some modules still call get_client
def get_client() -> Optional[Client]:
    return get_supabase()

# ─── Init function (call on startup) ─────────────────────────────────────────
def init_db():
    """Test Supabase connection on startup"""
    sb = get_supabase()
    if sb:
        logger.info("[DB] Supabase client initialized successfully")
    else:
        logger.warning("[DB] Supabase not configured - running in local mode")

# ─── save_call_log (OG implementation with retry logic) ──────────────────────
def save_call_log(
    phone: str,
    duration: int,
    transcript: str,
    summary: str = "",
    recording_url: str = "",
    caller_name: str = "",
    sentiment: str = "unknown",
    estimated_cost_usd: Optional[float] = None,
    call_date: Optional[str] = None,
    call_hour: Optional[int] = None,
    call_day_of_week: Optional[str] = None,
    was_booked: bool = False,
    interrupt_count: int = 0,
    audio_codec: str = "",
) -> dict:
    supabase = get_supabase()
    if not supabase:
        logger.info(f"Supabase not configured. Local log → {phone} {duration}s")
        return {"success": False, "message": "Supabase not configured"}

    full_data = {
        "phone_number":        phone,
        "duration_seconds":    duration,
        "transcript":          transcript,
        "summary":             summary,
        "sentiment":           sentiment,
        "was_booked":          was_booked,
        "interrupt_count":     interrupt_count,
        "audio_codec":         audio_codec,
    }
    if recording_url:               full_data["recording_url"]       = recording_url
    if caller_name:                 full_data["caller_name"]         = caller_name
    if estimated_cost_usd is not None: full_data["estimated_cost_usd"] = estimated_cost_usd
    if call_date:                   full_data["call_date"]           = call_date
    if call_hour is not None:       full_data["call_hour"]           = call_hour
    if call_day_of_week:            full_data["call_day_of_week"]    = call_day_of_week

    # Retry logic with schema fallback
    for attempt in range(_MAX_RETRIES):
        try:
            res = supabase.table("call_logs").insert(full_data).execute()
            logger.info(f"[DB] Call log saved for {phone}")
            return {"success": True, "data": res.data}
        except Exception as e:
            err = str(e)
            if _is_schema_error(err):
                # Migration not run - strip analytics columns and retry
                base_data = {k: v for k, v in full_data.items() if k in {
                    "phone_number", "duration_seconds", "transcript", "summary",
                    "recording_url", "caller_name"
                }}
                logger.warning("Analytics columns missing - using base schema")
                try:
                    res = supabase.table("call_logs").insert(base_data).execute()
                    return {"success": True, "data": res.data}
                except Exception as e2:
                    logger.error(f"Base schema insert failed: {e2}")
                    return {"success": False, "message": str(e2)}
            if _is_retryable(err) and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error(f"Failed to save call log: {e}")
            return {"success": False, "message": err}
    return {"success": False, "message": "Max retries exceeded"}

# ─── fetch_call_logs ──────────────────────────────────────────────────────────
def fetch_call_logs(limit: int = 100) -> list:
    supabase = get_supabase()
    if not supabase:
        return []
    for attempt in range(_MAX_RETRIES):
        try:
            res = (supabase.table("call_logs")
                   .select("*")
                   .order("created_at", desc=True)
                   .limit(limit)
                   .execute())
            return res.data or []
        except Exception as e:
            if _is_retryable(str(e)) and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error(f"Failed to fetch call logs: {e}")
            return []
    return []

def get_all_call_logs() -> list[dict]:
    return fetch_call_logs(limit=200)

# ─── fetch_bookings ───────────────────────────────────────────────────────────
def fetch_bookings() -> list:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        res = (supabase.table("call_logs")
               .select("id, phone_number, summary, created_at, caller_name")
               .ilike("summary", "%Confirmed%")
               .order("created_at", desc=True)
               .limit(200)
               .execute())
        return res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch bookings: {e}")
        return []

# ─── Agents table (UI additions) ──────────────────────────────────────────────
def list_agents() -> list:
    sb = get_supabase()
    if not sb:
        return []
    try:
        res = sb.table("agents").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        return []

def get_agent_by_id(agent_id: str) -> Optional[dict]:
    sb = get_supabase()
    if not sb:
        return None
    try:
        res = sb.table("agents").select("*").eq("id", agent_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Failed to get agent: {e}")
        return None

def create_agent(data: dict) -> dict:
    sb = get_supabase()
    if not sb:
        return {}
    try:
        res = sb.table("agents").insert(data).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        return {}

def update_agent(agent_id: str, data: dict) -> dict:
    sb = get_supabase()
    if not sb:
        return {}
    try:
        res = sb.table("agents").update(data).eq("id", agent_id).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to update agent: {e}")
        return {}

def delete_agent(agent_id: str):
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("agents").delete().eq("id", agent_id).execute()
    except Exception as e:
        logger.error(f"Failed to delete agent: {e}")

def get_inbound_active_agent() -> Optional[dict]:
    sb = get_supabase()
    if not sb:
        return None
    try:
        res = sb.table("agents").select("*").eq("is_inbound_active", True).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Failed to get active inbound agent: {e}")
        return None

def get_outbound_active_agent() -> Optional[dict]:
    sb = get_supabase()
    if not sb:
        return None
    try:
        res = sb.table("agents").select("*").eq("is_outbound_active", True).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Failed to get active outbound agent: {e}")
        return None

def set_active_agent(agent_id: str, mode: str = "inbound"):
    sb = get_supabase()
    if not sb:
        return
    try:
        key = "is_inbound_active" if mode == "inbound" else "is_outbound_active"
        # Reset all
        sb.table("agents").update({key: False}).neq("id", "00000000-0000-0000-0000-000000000000").execute()
        # Set target
        sb.table("agents").update({key: True}).eq("id", agent_id).execute()
    except Exception as e:
        logger.error(f"Failed to set active agent: {e}")
        raise e

# ─── Campaigns Table ──────────────────────────────────────────────────────────
def get_campaign_stats(campaign_id: str) -> dict:
    try:
        res = get_supabase().table('campaigns').select('*').eq('id', campaign_id).limit(1).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"[DB] Error fetching campaign stats: {e}")
        return {}

def get_campaigns() -> list[dict]:
    try:
        res = get_supabase().table('campaigns').select('*').order('created_at', desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"[DB] Error fetching campaigns: {e}")
        return []

def get_campaign_full(campaign_id: int) -> Optional[dict]:
    try:
        res = get_supabase().table('campaigns').select('*').eq('id', campaign_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"[DB] Error fetching full campaign: {e}")
        return None

def create_campaign(name, phone_numbers, sip_trunk_id, max_concurrent_calls, notes, agent_id, calls_per_minute, retry_failed, max_retries):
    try:
        data = {
            'name': name,
            'phone_numbers': phone_numbers,
            'sip_trunk_id': sip_trunk_id,
            'max_concurrent_calls': max_concurrent_calls,
            'notes': notes,
            'agent_id': agent_id,
            'calls_per_minute': calls_per_minute,
            'retry_failed': retry_failed,
            'max_retries': max_retries,
            'status': 'draft',
        }
        res = get_supabase().table('campaigns').insert(data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"[DB] Error creating campaign: {e}")
        return None

def update_campaign_status(campaign_id: int, status: str) -> bool:
    try:
        res = get_supabase().table('campaigns').update({'status': status}).eq('id', campaign_id).execute()
        return bool(res.data)
    except Exception as e:
        logger.error(f"[DB] Error updating campaign status: {e}")
        return False

# ─── Campaign Numbers (Leads) ──────────────────────────────────────────────────
def create_lead(campaign_id: int, phone: str, name: str, email: str, custom_data: dict) -> dict:
    try:
        data = {
            'campaign_id': campaign_id,
            'phone': phone,
            'name': name,
            'email': email,
            'custom_data': custom_data,
            'status': 'pending'
        }
        res = get_supabase().table('campaign_numbers').insert(data).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"[DB] Error creating lead: {e}")
        raise e

def get_leads(campaign_id: int, status: Optional[str] = None) -> list[dict]:
    try:
        q = get_supabase().table('campaign_numbers').select('*').eq('campaign_id', campaign_id)
        if status:
            q = q.eq('status', status)
        res = q.order('created_at', desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"[DB] Error fetching leads: {e}")
        return []

def get_leads_stats(campaign_id: int) -> dict:
    try:
        res = get_supabase().table('campaign_numbers').select('status').eq('campaign_id', campaign_id).execute()
        stats = {'total': 0, 'pending': 0, 'calling': 0, 'completed': 0, 'failed': 0}
        if res.data:
            stats['total'] = len(res.data)
            for row in res.data:
                st = row.get('status', 'pending')
                stats[st] = stats.get(st, 0) + 1
        return stats
    except Exception as e:
        logger.error(f"[DB] Error fetching leads stats: {e}")
        return {'total': 0, 'pending': 0, 'calling': 0, 'completed': 0, 'failed': 0}
