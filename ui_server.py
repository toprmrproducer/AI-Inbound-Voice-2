import json
import logging
import os
import asyncio
import uuid
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, Response
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(title="Med Spa AI Dashboard")

# ── #22 Health check endpoint ─────────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "service": "inbound-voice-agent"}

# ── #40 Prometheus metrics endpoint ──────────────────────────────────────────
try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    _calls_total   = Counter("voice_calls_total",  "Total calls handled")
    _calls_booked  = Counter("voice_calls_booked_total", "Calls that resulted in a booking")
    _call_duration = Histogram("voice_call_duration_seconds", "Call duration in seconds",
                               buckets=[10, 30, 60, 120, 300, 600])
    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/internal/record-call")
    async def record_call(request: Request):
        data = await request.json()
        _calls_total.inc()
        if data.get("booked"):
            _calls_booked.inc()
        if data.get("duration"):
            _call_duration.observe(data["duration"])
        return {"ok": True}
except ImportError:
    pass  # prometheus_client not installed — metrics endpoint skipped

AGENTS_FILE = "agents.json"
DEMO_FILE = "demo_links.json"

# In-memory bulk campaign tracker
bulk_campaigns: dict = {}

def read_json_file(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def write_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

CONFIG_FILE = "config.json"

def read_config():
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

    def get_val(key, env_key, default=""):
        return config.get(key) if config.get(key) else os.getenv(env_key, default)

    return {
        "first_line": get_val("first_line", "FIRST_LINE", "Namaste! Welcome to Daisy's Med Spa. Main aapki kaise madad kar sakti hoon? I can answer questions about our treatments or help you book an appointment."),
        "agent_instructions": get_val("agent_instructions", "AGENT_INSTRUCTIONS", ""),
        "stt_min_endpointing_delay": float(get_val("stt_min_endpointing_delay", "STT_MIN_ENDPOINTING_DELAY", 0.6)),
        "llm_model": get_val("llm_model", "LLM_MODEL", "gpt-4o-mini"),
        "tts_voice": get_val("tts_voice", "TTS_VOICE", "kavya"),
        "tts_language": get_val("tts_language", "TTS_LANGUAGE", "hi-IN"),
        "stt_language": get_val("stt_language", "STT_LANGUAGE", "hi-IN"),
        "livekit_url": get_val("livekit_url", "LIVEKIT_URL", ""),
        "sip_trunk_id": get_val("sip_trunk_id", "SIP_TRUNK_ID", ""),
        "livekit_api_key": get_val("livekit_api_key", "LIVEKIT_API_KEY", ""),
        "livekit_api_secret": get_val("livekit_api_secret", "LIVEKIT_API_SECRET", ""),
        "openai_api_key": get_val("openai_api_key", "OPENAI_API_KEY", ""),
        "sarvam_api_key": get_val("sarvam_api_key", "SARVAM_API_KEY", ""),
        "cal_api_key": get_val("cal_api_key", "CAL_API_KEY", ""),
        "cal_event_type_id": get_val("cal_event_type_id", "CAL_EVENT_TYPE_ID", ""),
        "telegram_bot_token": get_val("telegram_bot_token", "TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": get_val("telegram_chat_id", "TELEGRAM_CHAT_ID", ""),
        "supabase_url": get_val("supabase_url", "SUPABASE_URL", ""),
        "supabase_key": get_val("supabase_key", "SUPABASE_KEY", ""),
        **config
    }

def write_config(data):
    config = read_config()
    config.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/config")
async def api_get_config():
    return read_config()

@app.post("/api/config")
async def api_post_config(request: Request):
    data = await request.json()
    write_config(data)
    logger.info("Configuration updated via UI.")
    return {"status": "success"}

@app.get("/api/logs")
async def api_get_logs():
    import db
    try:
        logs = db.fetch_call_logs(limit=50)
        return logs
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return []

@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    import db
    from psycopg2.extras import RealDictCursor
    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM call_logs WHERE id = %s", (log_id,))
                data = dict(cur.fetchone() or {})
        if not data:
            return PlainTextResponse(content="Log not found", status_code=404)
        text = f"Call Log — {data.get('created_at', '')}\n"
        text += f"Phone: {data.get('phone', 'Unknown')}\n"
        text += f"Duration: {data.get('duration', 0)}s\n"
        text += f"Summary: {data.get('summary', '')}\n\n"
        text += "--- TRANSCRIPT ---\n"
        text += data.get("transcript", "No transcript available.")
        return PlainTextResponse(content=text, media_type="text/plain",
                                 headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"})
    except Exception as e:
        return PlainTextResponse(content=f"Error: {e}", status_code=500)

@app.get("/api/bookings")
async def api_get_bookings():
    import db
    try:
        # fetch_bookings removed — filter call_logs by was_booked
        rows = db.fetch_call_logs(limit=200)
        return [r for r in rows if r.get("was_booked")]
    except Exception as e:
        logger.error(f"Error fetching bookings: {e}")
        return []

@app.get("/api/stats")
async def api_get_stats():
    import db
    try:
        stats = db.fetch_stats()
        stats["db_error"] = None
        return stats
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {
            "total_calls": None, "total_bookings": None,
            "avg_duration": None, "booking_rate": None,
            "db_error": str(e),
        }

@app.get("/api/db-status")
async def api_db_status():
    """Quick health-check for the PostgreSQL connection."""
    import db
    try:
        url = db._get_db_url()
        # Try a trivial query
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        # Redact password from URL before returning
        import re
        safe_url = re.sub(r"(:)[^@]+(@)", r"\1***\2", url)
        return {"connected": True, "url": safe_url}
    except Exception as e:
        return {"connected": False, "error": str(e)}

@app.get("/api/contacts")
async def api_get_contacts():
    """CRM endpoint — groups call_logs by phone number, deduplicates into contacts."""
    import db
    try:
        rows = db.fetch_call_logs(limit=500)

        # Deduplicate by phone number
        contacts: dict = {}
        for r in rows:
            phone = r.get("phone") or r.get("phone_number") or "unknown"
            if phone not in contacts:
                contacts[phone] = {
                    "phone_number": phone,
                    "caller_name": r.get("caller_name") or "",
                    "total_calls": 0,
                    "last_seen": str(r.get("created_at", "")),
                    "is_booked": False,
                }
            c = contacts[phone]
            c["total_calls"] += 1
            if not c["caller_name"] and r.get("caller_name"):
                c["caller_name"] = r["caller_name"]
            if r.get("summary") and "Confirmed" in r.get("summary", ""):
                c["is_booked"] = True

        return sorted(contacts.values(), key=lambda x: x["last_seen"] or "", reverse=True)
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")
        return []

# ── Outbound Call Endpoints ───────────────────────────────────────────────────

@app.post("/api/call/outbound")
async def api_outbound_call(request: Request):
    data = await request.json()
    phone_number = data.get("phone_number", "").strip()
    if not phone_number or not phone_number.startswith("+"):
        raise HTTPException(400, "Phone number must start with + and include country code")
    config = read_config()
    url = config.get("livekit_url") or os.getenv("LIVEKIT_URL", "")
    api_key = config.get("livekit_api_key") or os.getenv("LIVEKIT_API_KEY", "")
    api_secret = config.get("livekit_api_secret") or os.getenv("LIVEKIT_API_SECRET", "")
    if not (url and api_key and api_secret):
        raise HTTPException(400, "LiveKit credentials not configured")
    try:
        import random
        from livekit import api as lk_api_mod
        lk = lk_api_mod.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
        room = f"call-{phone_number.replace('+','')}-{random.randint(1000,9999)}"
        dispatch = await lk.agent_dispatch.create_dispatch(
            lk_api_mod.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room,
                metadata=json.dumps({"phone_number": phone_number})
            )
        )
        await lk.aclose()
        return {"status": "dispatched", "dispatch_id": dispatch.id, "room": room, "phone": phone_number}
    except Exception as e:
        logger.error(f"Outbound dispatch error: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/call/bulk")
async def api_bulk_calls(request: Request):
    data = await request.json()
    numbers = [n.strip() for n in data.get("numbers", []) if n.strip()]
    if not numbers:
        raise HTTPException(400, "No phone numbers provided")
    job_id = str(uuid.uuid4())[:8]
    bulk_campaigns[job_id] = {"status": "running", "total": len(numbers), "done": 0, "results": []}
    asyncio.create_task(_run_bulk_campaign(job_id, numbers))
    return {"job_id": job_id, "total": len(numbers)}

async def _run_bulk_campaign(job_id: str, numbers: list):
    config = read_config()
    url = config.get("livekit_url") or os.getenv("LIVEKIT_URL", "")
    api_key = config.get("livekit_api_key") or os.getenv("LIVEKIT_API_KEY", "")
    api_secret = config.get("livekit_api_secret") or os.getenv("LIVEKIT_API_SECRET", "")
    for phone in numbers:
        if bulk_campaigns.get(job_id, {}).get("status") == "stopped":
            break
        result = {"phone": phone, "status": "pending"}
        try:
            import random
            from livekit import api as lk_api_mod
            lk = lk_api_mod.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
            room = f"bulk-{phone.replace('+','')}-{random.randint(1000,9999)}"
            await lk.agent_dispatch.create_dispatch(
                lk_api_mod.CreateAgentDispatchRequest(
                    agent_name="outbound-caller", room=room,
                    metadata=json.dumps({"phone_number": phone})
                )
            )
            await lk.aclose()
            result["status"] = "dispatched"
        except Exception as e:
            result["status"] = f"error: {e}"
        bulk_campaigns[job_id]["results"].append(result)
        bulk_campaigns[job_id]["done"] += 1
        await asyncio.sleep(3)
    bulk_campaigns[job_id]["status"] = "completed"

@app.get("/api/call/bulk/{job_id}")
async def api_bulk_status(job_id: str):
    if job_id not in bulk_campaigns:
        raise HTTPException(404, "Campaign not found")
    return bulk_campaigns[job_id]

@app.post("/api/call/bulk/{job_id}/stop")
async def api_bulk_stop(job_id: str):
    if job_id in bulk_campaigns:
        bulk_campaigns[job_id]["status"] = "stopped"
    return {"status": "stopped"}


# ── Telephony API Routes ──────────────────────────────────────────────────────

import csv as _csv
import io as _io

@app.on_event("startup")
async def startup_event():
    try:
        import db
        db.init_db()
        logger.info("[STARTUP] Database initialized")
    except Exception as e:
        logger.warning(f"[STARTUP] DB init error: {e}")
    try:
        from campaign_scheduler import start_campaign_scheduler
        start_campaign_scheduler()
        logger.info("[STARTUP] Campaign scheduler started")
    except Exception as e:
        logger.warning(f"[STARTUP] Campaign scheduler error: {e}")


# ── SIP Trunks ────────────────────────────────────────────────────────────────

@app.get("/api/telephony/trunks")
async def tel_list_trunks():
    import db
    return db.fetch_trunks()


@app.get("/api/telephony/trunks/livekit/sync")
async def tel_sync_livekit_trunks():
    from sip_manager import list_outbound_trunks, list_inbound_trunks
    outbound = await list_outbound_trunks()
    inbound  = await list_inbound_trunks()
    return {"outbound": outbound, "inbound": inbound}


@app.post("/api/telephony/trunks/outbound")
async def tel_create_outbound_trunk(request: Request):
    data = await request.json()
    from sip_manager import create_outbound_trunk as lk_create
    try:
        lk_id = await lk_create(
            name=data["name"],
            address=data.get("sip_address", ""),
            numbers=data.get("number_pool", []),
            username=data.get("auth_username", ""),
            password=data.get("auth_password", ""),
        )
    except Exception as e:
        raise HTTPException(500, f"LiveKit trunk creation failed: {e}")
    import db
    row = {
        "name": data["name"],
        "provider": data.get("provider", "vobiz"),
        "trunk_type": "outbound",
        "sip_address": data.get("sip_address", ""),
        "auth_username": data.get("auth_username", ""),
        "auth_password": data.get("auth_password", ""),
        "number_pool": json.dumps(data.get("number_pool", [])),
        "livekit_trunk_id": lk_id,
        "max_concurrent_calls": int(data.get("max_concurrent_calls", 10)),
        "max_calls_per_number_per_day": int(data.get("max_calls_per_number_per_day", 150)),
        "notes": data.get("notes", ""),
    }
    result = db.insert_trunk(row)
    # Convert JSONB back to list for JSON response
    if isinstance(result.get("number_pool"), str):
        try:
            result["number_pool"] = json.loads(result["number_pool"])
        except Exception:
            result["number_pool"] = []
    return result


@app.post("/api/telephony/trunks/inbound")
async def tel_create_inbound_trunk(request: Request):
    data = await request.json()
    from sip_manager import create_inbound_trunk as lk_create
    try:
        lk_id = await lk_create(
            name=data["name"],
            numbers=data.get("numbers", []),
            allowed_addresses=data.get("allowed_addresses", []),
        )
    except Exception as e:
        raise HTTPException(500, f"LiveKit inbound trunk creation failed: {e}")
    import db
    row = {
        "name": data["name"],
        "provider": data.get("provider", "vobiz"),
        "trunk_type": "inbound",
        "sip_address": "",
        "number_pool": json.dumps(data.get("numbers", [])),
        "livekit_trunk_id": lk_id,
        "notes": data.get("notes", ""),
    }
    return db.insert_trunk(row)


@app.delete("/api/telephony/trunks/{trunk_id}")
async def tel_delete_trunk(trunk_id: str):
    import db
    trunks = db.fetch_trunks()
    trunk = next((t for t in trunks if str(t.get("id")) == trunk_id), None)
    if trunk and trunk.get("livekit_trunk_id"):
        from sip_manager import delete_outbound_trunk, delete_inbound_trunk
        try:
            if trunk.get("trunk_type") == "outbound":
                await delete_outbound_trunk(trunk["livekit_trunk_id"])
            else:
                await delete_inbound_trunk(trunk["livekit_trunk_id"])
        except Exception as e:
            logger.warning(f"LiveKit trunk delete failed (ignoring): {e}")
    db.delete_trunk_by_id(trunk_id)
    return {"success": True}


# ── Agent Configs / Presets ───────────────────────────────────────────────────

@app.get("/api/telephony/presets")
async def tel_get_presets():
    from presets import CALLING_PRESETS
    return CALLING_PRESETS


@app.get("/api/telephony/agent-configs")
async def tel_list_agent_configs():
    import db
    return db.fetch_agent_configs()


@app.post("/api/telephony/agent-configs")
async def tel_create_agent_config(request: Request):
    data = await request.json()
    import db
    return db.insert_agent_config(data)


@app.post("/api/telephony/agent-configs/from-preset/{preset_type}")
async def tel_create_from_preset(preset_type: str, request: Request):
    from presets import CALLING_PRESETS
    if preset_type not in CALLING_PRESETS:
        raise HTTPException(404, f"Unknown preset: {preset_type}")
    import db
    data = {**CALLING_PRESETS[preset_type]}
    try:
        body = await request.json()
        if body.get("sip_trunk_id"):
            data["sip_trunk_id"] = body["sip_trunk_id"]
    except Exception:
        pass
    return db.insert_agent_config(data)


@app.put("/api/telephony/agent-configs/{config_id}")
async def tel_update_agent_config(config_id: str, request: Request):
    data = await request.json()
    import db
    return db.update_agent_config(config_id, data)


@app.delete("/api/telephony/agent-configs/{config_id}")
async def tel_delete_agent_config(config_id: str):
    import db
    db.soft_delete_agent_config(config_id)
    return {"success": True}


# ── Campaigns ─────────────────────────────────────────────────────────────────

@app.get("/api/telephony/campaigns")
async def tel_list_campaigns():
    import db
    return db.fetch_campaigns()


@app.post("/api/telephony/campaigns")
async def tel_create_campaign(request: Request):
    data = await request.json()
    import db
    return db.insert_campaign(data)


@app.post("/api/telephony/campaigns/{cid}/start")
async def tel_start_campaign(cid: str):
    import db
    db.update_campaign_status(cid, "active")
    return {"status": "active"}


@app.post("/api/telephony/campaigns/{cid}/pause")
async def tel_pause_campaign(cid: str):
    import db
    db.update_campaign_status(cid, "paused")
    return {"status": "paused"}


@app.post("/api/telephony/campaigns/{cid}/resume")
async def tel_resume_campaign(cid: str):
    import db
    db.update_campaign_status(cid, "active")
    return {"status": "active"}


@app.post("/api/telephony/campaigns/{cid}/cancel")
async def tel_cancel_campaign(cid: str):
    import db
    db.update_campaign_status(cid, "cancelled")
    return {"status": "cancelled"}


@app.delete("/api/telephony/campaigns/{cid}")
async def tel_delete_campaign(cid: str):
    import db
    db.delete_campaign_by_id(cid)
    return {"success": True}


# ── Leads ─────────────────────────────────────────────────────────────────────

@app.get("/api/telephony/campaigns/{cid}/leads")
async def tel_get_leads(cid: str, status: str = None, limit: int = 100, offset: int = 0):
    import db
    leads = db.fetch_campaign_leads(cid, limit=limit)
    if status:
        leads = [l for l in leads if l.get("status") == status]
    return leads


@app.post("/api/telephony/campaigns/{cid}/leads")
async def tel_add_leads(cid: str, request: Request):
    data = await request.json()
    leads = data.get("leads", [])
    import db
    added = db.insert_leads_bulk(cid, leads)
    return {"added": added}


@app.post("/api/telephony/campaigns/{cid}/leads/csv")
async def tel_upload_leads_csv(cid: str, request: Request):
    from fastapi import UploadFile, File
    body = await request.body()
    try:
        text = body.decode("utf-8")
    except Exception:
        text = body.decode("latin-1")
    reader = _csv.DictReader(_io.StringIO(text))
    leads = []
    for row in reader:
        phone = row.get("phone", "").strip()
        if not phone:
            continue
        leads.append({
            "phone":       phone,
            "name":        row.get("name", "").strip(),
            "email":       row.get("email", "").strip(),
            "custom_data": {k: v for k, v in row.items()
                            if k not in ("phone", "name", "email")},
        })
    import db
    added = db.insert_leads_bulk(cid, leads)
    return {"added": added}


@app.post("/api/telephony/campaigns/{cid}/leads/{lid}/dnc")
async def tel_lead_to_dnc(cid: str, lid: str):
    import db, psycopg2
    from psycopg2.extras import RealDictCursor
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT phone FROM campaign_leads WHERE id = %s", (lid,))
            row = cur.fetchone()
    if row:
        db.add_to_dnc(row["phone"], reason="user_request", source="campaign")
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE campaign_leads SET status = 'dnc' WHERE id = %s", (lid,))
                conn.commit()
    return {"success": True}


# ── Single Call ───────────────────────────────────────────────────────────────

@app.post("/api/telephony/call/single")
async def tel_single_call(request: Request):
    data = await request.json()
    phone = data.get("phone", "").strip()
    agent_config_id = data.get("agent_config_id", "")
    name = data.get("name", "Caller")
    if not phone:
        raise HTTPException(400, "phone is required")
    import db
    configs = db.fetch_agent_configs()
    config = next((c for c in configs if str(c.get("id")) == agent_config_id), None)
    if not config:
        raise HTTPException(404, "Agent config not found")
    trunks_list = db.fetch_trunks()
    trunk = next((t for t in trunks_list if str(t.get("id")) == str(config.get("sip_trunk_id", ""))), None)

    async def _dial():
        from campaign_scheduler import dispatch_lead as _dispatch
        fake_lead = {"id": "single", "phone": phone, "name": name, "attempts": 0}
        fake_campaign = {"id": "single", "name": "Single Call", "max_calls_per_minute": 1}
        await _dispatch(fake_lead, fake_campaign, config, trunk or {})

    asyncio.create_task(_dial())
    return {"status": "dispatching", "phone": phone}


# ── DNC ───────────────────────────────────────────────────────────────────────

@app.get("/api/telephony/dnc")
async def tel_list_dnc(limit: int = 200, offset: int = 0):
    import db
    return db.fetch_dnc(limit=limit, offset=offset)


@app.post("/api/telephony/dnc")
async def tel_add_dnc(request: Request):
    data = await request.json()
    phone = data.get("phone", "").strip()
    if not phone:
        raise HTTPException(400, "phone is required")
    import db
    db.add_to_dnc(phone, reason=data.get("reason", "manual"))
    return {"success": True}


@app.delete("/api/telephony/dnc/{phone}")
async def tel_remove_dnc(phone: str):
    import db
    db.remove_from_dnc(phone)
    return {"success": True}


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/api/telephony/analytics/overview")
async def tel_analytics_overview():
    import db
    return db.fetch_telephony_overview()


@app.get("/api/telephony/analytics/daily")
async def tel_analytics_daily(days: int = 14):
    import db
    return db.fetch_daily_analytics(days=days)


@app.get("/api/telephony/analytics/campaign/{cid}")
async def tel_campaign_analytics(cid: str):
    import db
    leads = db.fetch_campaign_leads(cid, limit=10000)
    by_status: dict = {}
    for lead in leads:
        s = lead.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "total":         len(leads),
        "by_status":     by_status,
        "total_booked":  sum(1 for l in leads if l.get("booked")),
        "avg_duration":  round(
            sum(l.get("call_duration_seconds") or 0 for l in leads) / max(len(leads), 1)
        ),
    }


# ── Demo Link Endpoints (PostgreSQL-backed) ───────────────────────────────────

import secrets, string as _string
import psycopg2
from psycopg2.extras import RealDictCursor as _RDC
from db import get_conn as _get_conn


@app.get("/api/demo/list")
async def api_demo_list():
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=_RDC) as cur:
                cur.execute("SELECT * FROM demo_links ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DEMO] list failed: {e}")
        return []

@app.post("/api/demo/create")
async def api_demo_create(request: Request):
    body = await request.json()
    label = body.get("label") or body.get("name", "Demo Link")
    slug = ''.join(secrets.choice(_string.ascii_lowercase + _string.digits) for _ in range(8))
    language = body.get("language", "auto")
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO demo_links (slug, label, language) VALUES (%s, %s, %s) RETURNING id, slug",
                    (slug, label, language)
                )
                row = cur.fetchone()
                conn.commit()
        base_url = os.getenv("PUBLIC_BASE_URL", "")
        return {"slug": row[1], "url": f"{base_url}/demo/{row[1]}", "label": label, "language": language, "token": row[1]}
    except Exception as e:
        logger.error(f"[DEMO] create failed: {e}")
        raise HTTPException(500, str(e))

@app.get("/api/demo/token/{slug}")
async def api_demo_token(slug: str):
    """Generate a LiveKit JWT for a browser visitor joining a demo room."""
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT is_active FROM demo_links WHERE slug = %s", (slug,))
                row = cur.fetchone()
                if not row or not row[0]:
                    raise HTTPException(404, "Demo link not found or inactive")
                cur.execute(
                    "UPDATE demo_links SET total_sessions = total_sessions + 1 WHERE slug = %s",
                    (slug,)
                )
                conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DEMO] token DB error: {e}")
        raise HTTPException(500, str(e))

    room_name = f"demo-{slug}-{secrets.token_hex(4)}"
    lk_api_key    = os.getenv("LIVEKIT_API_KEY", "")
    lk_api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    # Strip trailing slash to ensure clean wss:// URL for the frontend
    lk_url        = os.getenv("LIVEKIT_URL", "").rstrip("/")

    try:
        from livekit import api as lk_api
        import asyncio

        # Step 1 — Create the room & Dispatch agent
        async with lk_api.LiveKitAPI(lk_url, lk_api_key, lk_api_secret) as lk:
            await lk.room.create_room(
                lk_api.CreateRoomRequest(name=room_name)
            )
            await lk.agent_dispatch.create_dispatch(
                lk_api.CreateAgentDispatchRequest(
                    agent_name="outbound-caller",
                    room=room_name,
                    metadata="demo",
                )
            )

        # Step 2 — Generate visitor token (1-hour TTL)
        visitor_identity = f"visitor-{secrets.token_hex(4)}"
        token = (
            lk_api.AccessToken(lk_api_key, lk_api_secret)
            .with_identity(visitor_identity)
            .with_name("Demo Visitor")
            .with_grants(lk_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            ))
            .with_ttl(3600)
            .to_jwt()
        )
    except Exception as e:
        logger.error(f"[DEMO] Token/Dispatch error: {e}")
        raise HTTPException(500, f"Token generation failed: {e}")

    return {"token": token, "room": room_name, "ws_url": lk_url, "identity": visitor_identity}

@app.delete("/api/demo/{slug}")
async def api_demo_delete(slug: str):
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE demo_links SET is_active = FALSE WHERE slug = %s", (slug,))
                conn.commit()
        return {"status": "deactivated"}
    except Exception as e:
        logger.error(f"[DEMO] delete failed: {e}")
        raise HTTPException(500, str(e))

@app.get("/demo/{slug}", response_class=HTMLResponse)
async def demo_page(slug: str):
    """Serve the visitor-facing browser demo page."""
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT label, is_active FROM demo_links WHERE slug = %s", (slug,))
                row = cur.fetchone()
    except Exception:
        row = None

    if not row or not row[1]:
        return HTMLResponse(
            "<h2 style='font-family:sans-serif;text-align:center;margin-top:20vh'>"
            "This demo link is invalid or has expired.</h2>",
            status_code=404
        )

    label = (row[0] or "AI Voice Agent").replace('"', '&quot;').replace("'", "&#39;")

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{label} \u2014 Live AI Demo</title>
    <script src="https://cdn.jsdelivr.net/npm/livekit-client@2.5.5/dist/livekit-client.umd.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: linear-gradient(135deg, #0d1117 0%, #161b27 60%, #0d1117 100%);
            color: #e6edf3;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .card {{
            background: rgba(22, 27, 39, 0.95);
            border: 1px solid rgba(108,99,255,0.2);
            border-radius: 24px;
            padding: 52px 44px;
            max-width: 500px;
            width: 100%;
            text-align: center;
            box-shadow: 0 32px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(108,99,255,0.05);
            backdrop-filter: blur(20px);
        }}
        .logo {{
            width: 80px; height: 80px;
            background: linear-gradient(135deg, #6c63ff, #a78bfa);
            border-radius: 22px;
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 28px;
            font-size: 38px;
            box-shadow: 0 8px 32px rgba(108,99,255,0.35);
        }}
        h1 {{ font-size: 26px; font-weight: 700; margin-bottom: 10px; }}
        .subtitle {{
            color: #8b949e; font-size: 15px; margin-bottom: 44px; line-height: 1.6;
        }}
        .btn {{
            display: inline-flex; align-items: center; justify-content: center;
            gap: 10px; padding: 16px 40px; font-size: 16px; font-weight: 600;
            border: none; border-radius: 50px; cursor: pointer;
            transition: all 0.2s; width: 100%; font-family: inherit;
        }}
        .btn-start {{
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: white; box-shadow: 0 6px 24px rgba(99,102,241,0.4);
        }}
        .btn-start:hover:not(:disabled) {{ opacity: 0.88; transform: translateY(-2px); box-shadow: 0 10px 32px rgba(99,102,241,0.5); }}
        .btn-stop {{ background: #ef4444; color: white; display: none; }}
        .btn-stop:hover {{ background: #dc2626; }}
        .btn:disabled {{ opacity: 0.45; cursor: not-allowed; transform: none !important; box-shadow: none !important; }}
        .status-bar {{
            margin-top: 24px; padding: 14px 20px; border-radius: 12px;
            font-size: 14px; background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            color: #8b949e; min-height: 48px;
            display: flex; align-items: center; justify-content: center; gap: 10px;
        }}
        .dot {{
            width: 8px; height: 8px; border-radius: 50%;
            background: #484f58; flex-shrink: 0;
        }}
        .dot.live {{ background: #22c55e; animation: pulse 1.5s infinite; }}
        .dot.connecting {{ background: #f59e0b; animation: pulse 0.7s infinite; }}
        .dot.error {{ background: #ef4444; }}
        @keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.25; }} }}
        .hint {{ margin-top: 22px; font-size: 12px; color: #30363d; line-height: 1.7; }}
        .powered {{ margin-top: 36px; font-size: 11px; color: #30363d; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">🎙️</div>
        <h1>{label}</h1>
        <p class="subtitle">
            Talk to our AI agent live — no phone number needed.<br>
            Click below and allow microphone access to begin.
        </p>

        <button class="btn btn-start" id="startBtn" onclick="startDemo()">
            ▶&nbsp; Start Conversation
        </button>
        <button class="btn btn-stop" id="stopBtn" onclick="stopDemo()">
            ■&nbsp; End Call
        </button>

        <div class="status-bar" id="statusBar">
            <div class="dot" id="statusDot"></div>
            <span id="statusText">Ready — click to begin</span>
        </div>

        <p class="hint">🔒 Your audio is private and not permanently stored.<br>Microphone access is required to speak with the agent.</p>
        <p class="powered">Powered by AI Voice Agent Platform</p>
    </div>

    <script>
        const SLUG = "{slug}";
        let room = null;

        function setStatus(text, state) {{
            document.getElementById('statusText').textContent = text;
            const dot = document.getElementById('statusDot');
            dot.className = 'dot' + (state ? ' ' + state : '');
        }}

        async function startDemo() {{
            const startBtn = document.getElementById('startBtn');
            const stopBtn  = document.getElementById('stopBtn');
            startBtn.disabled = true;
            setStatus('Connecting\u2026', 'connecting');

            try {{
                const res = await fetch('/api/demo/token/' + SLUG);
                if (!res.ok) throw new Error('Demo link expired or invalid.');
                const {{ token, room: roomName, ws_url, identity }} = await res.json();

                room = new LivekitClient.Room({{
                    adaptiveStream: true,
                    dynacast: true,
                }});

                // ── Lifecycle events ──────────────────────────────────────
                room.on(LivekitClient.RoomEvent.Connected, () => {{
                    setStatus('Waiting for agent to join\u2026', 'connecting');
                }});

                room.on(LivekitClient.RoomEvent.Reconnecting, () => {{
                    setStatus('Reconnecting\u2026', 'connecting');
                }});

                room.on(LivekitClient.RoomEvent.Reconnected, () => {{
                    setStatus('Reconnected \u2014 speak now', 'live');
                }});

                room.on(LivekitClient.RoomEvent.Disconnected, () => {{
                    setStatus('Call ended.', '');
                    startBtn.style.display = 'flex';
                    startBtn.disabled = false;
                    stopBtn.style.display = 'none';
                }});

                // ── Agent joins ───────────────────────────────────────────
                room.on(LivekitClient.RoomEvent.ParticipantConnected, (p) => {{
                    if (p.identity && (p.identity.startsWith('agent') || p.identity.startsWith('outbound'))) {{
                        setStatus('Agent is live \u2014 speak now', 'live');
                    }}
                }});

                // ── Agent audio: auto-attach when track arrives ───────────
                room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, _pub, participant) => {{
                    if (track.kind === 'audio') {{
                        console.log('[DEMO] Agent audio track from', participant.identity);
                        const audioEl = track.attach();
                        audioEl.autoplay = true;
                        document.body.appendChild(audioEl);
                    }}
                }});

                // ── Speaking indicator ────────────────────────────────────
                room.on(LivekitClient.RoomEvent.ActiveSpeakersChanged, (speakers) => {{
                    const agentSpeaking = speakers.some(s =>
                        s.identity && (s.identity.startsWith('agent') || s.identity.startsWith('outbound'))
                    );
                    if (agentSpeaking) setStatus('Agent speaking\u2026', 'live');
                }});

                // ── Connect with autoSubscribe ────────────────────────────
                await room.connect(ws_url, token, {{ autoSubscribe: true }});
                startBtn.style.display = 'none';
                stopBtn.style.display  = 'flex';

                // ── Publish mic (graceful degradation if denied) ──────────
                try {{
                    await room.localParticipant.setMicrophoneEnabled(true);
                    console.log('[DEMO] Mic published');
                }} catch (micErr) {{
                    console.warn('[DEMO] Mic unavailable:', micErr.message);
                    setStatus('No mic access \u2014 you can listen but not speak', 'connecting');
                }}

            }} catch (err) {{
                setStatus('\u26a0\ufe0f ' + err.message, 'error');
                startBtn.disabled = false;
            }}
        }}

        async function stopDemo() {{
            if (room) {{ await room.disconnect(); room = null; }}
            document.getElementById('startBtn').style.display = 'flex';
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').style.display = 'none';
            setStatus('Call ended \u2014 click to start again', '');
        }}
    </script>
</body>
</html>""")


# ── Agent Management Endpoints ─────────────────────────────────────────────────

def _load_agents():
    agents = read_json_file(AGENTS_FILE, [])
    if not agents:
        # Seed with default agent from current config
        cfg = read_config()
        agents = [{
            "id": "default",
            "name": "Daisy — Med Spa (Default)",
            "active": True,
            "stt_language": cfg.get("stt_language", "hi-IN"),
            "tts_language": cfg.get("tts_language", "hi-IN"),
            "tts_voice": cfg.get("tts_voice", "rohan"),
            "llm_model": cfg.get("llm_model", "gpt-4o-mini"),
            "first_line": cfg.get("first_line", ""),
            "agent_instructions": cfg.get("agent_instructions", ""),
            "created_at": datetime.utcnow().isoformat()
        }]
        write_json_file(AGENTS_FILE, agents)
    return agents

@app.get("/api/agents")
async def api_agents_list():
    return _load_agents()

@app.post("/api/agents")
async def api_agents_create(request: Request):
    data = await request.json()
    agents = _load_agents()
    agent = {
        "id": str(uuid.uuid4())[:8],
        "name": data.get("name", "New Agent"),
        "active": False,
        "stt_language": data.get("stt_language", "hi-IN"),
        "tts_language": data.get("tts_language", "hi-IN"),
        "tts_voice": data.get("tts_voice", "rohan"),
        "llm_model": data.get("llm_model", "gpt-4o-mini"),
        "first_line": data.get("first_line", ""),
        "agent_instructions": data.get("agent_instructions", ""),
        "created_at": datetime.utcnow().isoformat()
    }
    agents.append(agent)
    write_json_file(AGENTS_FILE, agents)
    return agent

@app.put("/api/agents/{agent_id}")
async def api_agents_update(agent_id: str, request: Request):
    data = await request.json()
    agents = _load_agents()
    for a in agents:
        if a["id"] == agent_id:
            a.update({k: v for k, v in data.items() if k not in ("id", "created_at")})
    write_json_file(AGENTS_FILE, agents)
    return {"status": "updated"}

@app.delete("/api/agents/{agent_id}")
async def api_agents_delete(agent_id: str):
    agents = _load_agents()
    agents = [a for a in agents if a["id"] != agent_id]
    if not agents:
        raise HTTPException(400, "Cannot delete the last agent")
    write_json_file(AGENTS_FILE, agents)
    return {"status": "deleted"}

@app.post("/api/agents/{agent_id}/activate")
async def api_agents_activate(agent_id: str):
    agents = _load_agents()
    target = next((a for a in agents if a["id"] == agent_id), None)
    if not target:
        raise HTTPException(404, "Agent not found")
    for a in agents:
        a["active"] = (a["id"] == agent_id)
    write_json_file(AGENTS_FILE, agents)
    # Push agent config to config.json
    write_config({
        "stt_language": target.get("stt_language", "hi-IN"),
        "tts_language": target.get("tts_language", "hi-IN"),
        "tts_voice": target.get("tts_voice", "rohan"),
        "llm_model": target.get("llm_model", "gpt-4o-mini"),
        "first_line": target.get("first_line", ""),
        "agent_instructions": target.get("agent_instructions", ""),
    })
    return {"status": "activated", "agent": target}

# ── Main Dashboard HTML ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    config = read_config()

    def sel(key, val):
        return "selected" if config.get(key) == val else ""

    # ── HTML-escape all user-supplied text to prevent JS SyntaxErrors ─────────
    import html as _html
    def e(v):
        """HTML-escape a value so it's safe inside HTML attributes and textarea."""
        return _html.escape(str(v or ""), quote=True)

    _first_line       = e(config.get("first_line", ""))
    _agent_instr      = e(config.get("agent_instructions", ""))
    _livekit_url      = e(config.get("livekit_url", ""))
    _sip_trunk_id     = e(config.get("sip_trunk_id", ""))
    _livekit_api_key  = e(config.get("livekit_api_key", ""))
    _livekit_api_sec  = e(config.get("livekit_api_secret", ""))
    _openai_key       = e(config.get("openai_api_key", ""))
    _sarvam_key       = e(config.get("sarvam_api_key", ""))
    _cal_key          = e(config.get("cal_api_key", ""))
    _cal_event        = e(config.get("cal_event_type_id", ""))
    _tg_token         = e(config.get("telegram_bot_token", ""))
    _tg_chat          = e(config.get("telegram_chat_id", ""))
    _supa_url         = e(config.get("supabase_url", ""))
    _supa_key         = e(config.get("supabase_key", ""))
    _n8n_url          = e(config.get("n8n_webhook_url", ""))
    _stt_delay        = e(config.get("stt_min_endpointing_delay", 0.6))
    _opening_greeting = e(config.get("opening_greeting", ""))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Voice Agent — Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0f1117;
      --sidebar: #161b27;
      --card: #1c2333;
      --border: #2a3448;
      --accent: #6c63ff;
      --accent-glow: rgba(108,99,255,0.18);
      --text: #e2e8f0;
      --muted: #8892a4;
      --green: #22c55e;
      --red: #ef4444;
      --yellow: #f59e0b;
      --sidebar-w: 240px;
    }}
    body {{ font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }}

    /* ── Sidebar ── */
    #sidebar {{
      width: var(--sidebar-w); min-width: var(--sidebar-w);
      background: var(--sidebar); border-right: 1px solid var(--border);
      display: flex; flex-direction: column; padding: 24px 0;
      position: relative; z-index: 10;
    }}
    .sidebar-brand {{
      padding: 0 20px 24px;
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 10px;
    }}
    .sidebar-brand .logo {{
      width: 32px; height: 32px; background: var(--accent);
      border-radius: 8px; display: flex; align-items: center; justify-content: center;
      font-size: 16px;
    }}
    .sidebar-brand .brand-text {{ font-weight: 700; font-size: 14px; line-height: 1.2; }}
    .sidebar-brand .brand-sub {{ font-size: 10px; color: var(--muted); }}
    .sidebar-nav {{ padding: 16px 0; flex: 1; }}
    .nav-section {{ padding: 8px 16px 4px; font-size: 10px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}
    .nav-item {{
      display: flex; align-items: center; gap: 10px;
      padding: 10px 20px; cursor: pointer; font-size: 13.5px; font-weight: 500;
      color: var(--muted); border-left: 3px solid transparent;
      transition: all 0.15s; user-select: none;
    }}
    .nav-item:hover {{ color: var(--text); background: rgba(255,255,255,0.04); }}
    .nav-item.active {{ color: var(--accent); border-left-color: var(--accent); background: var(--accent-glow); }}
    .nav-item .icon {{ font-size: 16px; width: 20px; text-align: center; }}
    .sidebar-footer {{
      padding: 16px 20px;
      border-top: 1px solid var(--border);
      font-size: 11px; color: var(--muted);
    }}
    .status-dot {{
      display: inline-block; width: 7px; height: 7px; border-radius: 50%;
      background: var(--green); margin-right: 6px; box-shadow: 0 0 6px var(--green);
    }}

    /* ── Main ── */
    #main {{ flex: 1; overflow-y: auto; background: var(--bg); }}
    .page {{ display: none; padding: 32px 36px; min-height: 100%; }}
    .page.active {{ display: block; }}
    .page-header {{ margin-bottom: 28px; }}
    .page-title {{ font-size: 22px; font-weight: 700; }}
    .page-sub {{ font-size: 13px; color: var(--muted); margin-top: 4px; }}

    /* ── Cards ── */
    .card {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 20px;
    }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 28px; }}
    .stat-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
    .stat-label {{ font-size: 11px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }}
    .stat-value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    .stat-sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}

    /* ── Table ── */
    .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead th {{ padding: 12px 16px; text-align: left; font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; background: rgba(255,255,255,0.03); border-bottom: 1px solid var(--border); }}
    tbody td {{ padding: 13px 16px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    tbody tr:hover {{ background: rgba(255,255,255,0.025); }}
    .badge {{ display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
    .badge-green {{ background: rgba(34,197,94,0.12); color: var(--green); }}
    .badge-gray {{ background: rgba(255,255,255,0.07); color: var(--muted); }}
    .badge-yellow {{ background: rgba(245,158,11,0.12); color: var(--yellow); }}

    /* ── Forms ── */
    label {{ display: block; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
    input[type=text], input[type=password], input[type=number], select, textarea {{
      width: 100%; background: var(--bg); border: 1px solid var(--border);
      border-radius: 8px; padding: 10px 12px; color: var(--text); font-family: inherit;
      font-size: 13.5px; outline: none; transition: border-color 0.15s;
    }}
    input:focus, select:focus, textarea:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }}
    textarea {{ font-family: 'JetBrains Mono', 'Fira Code', monospace; resize: vertical; }}
    .form-group {{ margin-bottom: 20px; }}
    .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .hint {{ font-size: 11.5px; color: var(--muted); margin-top: 5px; }}
    .section-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 20px; }}
    .section-title {{ font-size: 14px; font-weight: 600; margin-bottom: 18px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }}

    /* ── Buttons ── */
    .btn {{ display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: all 0.15s; }}
    .btn-primary {{ background: var(--accent); color: #fff; }}
    .btn-primary:hover {{ background: #5a52e0; box-shadow: 0 0 16px var(--accent-glow); }}
    .btn-ghost {{ background: transparent; border: 1px solid var(--border); color: var(--muted); }}
    .btn-ghost:hover {{ border-color: var(--accent); color: var(--accent); }}
    .btn-sm {{ padding: 5px 12px; font-size: 12px; }}
    .save-bar {{
      position: sticky; bottom: 0; left: 0; right: 0;
      background: rgba(22,27,39,0.95); backdrop-filter: blur(12px);
      border-top: 1px solid var(--border);
      padding: 14px 36px; display: flex; align-items: center; justify-content: space-between; z-index: 20;
    }}
    .save-status {{ font-size: 13px; font-weight: 500; color: var(--green); opacity: 0; transition: opacity 0.3s; }}

    /* ── Calendar ── */
    .cal-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }}
    .cal-grid {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }}
    .cal-day-name {{ text-align: center; font-size: 11px; color: var(--muted); font-weight: 600; padding: 8px 0; text-transform: uppercase; letter-spacing: 0.06em; }}
    .cal-cell {{
      min-height: 80px; background: var(--card); border: 1px solid var(--border);
      border-radius: 10px; padding: 10px; cursor: pointer; transition: all 0.18s; position: relative;
    }}
    .cal-cell:hover {{ border-color: var(--accent); background: var(--accent-glow); transform: scale(1.03); box-shadow: 0 4px 20px rgba(108,99,255,0.15); }}
    .cal-cell.today {{ border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }}
    .cal-cell.other-month {{ opacity: 0.3; }}
    .cal-num {{ font-size: 13px; font-weight: 700; }}
    .cal-dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--accent); margin-top: 6px; box-shadow: 0 0 6px var(--accent); }}
    .cal-booking-count {{ font-size: 10px; color: var(--accent); font-weight: 600; margin-top: 3px; }}
    .day-panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-top: 20px; display: none; }}
    .day-panel.show {{ display: block; animation: fadeIn 0.2s ease; }}
    .booking-item {{ padding: 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; transition: border-color 0.15s; }}
    .booking-item:hover {{ border-color: var(--accent); }}
    .booking-item:last-child {{ margin-bottom: 0; }}

    /* ── Modal ── */
    .modal-overlay {{
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.7); backdrop-filter: blur(6px);
      z-index: 1000; align-items: center; justify-content: center;
    }}
    .modal-overlay.open {{ display: flex; animation: fadeIn 0.2s ease; }}
    .modal-box {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 16px; padding: 28px; min-width: 480px; max-width: 600px; width: 90%;
      box-shadow: 0 24px 60px rgba(0,0,0,0.5);
      animation: slideUp 0.25s ease;
    }}
    .modal-title {{ font-size: 18px; font-weight: 700; margin-bottom: 6px; }}
    .modal-sub {{ font-size: 12px; color: var(--muted); margin-bottom: 20px; }}
    .modal-close {{
      position: absolute; top: 20px; right: 24px;
      background: none; border: none; color: var(--muted);
      font-size: 20px; cursor: pointer; line-height: 1;
    }}
    .modal-close:hover {{ color: var(--text); }}
    @keyframes fadeIn {{ from {{ opacity:0 }} to {{ opacity:1 }} }}
    @keyframes slideUp {{ from {{ transform:translateY(20px); opacity:0 }} to {{ transform:translateY(0); opacity:1 }} }}

    /* ── Premium extras ── */
    .stat-card {{ transition: transform 0.15s, box-shadow 0.15s; }}
    .stat-card:hover {{ transform: translateY(-3px); box-shadow: 0 8px 30px rgba(108,99,255,0.12); }}
    .stat-accent {{ color: var(--accent); }}
    .pulse {{ animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0%,100% {{ box-shadow: 0 0 6px var(--green); }} 50% {{ box-shadow: 0 0 14px var(--green); }} }}
    .preset-btn {{
      background: transparent; border: 1px solid var(--border); color: var(--muted);
      border-radius: 8px; padding: 8px 12px; font-size: 13px; font-weight: 600;
      cursor: pointer; transition: all 0.15s; text-align: left;
    }}
    .preset-btn:hover {{ border-color: var(--accent); color: var(--accent); background: var(--accent-glow); }}
    .agent-card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px;
      transition: border-color 0.15s;
    }}
    .agent-card.active {{ border-color: var(--green); box-shadow: 0 0 0 1px rgba(34,197,94,0.2); }}
    .demo-card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px;
    }}
  </style>
</head>
<body>

<!-- ── Day Detail Modal ── -->
<div class="modal-overlay" id="day-modal" onclick="if(event.target===this)closeDayModal()">
  <div class="modal-box" style="position:relative;">
    <button class="modal-close" onclick="closeDayModal()">✕</button>
    <div class="modal-title" id="modal-date-title">Bookings</div>
    <div class="modal-sub" id="modal-date-sub"></div>
    <div id="modal-bookings-body"></div>
  </div>
</div>

<!-- ── Agent Modal ── -->
<div class="modal-overlay" id="agent-modal" onclick="if(event.target===this)closeAgentModal()">
  <div class="modal-box" style="position:relative;max-width:640px;width:95%;max-height:90vh;overflow-y:auto;">
    <button class="modal-close" onclick="closeAgentModal()">✕</button>
    <div class="modal-title">🤖 Agent Configuration</div>
    <div class="modal-sub">Create or edit an agent persona</div>
    <div class="form-group"><label>Agent Name</label><input type="text" id="am-name" placeholder="e.g. Priya — Tamil Support"></div>
    <div class="form-row">
      <div class="form-group"><label>Language</label>
        <select id="am-tts-lang">
          <option value="hi-IN">Hindi (hi-IN)</option>
          <option value="en-IN">English India (en-IN)</option>
          <option value="ta-IN">Tamil (ta-IN)</option>
          <option value="te-IN">Telugu (te-IN)</option>
          <option value="kn-IN">Kannada (kn-IN)</option>
          <option value="gu-IN">Gujarati (gu-IN)</option>
          <option value="bn-IN">Bengali (bn-IN)</option>
          <option value="mr-IN">Marathi (mr-IN)</option>
          <option value="ml-IN">Malayalam (ml-IN)</option>
        </select>
      </div>
      <div class="form-group"><label>Voice</label>
        <select id="am-voice">
          <option value="rohan">Rohan — Male</option>
          <option value="kavya">Kavya — Female</option>
          <option value="priya">Priya — Female</option>
          <option value="dev">Dev — Male</option>
          <option value="shreya">Shreya — Female</option>
          <option value="neha">Neha — Female</option>
          <option value="ritu">Ritu — Female</option>
          <option value="amit">Amit — Male</option>
        </select>
      </div>
    </div>
    <div class="form-group"><label>LLM Model</label>
      <select id="am-llm">
        <option value="gpt-4o-mini">gpt-4o-mini (Fast)</option>
        <option value="gpt-4o">gpt-4o (Balanced)</option>
        <option value="gpt-4.1-mini">gpt-4.1-mini (Latest Fast)</option>
      </select>
    </div>
    <div class="form-group"><label>First Line (Opening Greeting)</label><input type="text" id="am-first-line" placeholder="Namaste! Welcome to..."></div>
    <div class="form-group"><label>System Instructions</label><textarea id="am-instructions" rows="6" placeholder="You are..."></textarea></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:8px;">
      <button class="btn btn-ghost" onclick="closeAgentModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveAgent()">💾 Save Agent</button>
    </div>
  </div>
</div>

<!-- ── Demo Link Modal ── -->
<div class="modal-overlay" id="demo-modal" onclick="if(event.target===this)closeDemoModal()">
  <div class="modal-box" style="position:relative;">
    <button class="modal-close" onclick="closeDemoModal()">✕</button>
    <div class="modal-title">🔗 Create Demo Link</div>
    <div class="modal-sub">Share a branded page so prospects can test your agent</div>
    <div class="form-group"><label>Demo Name</label><input type="text" id="dm-name" placeholder="e.g. Tamil Demo — Daisy's Med Spa"></div>
    <div class="form-group"><label>Phone Number (with country code)</label><input type="text" id="dm-phone" placeholder="+918849280319"></div>
    <div class="form-group"><label>Language Label</label>
      <select id="dm-language">
        <option value="auto" selected>Auto-detect 🌐 (recommended)</option>
        <option value="hi-IN">Hindi</option>
        <option value="en-IN">English</option>
        <option value="ta-IN">Tamil</option>
        <option value="te-IN">Telugu</option>
        <option value="bn-IN">Bengali</option>
        <option value="gu-IN">Gujarati</option>
        <option value="kn-IN">Kannada</option>
        <option value="ml-IN">Malayalam</option>
        <option value="mr-IN">Marathi</option>
        <option value="pa-IN">Punjabi</option>
        <option value="od-IN">Odia</option>
        <option value="ur-IN">Urdu (Hindi TTS)</option>
      </select>
    </div>
    <div class="form-group"><label>Greeting Preview (shown on demo page)</label><input type="text" id="dm-greeting" placeholder="Namaste! Welcome to Daisy's Med Spa..."></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:8px;">
      <button class="btn btn-ghost" onclick="closeDemoModal()">Cancel</button>
      <button class="btn btn-primary" onclick="createDemo()">🔗 Create Link</button>
    </div>
  </div>
</div>

<!-- ── Sidebar ── -->
<nav id="sidebar">
  <div class="sidebar-brand">
    <div class="logo">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="10" fill="rgba(255,255,255,0.12)"/>
        <path d="M8 12c0-2.21 1.79-4 4-4s4 1.79 4 4" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
        <circle cx="12" cy="15" r="2" fill="white"/>
        <path d="M6 18c1.5-1.5 3.5-2.5 6-2.5s4.5 1 6 2.5" stroke="white" stroke-width="1.4" stroke-linecap="round" opacity="0.6"/>
      </svg>
    </div>
    <div>
      <div class="brand-text">Voice Agent</div>
      <div class="brand-sub">Med Spa AI</div>
    </div>
  </div>
  <div class="sidebar-nav">
    <div class="nav-section">Overview</div>
    <div class="nav-item active" onclick="goTo('dashboard', this)"><span class="icon">📊</span> Dashboard</div>
    <div class="nav-item" onclick="goTo('calendar', this); loadCalendar();"><span class="icon">📅</span> Calendar</div>
    <div class="nav-section" style="margin-top:12px;">Configuration</div>
    <div class="nav-item" onclick="goTo('agent', this)"><span class="icon">🤖</span> Agent Settings</div>
    <div class="nav-item" onclick="goTo('agents', this); loadAgents();"><span class="icon">🧠</span> Agents</div>
    <div class="nav-item" onclick="goTo('models', this)"><span class="icon">🎙️</span> Models &amp; Voice</div>
    <div class="nav-item" onclick="goTo('credentials', this)"><span class="icon">🔑</span> API Credentials</div>
    <div class="nav-section" style="margin-top:12px;">Calling</div>
    <div class="nav-item" onclick="goTo('outbound', this)"><span class="icon">📤</span> Outbound Calls</div>
    <div class="nav-item" onclick="goTo('demos', this); loadDemos();"><span class="icon">🔗</span> Demo Links</div>
    <div class="nav-section" style="margin-top:12px;">Data</div>
    <div class="nav-item" onclick="goTo('logs', this); loadLogs();"><span class="icon">📞</span> Call Logs</div>
    <div class="nav-item" onclick="goTo('crm', this); loadCRM();"><span class="icon">👥</span> CRM Contacts</div>
    <div class="nav-section" style="margin-top:12px;">Mass Calling</div>
    <div class="nav-item" onclick="goTo('tel-overview', this); loadTelOverview();"><span class="icon">📊</span> Telephony Overview</div>
    <div class="nav-item" onclick="goTo('tel-trunks', this); loadTrunks();"><span class="icon">🔌</span> SIP Trunks</div>
    <div class="nav-item" onclick="goTo('tel-presets', this); loadAgentConfigs();"><span class="icon">🤖</span> Agent Presets</div>
    <div class="nav-item" onclick="goTo('tel-campaigns', this); loadCampaigns();"><span class="icon">📢</span> Campaigns</div>
    <div class="nav-item" onclick="goTo('tel-dnc', this); loadDNC();"><span class="icon">🚫</span> DNC List</div>
  </div>
  <div class="sidebar-footer">
    <span class="status-dot pulse"></span>Agent Online
  </div>
</nav>

<!-- ── Main Content ── -->
<div id="main">

  <!-- ── Dashboard ── -->
  <div id="page-dashboard" class="page active">
    <div class="page-header">
      <div class="page-title">Dashboard</div>
      <div class="page-sub">Real-time overview of your AI voice agent performance</div>
    </div>
    <div class="stat-grid" id="stat-grid">
      <div class="stat-card"><div class="stat-label">Total Calls</div><div class="stat-value" id="stat-calls">—</div><div class="stat-sub">All time</div></div>
      <div class="stat-card"><div class="stat-label">Bookings Made</div><div class="stat-value" id="stat-bookings">—</div><div class="stat-sub">Confirmed appointments</div></div>
      <div class="stat-card"><div class="stat-label">Avg Duration</div><div class="stat-value" id="stat-duration">—</div><div class="stat-sub">Seconds per call</div></div>
      <div class="stat-card"><div class="stat-label">Booking Rate</div><div class="stat-value" id="stat-rate">—</div><div class="stat-sub">Calls that converted</div></div>
    </div>
    <div class="section-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <div class="section-title" style="border:none;padding:0;margin:0;">Recent Calls</div>
        <button class="btn btn-ghost btn-sm" onclick="loadDashboard()">↻ Refresh</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Date</th><th>Phone</th><th>Duration</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody id="dash-table-body"><tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── Calendar ── -->
  <div id="page-calendar" class="page">
    <div class="page-header">
      <div class="page-title">Booking Calendar</div>
      <div class="page-sub">View confirmed appointments by date</div>
    </div>
    <div class="section-card">
      <div class="cal-header">
        <button class="btn btn-ghost btn-sm" onclick="changeMonth(-1)">← Prev</button>
        <div style="font-size:16px;font-weight:700;" id="cal-month-label">Month Year</div>
        <button class="btn btn-ghost btn-sm" onclick="changeMonth(1)">Next →</button>
      </div>
      <div class="cal-grid" id="cal-grid"></div>
      <div class="day-panel" id="day-panel">
        <div style="font-size:14px;font-weight:700;margin-bottom:12px;" id="day-panel-title">Selected Day</div>
        <div id="day-panel-body"></div>
      </div>
    </div>
  </div>

  <!-- ── Agent Settings ── -->
  <div id="page-agent" class="page">
    <div class="page-header">
      <div class="page-title">Agent Settings</div>
      <div class="page-sub">Configure AI personality, opening line, and sensitivity</div>
    </div>
    <div class="section-card">
      <div class="section-title">Opening Greeting</div>
      <div class="form-group">
        <label>First Line (What the agent says when a call connects)</label>
        <input type="text" id="first_line" value="{_first_line}" placeholder="Namaste! Welcome to Daisy's Med Spa...">
        <div class="hint">This is the very first thing the agent says. Keep it concise and warm.</div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">System Prompt</div>
      <div class="form-group">
        <label>Master System Prompt</label>
        <textarea id="agent_instructions" rows="16" placeholder="Enter the AI's full personality and instructions...">{_agent_instr}</textarea>
        <div class="hint">Date and time context are injected automatically. Do not hardcode today's date.</div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">Listening Sensitivity</div>
      <div class="form-group" style="max-width:220px;">
        <label>Endpointing Delay (seconds)</label>
        <input type="number" id="stt_min_endpointing_delay" step="0.05" min="0.1" max="3.0" value="{_stt_delay}">
        <div class="hint">Seconds the AI waits after silence before responding. Default: 0.6</div>
      </div>
    </div>
    <div class="save-bar">
      <span class="save-status" id="save-status-agent">✅ Saved!</span>
      <button class="btn btn-primary" onclick="saveConfig('agent')">💾 Save Agent Settings</button>
    </div>
  </div>

  <!-- ── Models & Voice ── -->
  <div id="page-models" class="page">
    <div class="page-header">
      <div class="page-title">Models & Voice</div>
      <div class="page-sub">Select the LLM brain and TTS voice persona</div>
    </div>
    <div class="section-card">
      <div class="section-title">🌐 Language Presets <span style="font-size:11px;color:var(--muted);font-weight:400;">(click to auto-fill all language settings)</span></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:4px;">
        <button class="preset-btn" onclick="applyPreset('hindi')">🇮🇳 Hindi</button>
        <button class="preset-btn" onclick="applyPreset('english')">🇬🇧 English (India)</button>
        <button class="preset-btn" onclick="applyPreset('tamil')">🌐 Tamil</button>
        <button class="preset-btn" onclick="applyPreset('telugu')">🌐 Telugu</button>
        <button class="preset-btn" onclick="applyPreset('kannada')">🌐 Kannada</button>
        <button class="preset-btn" onclick="applyPreset('gujarati')">🌐 Gujarati</button>
        <button class="preset-btn" onclick="applyPreset('bengali')">🌐 Bengali</button>
        <button class="preset-btn" onclick="applyPreset('marathi')">🌐 Marathi</button>
        <button class="preset-btn" onclick="applyPreset('malayalam')">🌐 Malayalam</button>
        <button class="preset-btn" onclick="applyPreset('hinglish')" style="border-color:var(--accent);color:var(--accent);">🎯 Hinglish</button>
        <button class="preset-btn" onclick="applyPreset('multilingual')" style="border-color:#f59e0b;color:#f59e0b;">🌍 Multilingual</button>
      </div>
      <div class="hint" id="preset-status"></div>
    </div>
    <div class="section-card">
      <div class="section-title">Language Model (LLM)</div>
      <div class="form-group" style="max-width:360px;">
        <label>OpenAI Model</label>
        <select id="llm_model">
          <option value="gpt-4o-mini" {sel('llm_model','gpt-4o-mini')}>gpt-4o-mini — Fast &amp; Cheap (Default)</option>
          <option value="gpt-4o" {sel('llm_model','gpt-4o')}>gpt-4o — Balanced</option>
          <option value="gpt-4.1" {sel('llm_model','gpt-4.1')}>gpt-4.1 — Latest (Recommended)</option>
          <option value="gpt-4.1-mini" {sel('llm_model','gpt-4.1-mini')}>gpt-4.1-mini — Fast &amp; Latest</option>
          <option value="gpt-4.5-preview" {sel('llm_model','gpt-4.5-preview')}>gpt-4.5-preview — Most Capable</option>
          <option value="o4-mini" {sel('llm_model','o4-mini')}>o4-mini — Reasoning, Fast</option>
          <option value="o3" {sel('llm_model','o3')}>o3 — Reasoning, Best</option>
          <option value="gpt-4-turbo" {sel('llm_model','gpt-4-turbo')}>gpt-4-turbo — Legacy</option>
          <option value="gpt-3.5-turbo" {sel('llm_model','gpt-3.5-turbo')}>gpt-3.5-turbo — Cheapest</option>
        </select>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">Voice Synthesis (Sarvam bulbul:v3)</div>
      <div class="form-row" style="max-width:720px;">
        <div class="form-group">
          <label>Speaker Voice</label>
          <select id="tts_voice">
            <option value="kavya" {sel('tts_voice','kavya')}>Kavya — Female, Friendly</option>
            <option value="rohan" {sel('tts_voice','rohan')}>Rohan — Male, Balanced</option>
            <option value="priya" {sel('tts_voice','priya')}>Priya — Female, Warm</option>
            <option value="shubh" {sel('tts_voice','shubh')}>Shubh — Male, Formal</option>
            <option value="shreya" {sel('tts_voice','shreya')}>Shreya — Female, Clear</option>
            <option value="ritu" {sel('tts_voice','ritu')}>Ritu — Female, Soft</option>
            <option value="rahul" {sel('tts_voice','rahul')}>Rahul — Male, Deep</option>
            <option value="amit" {sel('tts_voice','amit')}>Amit — Male, Casual</option>
            <option value="neha" {sel('tts_voice','neha')}>Neha — Female, Energetic</option>
            <option value="dev" {sel('tts_voice','dev')}>Dev — Male, Professional</option>
          </select>
        </div>
        <div class="form-group">
          <label>Language</label>
          <select id="tts_language">
            <option value="hi-IN" {sel('tts_language','hi-IN')}>Hindi (hi-IN)</option>
            <option value="en-IN" {sel('tts_language','en-IN')}>English India (en-IN)</option>
            <option value="ta-IN" {sel('tts_language','ta-IN')}>Tamil (ta-IN)</option>
            <option value="te-IN" {sel('tts_language','te-IN')}>Telugu (te-IN)</option>
            <option value="kn-IN" {sel('tts_language','kn-IN')}>Kannada (kn-IN)</option>
            <option value="ml-IN" {sel('tts_language','ml-IN')}>Malayalam (ml-IN)</option>
            <option value="mr-IN" {sel('tts_language','mr-IN')}>Marathi (mr-IN)</option>
            <option value="gu-IN" {sel('tts_language','gu-IN')}>Gujarati (gu-IN)</option>
            <option value="bn-IN" {sel('tts_language','bn-IN')}>Bengali (bn-IN)</option>
          </select>
        </div>
      </div>
    </div>
    <div class="save-bar">
      <span class="save-status" id="save-status-models">✅ Saved!</span>
      <button class="btn btn-primary" onclick="saveConfig('models')">💾 Save Model Settings</button>
    </div>
  </div>

  <!-- ── Agents Page ── -->
  <div id="page-agents" class="page">
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div class="page-title">🧠 Agent Library</div>
          <div class="page-sub">Create and manage multiple agent personas. Activate one to make it live.</div>
        </div>
        <button class="btn btn-primary" onclick="openAgentModal()">＋ New Agent</button>
      </div>
    </div>
    <div id="agents-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;"></div>
  </div>

  <!-- ── Outbound Calls Page ── -->
  <div id="page-outbound" class="page">
    <div class="page-header">
      <div class="page-title">📤 Outbound Calls</div>
      <div class="page-sub">Dispatch AI calls to individual numbers or run bulk campaigns</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start;">
      <div class="section-card">
        <div class="section-title">Single Call</div>
        <div class="form-group">
          <label>Phone Number (with country code)</label>
          <input type="text" id="single-phone" placeholder="+918849280319">
          <div class="hint">Must include + and country code</div>
        </div>
        <button class="btn btn-primary" style="width:100%" onclick="dispatchSingleCall()">📞 Dispatch Call</button>
        <div id="single-call-status" style="margin-top:12px;font-size:13px;"></div>
      </div>
      <div class="section-card">
        <div class="section-title">Bulk Campaign</div>
        <div class="form-group">
          <label>Phone Numbers (one per line)</label>
          <textarea id="bulk-phones" rows="6" placeholder="+918849280319
+917777777777
+919999999999"></textarea>
        </div>
        <div style="display:flex;gap:10px;">
          <button class="btn btn-primary" style="flex:1" onclick="startBulkCampaign()">▶ Start Campaign</button>
          <button class="btn btn-ghost" onclick="stopBulkCampaign()">⏹ Stop</button>
        </div>
        <div id="bulk-progress" style="margin-top:14px;"></div>
      </div>
    </div>
    <div class="section-card" style="margin-top:20px;" id="outbound-log-card">
      <div class="section-title">Campaign Log</div>
      <div id="outbound-log" style="font-size:13px;color:var(--muted);">No calls dispatched yet.</div>
    </div>
  </div>

  <!-- ── Demo Links Page ── -->
  <div id="page-demos" class="page">
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div class="page-title">🔗 Demo Links</div>
          <div class="page-sub">Share branded landing pages so anyone can test your agent instantly</div>
        </div>
        <button class="btn btn-primary" onclick="openDemoModal()">＋ Create Demo Link</button>
      </div>
    </div>
    <div id="demos-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;"></div>
  </div>

  <!-- ── API Credentials ── -->
  <!-- CRM Contacts Page -->
  <div id="page-crm" class="page">
    <div class="page-header">
      <div class="page-title">👥 CRM Contacts</div>
      <div class="page-sub">Every caller recorded automatically — name, phone, call history</div>
    </div>
    <div class="section-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <div class="section-title" style="margin:0;">All Contacts</div>
        <button class="btn btn-ghost btn-sm" onclick="loadCRM()">&#x21bb; Refresh</button>
      </div>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead>
            <tr style="border-bottom:1px solid var(--border);">
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Name</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Phone</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Total Calls</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Last Seen</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Status</th>
            </tr>
          </thead>
          <tbody id="crm-tbody">
            <tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading contacts...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── Telephony Overview ── -->
  <div id="page-tel-overview" class="page">
    <div class="page-header">
      <div class="page-title">📊 Telephony Overview</div>
      <div class="page-sub">Mass outbound calling — campaigns, trunks, analytics</div>
    </div>
    <div class="stat-grid" id="tel-stat-grid">
      <div class="stat-card"><div class="stat-label">Total Campaigns</div><div class="stat-value" id="tel-stat-campaigns">—</div><div class="stat-sub">All time</div></div>
      <div class="stat-card"><div class="stat-label">Active Campaigns</div><div class="stat-value" id="tel-stat-active">—</div><div class="stat-sub">Running now</div></div>
      <div class="stat-card"><div class="stat-label">Outbound Today</div><div class="stat-value" id="tel-stat-today">—</div><div class="stat-sub">Calls placed today</div></div>
      <div class="stat-card"><div class="stat-label">DNC Count</div><div class="stat-value" id="tel-stat-dnc">—</div><div class="stat-sub">Do Not Call list</div></div>
    </div>
    <div class="section-card" style="margin-top:20px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <div class="section-title" style="border:none;padding:0;margin:0;">14-Day Call Volume</div>
        <button class="btn btn-ghost btn-sm" onclick="loadTelOverview()">↻ Refresh</button>
      </div>
      <div id="tel-chart-container" style="overflow-x:auto;">
        <div id="tel-chart-bars" style="display:flex;align-items:flex-end;gap:6px;height:120px;padding:0 4px;"></div>
        <div id="tel-chart-labels" style="display:flex;gap:6px;margin-top:4px;"></div>
      </div>
    </div>
  </div>

  <!-- ── SIP Trunks ── -->
  <div id="page-tel-trunks" class="page">
    <div class="page-header">
      <div class="page-title">🔌 SIP Trunks</div>
      <div class="page-sub">Manage inbound and outbound telephony trunks linked to LiveKit</div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">
      <button class="btn btn-ghost btn-sm" onclick="syncLiveKit()" id="sync-btn">↻ Sync LiveKit</button>
      <button class="btn btn-ghost btn-sm" onclick="showTrunkForm('inbound')">+ Add Inbound</button>
      <button class="btn btn-primary btn-sm" onclick="showTrunkForm('outbound')">+ Add Outbound</button>
    </div>
    <div id="trunks-list"><div style="text-align:center;padding:32px;color:var(--muted);">Loading...</div></div>

    <!-- Trunk Form Modal -->
    <div id="trunk-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:none;align-items:center;justify-content:center;">
      <div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;width:100%;max-width:480px;max-height:90vh;overflow-y:auto;">
        <h3 id="trunk-modal-title" style="margin:0 0 20px;font-size:16px;font-weight:700;">Add Trunk</h3>
        <div id="trunk-form-fields"></div>
        <div style="display:flex;gap:8px;margin-top:20px;">
          <button class="btn btn-ghost" style="flex:1;" onclick="closeTrunkModal()">Cancel</button>
          <button class="btn btn-primary" style="flex:1;" onclick="submitTrunk()">Create Trunk</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Agent Presets ── -->
  <div id="page-tel-presets" class="page">
    <div class="page-header">
      <div class="page-title">🤖 Agent Presets</div>
      <div class="page-sub">Configure voice, language, and script for each call type</div>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:12px;color:var(--muted);margin-bottom:10px;">Quick-start from built-in template:</div>
      <div id="preset-templates" style="display:flex;gap:8px;flex-wrap:wrap;"></div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:20px;">
      <button class="btn btn-primary btn-sm" onclick="showConfigForm(null)">+ New Custom Preset</button>
      <button class="btn btn-ghost btn-sm" onclick="loadAgentConfigs()">↻ Refresh</button>
    </div>
    <div id="configs-list"><div style="text-align:center;padding:32px;color:var(--muted);">Loading...</div></div>

    <!-- Config Form Modal -->
    <div id="config-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;align-items:center;justify-content:center;">
      <div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;width:100%;max-width:600px;max-height:90vh;overflow-y:auto;">
        <h3 id="config-modal-title" style="margin:0 0 20px;font-size:16px;font-weight:700;">New Agent Preset</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div class="form-group"><label>Name</label><input type="text" id="cfg-name" placeholder="My Insurance Config"></div>
          <div class="form-group"><label>Type</label><select id="cfg-type"><option value="custom">Custom</option><option value="insurance">Insurance</option><option value="inquiry">Inquiry</option><option value="hr">HR</option><option value="appointment">Appointment</option><option value="survey">Survey</option></select></div>
          <div class="form-group"><label>LLM Model</label><input type="text" id="cfg-llm" value="gpt-4o-mini"></div>
          <div class="form-group"><label>TTS Provider</label><select id="cfg-tts-provider"><option value="sarvam">Sarvam</option><option value="elevenlabs">ElevenLabs</option></select></div>
          <div class="form-group"><label>TTS Voice</label><input type="text" id="cfg-voice" value="rohan" placeholder="rohan / anushka / kavya"></div>
          <div class="form-group"><label>TTS Language</label><input type="text" id="cfg-lang" value="hi-IN" placeholder="hi-IN / en-IN"></div>
          <div class="form-group"><label>Max Duration (s)</label><input type="number" id="cfg-duration" value="300"></div>
          <div class="form-group"><label>Max Turns</label><input type="number" id="cfg-turns" value="25"></div>
          <div class="form-group"><label>Window Start</label><input type="time" id="cfg-win-start" value="09:30"></div>
          <div class="form-group"><label>Window End</label><input type="time" id="cfg-win-end" value="19:30"></div>
        </div>
        <div class="form-group" style="margin-top:12px;"><label>First Line (greeting)</label><textarea id="cfg-first-line" rows="2" placeholder="Namaste! Main..."></textarea></div>
        <div class="form-group" style="margin-top:12px;"><label>Agent Instructions</label><textarea id="cfg-instructions" rows="5" placeholder="You are a..."></textarea></div>
        <div style="display:flex;gap:8px;margin-top:20px;">
          <button class="btn btn-ghost" style="flex:1;" onclick="closeConfigModal()">Cancel</button>
          <button class="btn btn-primary" style="flex:1;" onclick="submitConfig()">Save Preset</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Campaigns ── -->
  <div id="page-tel-campaigns" class="page">
    <div class="page-header">
      <div class="page-title">📢 Campaigns</div>
      <div class="page-sub">Create, manage, and monitor outbound call campaigns</div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">
      <button class="btn btn-primary btn-sm" onclick="showCampaignForm()">+ New Campaign</button>
      <button class="btn btn-ghost btn-sm" onclick="loadCampaigns()">↻ Refresh</button>
    </div>
    <div id="campaigns-list"><div style="text-align:center;padding:32px;color:var(--muted);">Loading...</div></div>

    <!-- Campaign Form Modal -->
    <div id="campaign-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;align-items:center;justify-content:center;">
      <div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;width:100%;max-width:560px;max-height:90vh;overflow-y:auto;">
        <h3 style="margin:0 0 20px;font-size:16px;font-weight:700;">New Campaign</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div class="form-group" style="grid-column:1/-1;"><label>Campaign Name</label><input type="text" id="camp-name" placeholder="March Insurance Drive"></div>
          <div class="form-group"><label>Agent Preset</label><select id="camp-config"></select></div>
          <div class="form-group"><label>SIP Trunk</label><select id="camp-trunk"></select></div>
          <div class="form-group"><label>Max Calls/Min</label><input type="number" id="camp-rate" value="5" min="1" max="60"></div>
          <div class="form-group"><label>Max Retries</label><input type="number" id="camp-retries" value="2" min="0" max="5"></div>
          <div class="form-group"><label>Daily Start</label><input type="time" id="camp-start" value="09:30"></div>
          <div class="form-group"><label>Daily End</label><input type="time" id="camp-end" value="19:30"></div>
        </div>
        <div class="form-group" style="margin-top:12px;"><label>Notes</label><textarea id="camp-notes" rows="2" placeholder="Optional notes..."></textarea></div>
        <div style="display:flex;gap:8px;margin-top:20px;">
          <button class="btn btn-ghost" style="flex:1;" onclick="closeCampaignModal()">Cancel</button>
          <button class="btn btn-primary" style="flex:1;" onclick="submitCampaign()">Create Campaign</button>
        </div>
      </div>
    </div>

    <!-- Leads Modal -->
    <div id="leads-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9998;align-items:center;justify-content:center;">
      <div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;width:100%;max-width:700px;max-height:90vh;overflow-y:auto;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
          <h3 id="leads-modal-title" style="margin:0;font-size:16px;font-weight:700;">Campaign Leads</h3>
          <button onclick="closeLeadsModal()" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;">×</button>
        </div>
        <!-- CSV upload -->
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:16px;">
          <div style="font-size:12px;font-weight:600;margin-bottom:8px;">Upload CSV (columns: phone, name, email)</div>
          <div style="display:flex;gap:8px;align-items:center;">
            <input type="file" id="leads-csv-file" accept=".csv" style="font-size:12px;">
            <button class="btn btn-primary btn-sm" onclick="uploadLeadsCSV()">Upload</button>
          </div>
          <div id="csv-upload-result" style="font-size:12px;color:var(--muted);margin-top:6px;"></div>
        </div>
        <!-- Manual add -->
        <div style="display:flex;gap:8px;margin-bottom:16px;">
          <input type="text" id="lead-phone" placeholder="+919876543210" style="flex:1;">
          <input type="text" id="lead-name" placeholder="Name (optional)" style="flex:1;">
          <button class="btn btn-ghost btn-sm" onclick="addSingleLead()">+ Add</button>
        </div>
        <!-- Leads table -->
        <div style="overflow-x:auto;max-height:300px;overflow-y:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr style="border-bottom:1px solid var(--border);">
              <th style="padding:8px 10px;text-align:left;color:var(--muted);">Phone</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);">Name</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);">Status</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);">Attempts</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);">Actions</th>
            </tr></thead>
            <tbody id="leads-tbody"><tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr></tbody>
          </table>
        </div>
        <div style="margin-top:16px;text-align:right;">
          <button class="btn btn-ghost btn-sm" onclick="loadLeads()">↻ Refresh Leads</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── DNC List ── -->
  <div id="page-tel-dnc" class="page">
    <div class="page-header">
      <div class="page-title">🚫 Do Not Call List</div>
      <div class="page-sub">Numbers on this list will never be dialled by any campaign</div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:20px;align-items:center;flex-wrap:wrap;">
      <input type="text" id="dnc-phone-input" placeholder="+919876543210" style="width:220px;">
      <select id="dnc-reason" style="padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font-size:13px;">
        <option value="manual">Manual</option>
        <option value="user_request">User Request</option>
        <option value="carrier_block">Carrier Block</option>
        <option value="trai">TRAI</option>
      </select>
      <button class="btn btn-primary btn-sm" onclick="addDNC()">+ Add to DNC</button>
      <button class="btn btn-ghost btn-sm" onclick="loadDNC()">↻ Refresh</button>
    </div>
    <div class="section-card">
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="border-bottom:1px solid var(--border);">
            <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Phone</th>
            <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Reason</th>
            <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Source</th>
            <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Added</th>
            <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Action</th>
          </tr></thead>
          <tbody id="dnc-tbody"><tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="page-credentials" class="page">
    <div class="page-header">
      <div class="page-title">API Credentials</div>
      <div class="page-sub">Credentials here override .env values at runtime. Never share this page.</div>
    </div>
    <div class="section-card">
      <div class="section-title">LiveKit</div>
      <div class="form-row">
        <div class="form-group"><label>LiveKit URL</label><input type="text" id="livekit_url" value="{_livekit_url}"></div>
        <div class="form-group"><label>SIP Trunk ID</label><input type="text" id="sip_trunk_id" value="{_sip_trunk_id}"></div>
        <div class="form-group"><label>API Key</label><input type="password" id="livekit_api_key" value="{_livekit_api_key}"></div>
        <div class="form-group"><label>API Secret</label><input type="password" id="livekit_api_secret" value="{_livekit_api_sec}"></div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">AI Providers</div>
      <div class="form-row">
        <div class="form-group"><label>OpenAI API Key</label><input type="password" id="openai_api_key" value="{_openai_key}"></div>
        <div class="form-group"><label>Sarvam API Key</label><input type="password" id="sarvam_api_key" value="{_sarvam_key}"></div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">Integrations</div>
      <div class="form-row">
        <div class="form-group"><label>Cal.com API Key</label><input type="password" id="cal_api_key" value="{_cal_key}"></div>
        <div class="form-group"><label>Cal.com Event Type ID</label><input type="text" id="cal_event_type_id" value="{_cal_event}"></div>
        <div class="form-group"><label>Telegram Bot Token</label><input type="password" id="telegram_bot_token" value="{_tg_token}"></div>
        <div class="form-group"><label>Telegram Chat ID</label><input type="text" id="telegram_chat_id" value="{_tg_chat}"></div>
        <div class="form-group"><label>Supabase URL</label><input type="text" id="supabase_url" value="{_supa_url}"></div>
        <div class="form-group"><label>Supabase Anon Key</label><input type="password" id="supabase_key" value="{_supa_key}"></div>
      </div>
    </div>
    <div class="save-bar">
      <span class="save-status" id="save-status-credentials">✅ Saved!</span>
      <button class="btn btn-primary" onclick="saveConfig('credentials')">💾 Save Credentials</button>
    </div>
  </div>

  <!-- ── Call Logs ── -->
  <div id="page-logs" class="page">
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div class="page-title">Call Logs</div>
          <div class="page-sub">Full history of all incoming calls and transcripts</div>
        </div>
        <button class="btn btn-ghost" onclick="loadLogs()">↻ Refresh</button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date & Time</th>
            <th>Phone</th>
            <th>Duration</th>
            <th>Status</th>
            <th>Summary</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="logs-table-body"><tr><td colspan="6" style="text-align:center;padding:32px;color:var(--muted);">Click Refresh to load call logs</td></tr></tbody>
      </table>
    </div>
  </div>

</div><!-- /main -->

<script>
// ── Navigation ──────────────────────────────────────────────────────────────
function goTo(pageId, el) {{
  const target = document.getElementById('page-' + pageId);
  if (!target) {{ console.warn('Page not found: page-' + pageId); return; }}
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  target.classList.add('active');
  if (el) el.classList.add('active');
}}

// ── Stats & Dashboard ───────────────────────────────────────────────────────
async function loadDashboard() {{
  const tbody = document.getElementById('dash-table-body');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr>';

  // Remove any existing DB error banner
  const existingBanner = document.getElementById('db-error-banner');
  if (existingBanner) existingBanner.remove();

  let stats = null, logs = null;

  // Fetch stats
  try {{
    const r = await fetch('/api/stats');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    stats = await r.json();
  }} catch(e) {{
    console.error('Stats fetch failed:', e);
  }}

  // Fetch logs separately so one failure doesn't break the other
  try {{
    const r = await fetch('/api/logs');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    logs = await r.json();
  }} catch(e) {{
    console.error('Logs fetch failed:', e);
  }}

  // ── DB error banner ───────────────────────────────────────────────────
  const dbErr = (stats && stats.db_error) ? stats.db_error : null;
  if (dbErr) {{
    const banner = document.createElement('div');
    banner.id = 'db-error-banner';
    banner.style.cssText = 'background:#ef444420;border:1px solid #ef4444;border-radius:12px;padding:14px 18px;margin-bottom:20px;font-size:13px;line-height:1.6;';
    banner.innerHTML = '<div style="font-weight:700;color:#ef4444;margin-bottom:6px;">Database Error</div>' +
      '<div style="color:var(--text);margin-bottom:8px;">' + dbErr + '</div>' +
      '<div style="color:var(--muted);">Check your DATABASE_URL env var in Coolify. ' +
      '<a href="/api/db-status" target="_blank" style="color:#60a5fa;">Check DB status</a></div>';
    const pageHeader = document.querySelector('#page-dashboard .stat-grid');
    if (pageHeader) pageHeader.parentNode.insertBefore(banner, pageHeader);
  }}

  // ── Update stat cards ────────────────────────────────────────────────
  var fmt = function(v, suffix) {{
    suffix = suffix || '';
    return (v !== null && v !== undefined) ? v + suffix : 'N/A';
  }};
  document.getElementById('stat-calls').textContent    = fmt(stats && stats.total_calls);
  document.getElementById('stat-bookings').textContent = fmt(stats && stats.total_bookings);
  document.getElementById('stat-duration').textContent = fmt(stats && stats.avg_duration, 's');
  document.getElementById('stat-rate').textContent     = fmt(stats && stats.booking_rate, '%');

  // ── Update calls table ─────────────────────────────────────────────────
  if (logs === null) {{
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#ef4444;">⚠ Could not load calls — check DATABASE_URL environment variable.</td></tr>';
    return;
  }}
  if (!logs.length) {{
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">No calls yet. Make a test call!</td></tr>';
    return;
  }}
  tbody.innerHTML = logs.slice(0, 20).map(log => `
    <tr>
      <td style="color:var(--muted)">${{new Date(log.created_at).toLocaleString()}}</td>
      <td style="font-weight:600">${{log.phone_number || 'Unknown'}}</td>
      <td>${{log.duration_seconds || 0}}s</td>
      <td>${{badgeFor(log.summary)}}</td>
      <td>
        ${{log.id ? `<a style="color:var(--accent);font-size:12px;text-decoration:none;" href="/api/logs/${{log.id}}/transcript" download="transcript_${{log.id}}.txt">⬇ Download</a>` : ''}}
      </td>
    </tr>`).join('');
}}

function badgeFor(summary) {{
  if (!summary) return '<span class="badge badge-gray">Ended</span>';
  if (summary.toLowerCase().includes('confirm')) return '<span class="badge badge-green">✓ Booked</span>';
  if (summary.toLowerCase().includes('cancel')) return '<span class="badge badge-yellow">✗ Cancelled</span>';
  return '<span class="badge badge-gray">Completed</span>';
}}

// ── Telephony Overview ──────────────────────────────────────────────────────
async function loadTelOverview() {{
  try {{
    const [overview, daily] = await Promise.all([
      fetch('/api/telephony/analytics/overview').then(r => r.json()),
      fetch('/api/telephony/analytics/daily?days=14').then(r => r.json()),
    ]);
    document.getElementById('tel-stat-campaigns').textContent = (overview.total_campaigns !== undefined) ? overview.total_campaigns : 'N/A';
    document.getElementById('tel-stat-active').textContent = (overview.active_campaigns !== undefined) ? overview.active_campaigns : 'N/A';
    document.getElementById('tel-stat-today').textContent = (overview.calls_today !== undefined) ? overview.calls_today : 'N/A';
    document.getElementById('tel-stat-dnc').textContent = (overview.dnc_count !== undefined) ? overview.dnc_count : 'N/A';

    // Build mini bar chart
    const bars = document.getElementById('tel-chart-bars');
    const labels = document.getElementById('tel-chart-labels');
    if (!bars || !daily) return;
    const maxCalls = Math.max(...daily.map(d => d.total_calls), 1);
    bars.innerHTML = daily.map(d => {{
      const h = Math.max(Math.round((d.total_calls / maxCalls) * 110), 4);
      return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;">
        <div style="font-size:10px;color:var(--muted);">${{d.total_calls || ''}}</div>
        <div title="${{d.date}}: ${{d.total_calls}} calls" style="width:100%;height:${{h}}px;background:linear-gradient(135deg,#3b82f6,#6366f1);border-radius:3px 3px 0 0;min-height:4px;"></div>
      </div>`;
    }}).join('');
    labels.innerHTML = daily.map(d => {{
      const dt = d.date.slice(5); // MM-DD
      return `<div style="flex:1;text-align:center;font-size:9px;color:var(--muted);">${{dt}}</div>`;
    }}).join('');
  }} catch(e) {{
    console.error('loadTelOverview error', e);
  }}
}}

// ── SIP Trunks ───────────────────────────────────────────────────────────────
let _trunkType = 'outbound';

async function loadTrunks() {{
  const el = document.getElementById('trunks-list');
  if (!el) return;
  el.innerHTML = '<div style="text-align:center;padding:32px;color:var(--muted);">Loading...</div>';
  try {{
    const trunks = await fetch('/api/telephony/trunks').then(r => r.json());
    if (!trunks.length) {{
      el.innerHTML = '<div style="text-align:center;padding:48px;color:var(--muted);border:2px dashed var(--border);border-radius:12px;"><div style="font-size:28px;margin-bottom:12px;">🔌</div><div>No SIP trunks configured yet.</div><div style="font-size:12px;margin-top:6px;">Add an outbound trunk to start making calls.</div></div>';
      return;
    }}
    const typeColors = {{ outbound: '#3b82f6', inbound: '#22c55e' }};
    el.innerHTML = trunks.map(t => {{
      const pool = Array.isArray(t.number_pool) ? t.number_pool : (typeof t.number_pool === 'string' ? JSON.parse(t.number_pool || '[]') : []);
      return `<div style="border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:10px;background:var(--card);display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <div style="display:flex;gap:12px;align-items:center;flex:1;">
          <div style="width:36px;height:36px;border-radius:10px;background:${{typeColors[t.trunk_type] || '#888'}}22;display:flex;align-items:center;justify-content:center;font-size:16px;">${{t.trunk_type==='outbound'?'↗':'↙'}}</div>
          <div>
            <div style="font-weight:600;font-size:14px;">${{t.name}} <span style="font-size:11px;padding:2px 7px;border-radius:20px;background:${{typeColors[t.trunk_type]||'#888'}}22;color:${{typeColors[t.trunk_type]||'#888'}}">${{t.trunk_type}}</span> <span style="font-size:11px;padding:2px 7px;border-radius:20px;background:var(--surface);color:var(--muted)">${{t.provider||''}}</span></div>
            <div style="font-size:12px;color:var(--muted);margin-top:4px;">${{t.sip_address ? 'SIP: '+t.sip_address+'  ' : ''}}Numbers: ${{pool.join(', ')||'—'}}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;">LiveKit ID: ${{t.livekit_trunk_id||'not synced'}}${{t.max_concurrent_calls?' · Max concurrent: '+t.max_concurrent_calls:''}}</div>
          </div>
        </div>
        <button onclick="deleteTrunk('${{t.id}}')" style="background:none;border:none;color:#ef4444;cursor:pointer;padding:6px;border-radius:8px;">🗑</button>
      </div>`;
    }}).join('');
  }} catch(e) {{ el.innerHTML = '<div style="color:var(--muted);padding:24px;">Error loading trunks: '+e.message+'</div>'; }}
}}

function showTrunkForm(type) {{
  _trunkType = type;
  document.getElementById('trunk-modal-title').textContent = 'Add '+(type==='outbound'?'Outbound':'Inbound')+' SIP Trunk';
  const outboundExtras = type === 'outbound' ? `
    <div class="form-group"><label>SIP Address</label><input type="text" id="tf-sip" placeholder="sip.vobiz.ai"></div>
    <div class="form-group"><label>Auth Username</label><input type="text" id="tf-user" placeholder=""></div>
    <div class="form-group"><label>Auth Password</label><input type="password" id="tf-pass" placeholder=""></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <div class="form-group"><label>Max Concurrent</label><input type="number" id="tf-conc" value="10"></div>
      <div class="form-group"><label>Max/Number/Day</label><input type="number" id="tf-mpd" value="150"></div>
    </div>` : `<div class="form-group"><label>Allowed Addresses (one per line)</label><textarea id="tf-allowed" rows="2" placeholder="sip.carrier.com"></textarea></div>`;
  document.getElementById('trunk-form-fields').innerHTML = `
    <div class="form-group"><label>Trunk Name</label><input type="text" id="tf-name" placeholder="Vobiz India Primary"></div>
    <div class="form-group"><label>Provider</label><input type="text" id="tf-provider" placeholder="vobiz | twilio | telnyx" value="vobiz"></div>
    ${{outboundExtras}}
    <div class="form-group"><label>${{type==='outbound'?'Number Pool':'Your Numbers'}} <span style="font-size:11px;color:var(--muted)">(one per line, E.164)</span></label>
      <textarea id="tf-pool" rows="3" placeholder="+919876543210\n+919876543211" style="font-family:monospace;"></textarea>
    </div>`;
  const m = document.getElementById('trunk-modal');
  m.style.display = 'flex';
}}
function closeTrunkModal() {{ document.getElementById('trunk-modal').style.display='none'; }}

async function submitTrunk() {{
  const name = document.getElementById('tf-name').value.trim();
  const provider = document.getElementById('tf-provider').value.trim();
  if (!name) return alert('Name is required');
  const pool = (document.getElementById('tf-pool').value||'').split('\n').map(n=>n.trim()).filter(Boolean);
  let body, endpoint;
  if (_trunkType === 'outbound') {{
    var _sipEl=document.getElementById('tf-sip'), _userEl=document.getElementById('tf-user'), _passEl=document.getElementById('tf-pass'), _concEl=document.getElementById('tf-conc'), _mpdEl=document.getElementById('tf-mpd');
    body = {{ name: name, provider: provider, sip_address: (_sipEl&&_sipEl.value)||'', auth_username: (_userEl&&_userEl.value)||'', auth_password: (_passEl&&_passEl.value)||'', number_pool: pool, max_concurrent_calls: parseInt((_concEl&&_concEl.value)||10), max_calls_per_number_per_day: parseInt((_mpdEl&&_mpdEl.value)||150) }};
    endpoint = '/api/telephony/trunks/outbound';
  }} else {{
    var _allowedEl = document.getElementById('tf-allowed');
    const allowed = ((_allowedEl&&_allowedEl.value)||'').split('\n').map(n=>n.trim()).filter(Boolean);
    body = {{ name, provider, numbers: pool, allowed_addresses: allowed }};
    endpoint = '/api/telephony/trunks/inbound';
  }}
  try {{
    const r = await fetch(endpoint, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
    if (!r.ok) {{ const e = await r.text(); throw new Error(e); }}
    closeTrunkModal(); loadTrunks();
  }} catch(e) {{ alert('Error: '+e.message); }}
}}

async function deleteTrunk(id) {{
  if (!confirm('Delete this trunk from LiveKit and database?')) return;
  await fetch('/api/telephony/trunks/'+id, {{method:'DELETE'}});
  loadTrunks();
}}

async function syncLiveKit() {{
  const btn = document.getElementById('sync-btn');
  if (btn) btn.textContent = '⟳ Syncing...';
  try {{
    const r = await fetch('/api/telephony/trunks/livekit/sync').then(x=>x.json());
    var _ob = r.outbound ? r.outbound.length : 0;
    var _ib = r.inbound ? r.inbound.length : 0;
    alert('LiveKit Trunks\nOutbound: ' + _ob + '\nInbound: ' + _ib);
  }} catch(e) {{ alert('Sync error: '+e.message); }}
  finally {{ if (btn) btn.textContent = '↻ Sync LiveKit'; }}
}}

// ── Agent Configs ─────────────────────────────────────────────────────────────
let _editConfigId = null;

async function loadAgentConfigs() {{
  const el = document.getElementById('configs-list');
  if (!el) return;
  el.innerHTML = '<div style="text-align:center;padding:32px;color:var(--muted);">Loading...</div>';
  try {{
    const [configs, presets] = await Promise.all([
      fetch('/api/telephony/agent-configs').then(r=>r.json()),
      fetch('/api/telephony/presets').then(r=>r.json()),
    ]);
    // Render preset templates row
    const tmplEl = document.getElementById('preset-templates');
    if (tmplEl) {{
      const icons = {{insurance:'🛡️',inquiry:'🔍',hr:'👔',appointment:'📅',survey:'📊',custom:'⚙️'}};
      tmplEl.innerHTML = Object.keys(presets).map(type => `<button onclick="createFromPreset('${{type}}')" style="display:flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);cursor:pointer;font-size:12px;">${{icons[type]||'⚙️'}} ${{type.charAt(0).toUpperCase()+type.slice(1)}}</button>`).join('');
    }}
    if (!configs.length) {{
      el.innerHTML = '<div style="text-align:center;padding:48px;color:var(--muted);border:2px dashed var(--border);border-radius:12px;"><div style="font-size:28px;margin-bottom:12px;">🤖</div><div>No agent presets yet. Create one above.</div></div>';
      return;
    }}
    const colors = {{insurance:'#a855f7',inquiry:'#3b82f6',hr:'#22c55e',appointment:'#eab308',survey:'#ec4899',custom:'#64748b'}};
    // Store config objects in a map keyed by ID — never inject JSON into onclick attributes
    window._configMap = {{}};
    configs.forEach(c => {{ window._configMap[c.id] = c; }});
    el.innerHTML = configs.map(c => `<div style="border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:10px;background:var(--card);display:flex;justify-content:space-between;align-items:start;">
      <div>
        <div style="font-weight:600;font-size:14px;">${{c.name}} <span style="font-size:11px;padding:2px 7px;border-radius:20px;background:${{colors[c.preset_type]||'#888'}}22;color:${{colors[c.preset_type]||'#888'}}">${{c.preset_type||'custom'}}</span></div>
        <div style="font-size:12px;color:var(--muted);margin-top:4px;">TTS: ${{c.tts_voice||'N/A'}} (${{c.tts_language||'N/A'}}) · LLM: ${{c.llm_model||'N/A'}} · Max: ${{c.max_call_duration_seconds||300}}s / ${{c.max_turns||25}} turns</div>
        <div style="font-size:12px;color:var(--muted);margin-top:2px;max-width:500px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${{c.first_line||'No first line set'}}</div>
      </div>
      <div style="display:flex;gap:8px;margin-top:4px;">
        <button onclick="showConfigForm('${{c.id}}')" style="background:none;border:none;cursor:pointer;color:var(--muted);">✏️</button>
        <button onclick="deleteConfig('${{c.id}}')" style="background:none;border:none;color:#ef4444;cursor:pointer;">🗑</button>
      </div>
    </div>`).join('');
  }} catch(e) {{ el.innerHTML = '<div style="color:var(--muted);padding:24px;">Error: '+e.message+'</div>'; }}
}}

async function createFromPreset(type) {{
  try {{
    await fetch('/api/telephony/agent-configs/from-preset/'+type, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}});
    loadAgentConfigs();
  }} catch(e) {{ alert('Error: '+e.message); }}
}}

function showConfigForm(cfgId) {{
  _editConfigId = null;
  var c = window._configMap[cfgId] || {{}};
  if (cfgId) _editConfigId = cfgId;
  document.getElementById('config-modal-title').textContent = _editConfigId ? 'Edit Agent Preset' : 'New Agent Preset';
  document.getElementById('cfg-name').value = c.name || '';
  document.getElementById('cfg-type').value = c.preset_type || 'custom';
  document.getElementById('cfg-llm').value = c.llm_model || 'gpt-4o-mini';
  document.getElementById('cfg-tts-provider').value = c.tts_provider || 'sarvam';
  document.getElementById('cfg-voice').value = c.tts_voice || 'rohan';
  document.getElementById('cfg-lang').value = c.tts_language || 'hi-IN';
  document.getElementById('cfg-duration').value = c.max_call_duration_seconds || 300;
  document.getElementById('cfg-turns').value = c.max_turns || 25;
  document.getElementById('cfg-win-start').value = c.call_window_start || '09:30';
  document.getElementById('cfg-win-end').value = c.call_window_end || '19:30';
  document.getElementById('cfg-first-line').value = c.first_line || '';
  document.getElementById('cfg-instructions').value = c.agent_instructions || '';
  document.getElementById('config-modal').style.display = 'flex';
}}
function closeConfigModal() {{ document.getElementById('config-modal').style.display='none'; }}

async function submitConfig() {{
  const name = document.getElementById('cfg-name').value.trim();
  if (!name) return alert('Name is required');
  const payload = {{
    name, preset_type: document.getElementById('cfg-type').value,
    llm_model: document.getElementById('cfg-llm').value,
    tts_provider: document.getElementById('cfg-tts-provider').value,
    tts_voice: document.getElementById('cfg-voice').value,
    tts_language: document.getElementById('cfg-lang').value,
    max_call_duration_seconds: parseInt(document.getElementById('cfg-duration').value)||300,
    max_turns: parseInt(document.getElementById('cfg-turns').value)||25,
    call_window_start: document.getElementById('cfg-win-start').value,
    call_window_end: document.getElementById('cfg-win-end').value,
    first_line: document.getElementById('cfg-first-line').value,
    agent_instructions: document.getElementById('cfg-instructions').value,
  }};
  const url = _editConfigId ? '/api/telephony/agent-configs/'+_editConfigId : '/api/telephony/agent-configs';
  const method = _editConfigId ? 'PUT' : 'POST';
  try {{
    const r = await fetch(url, {{method, headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
    if (!r.ok) throw new Error(await r.text());
    closeConfigModal(); loadAgentConfigs();
  }} catch(e) {{ alert('Error: '+e.message); }}
}}

async function deleteConfig(id) {{
  if (!confirm('Archive this preset?')) return;
  await fetch('/api/telephony/agent-configs/'+id, {{method:'DELETE'}});
  loadAgentConfigs();
}}

// ── Campaigns ─────────────────────────────────────────────────────────────────
let _activeCampaignId = null;

async function loadCampaigns() {{
  const el = document.getElementById('campaigns-list');
  if (!el) return;
  el.innerHTML = '<div style="text-align:center;padding:32px;color:var(--muted);">Loading...</div>';
  try {{
    const campaigns = await fetch('/api/telephony/campaigns').then(r=>r.json());
    if (!campaigns.length) {{
      el.innerHTML = '<div style="text-align:center;padding:48px;color:var(--muted);border:2px dashed var(--border);border-radius:12px;"><div style="font-size:28px;margin-bottom:12px;">📢</div><div>No campaigns yet. Create one to get started.</div></div>';
      return;
    }}
    const statusColors = {{ active:'#22c55e', draft:'#64748b', paused:'#eab308', completed:'#3b82f6', cancelled:'#ef4444' }};
    el.innerHTML = campaigns.map(c => {{
      const pct = c.total_leads ? Math.round((c.called_count/c.total_leads)*100) : 0;
      return `<div style="border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:12px;background:var(--card);">
        <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:10px;">
          <div>
            <div style="font-weight:600;font-size:15px;">${{c.name}} <span style="font-size:11px;padding:2px 8px;border-radius:20px;background:${{statusColors[c.status]||'#888'}}22;color:${{statusColors[c.status]||'#888'}}">${{c.status}}</span></div>
            <div style="font-size:12px;color:var(--muted);margin-top:3px;">Preset: ${{c.config_name||'—'}} · Trunk: ${{c.trunk_name||'—'}} · ${{c.max_calls_per_minute}} calls/min</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${{c.status==='draft'||c.status==='paused'?`<button onclick="campaignAction('${{c.id}}','start')" class="btn btn-primary btn-sm">▶ Start</button>`:''}}\
            ${{c.status==='active'?`<button onclick="campaignAction('${{c.id}}','pause')" class="btn btn-ghost btn-sm">⏸ Pause</button>`:''}}\
            ${{c.status==='paused'?`<button onclick="campaignAction('${{c.id}}','resume')" class="btn btn-ghost btn-sm">↩ Resume</button>`:''}}\
            ${{c.status!=='cancelled'&&c.status!=='completed'?`<button onclick="campaignAction('${{c.id}}','cancel')" class="btn btn-ghost btn-sm" style="color:#ef4444;">✕ Cancel</button>`:''}}
            <button onclick="openLeads('${{c.id}}','${{(c.name||'').replace(/'/g,'')}}');" class="btn btn-ghost btn-sm">👥 Leads</button>
            <button onclick="deleteCampaign('${{c.id}}')" style="background:none;border:none;color:#ef4444;cursor:pointer;">🗑</button>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px;">
          <div style="background:var(--surface);border-radius:8px;padding:8px;text-align:center;"><div style="font-size:11px;color:var(--muted);">Total</div><div style="font-weight:600;">${{c.total_leads||0}}</div></div>
          <div style="background:var(--surface);border-radius:8px;padding:8px;text-align:center;"><div style="font-size:11px;color:var(--muted);">Called</div><div style="font-weight:600;">${{c.called_count||0}}</div></div>
          <div style="background:var(--surface);border-radius:8px;padding:8px;text-align:center;"><div style="font-size:11px;color:var(--muted);">Answered</div><div style="font-weight:600;">${{c.answered_count||0}}</div></div>
          <div style="background:var(--surface);border-radius:8px;padding:8px;text-align:center;"><div style="font-size:11px;color:var(--muted);">Booked</div><div style="font-weight:600;">${{c.booked_count||0}}</div></div>
        </div>
        <div style="background:var(--surface);border-radius:6px;height:6px;overflow:hidden;"><div style="height:100%;width:${{pct}}%;background:linear-gradient(90deg,#3b82f6,#6366f1);border-radius:6px;transition:width 0.3s;"></div></div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">${{pct}}% complete</div>
      </div>`;
    }}).join('');
  }} catch(e) {{ el.innerHTML = '<div style="color:var(--muted);padding:24px;">Error: '+e.message+'</div>'; }}
}}

async function showCampaignForm() {{
  // Populate selects
  const [configs, trunks] = await Promise.all([
    fetch('/api/telephony/agent-configs').then(r=>r.json()),
    fetch('/api/telephony/trunks').then(r=>r.json()),
  ]);
  document.getElementById('camp-config').innerHTML = configs.map(c=>`<option value="${{c.id}}">${{c.name}}</option>`).join('') || '<option value="">— No presets found —</option>';
  document.getElementById('camp-trunk').innerHTML = trunks.filter(t=>t.trunk_type==='outbound').map(t=>`<option value="${{t.id}}">${{t.name}}</option>`).join('') || '<option value="">— No trunks found —</option>';
  document.getElementById('camp-name').value = '';
  document.getElementById('campaign-modal').style.display = 'flex';
}}
function closeCampaignModal() {{ document.getElementById('campaign-modal').style.display='none'; }}

async function submitCampaign() {{
  const name = document.getElementById('camp-name').value.trim();
  if (!name) return alert('Campaign name is required');
  const payload = {{
    name,
    agent_config_id: document.getElementById('camp-config').value,
    sip_trunk_id: document.getElementById('camp-trunk').value,
    max_calls_per_minute: parseInt(document.getElementById('camp-rate').value)||5,
    max_retries_per_lead: parseInt(document.getElementById('camp-retries').value)||2,
    daily_start_time: document.getElementById('camp-start').value,
    daily_end_time: document.getElementById('camp-end').value,
    notes: document.getElementById('camp-notes').value,
  }};
  try {{
    const r = await fetch('/api/telephony/campaigns', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
    if (!r.ok) throw new Error(await r.text());
    closeCampaignModal(); loadCampaigns();
  }} catch(e) {{ alert('Error: '+e.message); }}
}}

async function campaignAction(id, action) {{
  await fetch('/api/telephony/campaigns/'+id+'/'+action, {{method:'POST'}});
  loadCampaigns();
}}

async function deleteCampaign(id) {{
  if (!confirm('Delete this campaign and all its leads?')) return;
  await fetch('/api/telephony/campaigns/'+id, {{method:'DELETE'}});
  loadCampaigns();
}}

// ── Leads ─────────────────────────────────────────────────────────────────────
function openLeads(campaignId, name) {{
  _activeCampaignId = campaignId;
  document.getElementById('leads-modal-title').textContent = 'Leads — '+name;
  document.getElementById('leads-modal').style.display = 'flex';
  document.getElementById('csv-upload-result').textContent = '';
  loadLeads();
}}
function closeLeadsModal() {{ document.getElementById('leads-modal').style.display='none'; _activeCampaignId=null; }}

async function loadLeads() {{
  if (!_activeCampaignId) return;
  const tbody = document.getElementById('leads-tbody');
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr>';
  try {{
    const leads = await fetch('/api/telephony/campaigns/'+_activeCampaignId+'/leads?limit=200').then(r=>r.json());
    if (!leads.length) {{
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">No leads yet. Upload a CSV or add manually.</td></tr>';
      return;
    }}
    const sc = {{pending:'#64748b',calling:'#3b82f6',answered:'#22c55e',no_answer:'#eab308',failed:'#ef4444',dnc:'#dc2626',completed:'#22c55e',retry:'#f97316'}};
    tbody.innerHTML = leads.map(l => `<tr style="border-bottom:1px solid var(--border);">
      <td style="padding:8px 10px;">${{l.phone}}</td>
      <td style="padding:8px 10px;">${{l.name||'—'}}</td>
      <td style="padding:8px 10px;"><span style="font-size:11px;padding:2px 7px;border-radius:10px;background:${{sc[l.status]||'#888'}}22;color:${{sc[l.status]||'#888'}}">${{l.status}}</span></td>
      <td style="padding:8px 10px;">${{l.attempts||0}}</td>
      <td style="padding:8px 10px;"><button onclick="leadToDNC('${{l.id}}')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:11px;">DNC</button></td>
    </tr>`).join('');
  }} catch(e) {{ tbody.innerHTML = '<tr><td colspan="5" style="padding:16px;color:var(--muted);">Error: '+e.message+'</td></tr>'; }}
}}

async function addSingleLead() {{
  const phone = (document.getElementById('lead-phone').value.trim());
  if (!phone || !_activeCampaignId) return;
  const name = document.getElementById('lead-name').value.trim();
  await fetch('/api/telephony/campaigns/'+_activeCampaignId+'/leads', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{leads:[{{phone,name}}]}})
  }});
  document.getElementById('lead-phone').value='';
  document.getElementById('lead-name').value='';
  loadLeads();
}}

async function uploadLeadsCSV() {{
  const fileInput = document.getElementById('leads-csv-file');
  const resultEl = document.getElementById('csv-upload-result');
  if (!fileInput.files.length) return alert('Please select a CSV file');
  resultEl.textContent = 'Uploading...';
  try {{
    const body = await fileInput.files[0].arrayBuffer();
    const r = await fetch('/api/telephony/campaigns/'+_activeCampaignId+'/leads/csv', {{
      method: 'POST',
      headers: {{'Content-Type': 'text/csv'}},
      body,
    }});
    const data = await r.json();
    resultEl.textContent = '✅ Added '+data.added+' leads';
    loadLeads();
  }} catch(e) {{ resultEl.textContent = '❌ Error: '+e.message; }}
}}

async function leadToDNC(leadId) {{
  if (!confirm('Move this lead to DNC?')) return;
  await fetch('/api/telephony/campaigns/'+_activeCampaignId+'/leads/'+leadId+'/dnc', {{method:'POST'}});
  loadLeads();
}}

// ── DNC List ──────────────────────────────────────────────────────────────────
async function loadDNC() {{
  const tbody = document.getElementById('dnc-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading...</td></tr>';
  try {{
    const list = await fetch('/api/telephony/dnc').then(r=>r.json());
    if (!list.length) {{
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">DNC list is empty.</td></tr>';
      return;
    }}
    tbody.innerHTML = list.map(d => `<tr style="border-bottom:1px solid var(--border);">
      <td style="padding:10px 12px;font-family:monospace;">${{d.phone}}</td>
      <td style="padding:10px 12px;">${{d.reason||'—'}}</td>
      <td style="padding:10px 12px;">${{d.source||'—'}}</td>
      <td style="padding:10px 12px;color:var(--muted);font-size:12px;">${{(d.created_at||'').slice(0,10)}}</td>
      <td style="padding:10px 12px;"><button onclick="removeDNC('${{d.phone}}')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:12px;">Remove</button></td>
    </tr>`).join('');
  }} catch(e) {{ tbody.innerHTML = '<tr><td colspan="5" style="padding:16px;color:var(--muted);">Error: '+e.message+'</td></tr>'; }}
}}

async function addDNC() {{
  const phone = document.getElementById('dnc-phone-input').value.trim();
  const reason = document.getElementById('dnc-reason').value;
  if (!phone) return alert('Enter a phone number');
  await fetch('/api/telephony/dnc', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{phone,reason}})}});
  document.getElementById('dnc-phone-input').value='';
  loadDNC();
}}

async function removeDNC(phone) {{
  if (!confirm('Remove '+phone+' from DNC list?')) return;
  await fetch('/api/telephony/dnc/'+encodeURIComponent(phone), {{method:'DELETE'}});
  loadDNC();
}}

// ── Call Logs ───────────────────────────────────────────────────────────────
async function loadLogs() {{

  const tbody = document.getElementById('logs-table-body');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr>';
  try {{
    const logs = await fetch('/api/logs').then(r => r.json());
    if (!logs || logs.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">No call logs found.</td></tr>';
      return;
    }}
    tbody.innerHTML = logs.map(log => `
      <tr>
        <td style="color:var(--muted);white-space:nowrap">${{new Date(log.created_at).toLocaleString()}}</td>
        <td style="font-weight:600">${{log.phone_number || 'Unknown'}}</td>
        <td>${{log.duration_seconds || 0}}s</td>
        <td>${{badgeFor(log.summary)}}</td>
        <td style="color:var(--muted);font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{log.summary || ''}}">${{log.summary || '—'}}</td>
        <td>
          ${{log.id ? `<a class="btn btn-ghost btn-sm" style="text-decoration:none;" href="/api/logs/${{log.id}}/transcript" download="transcript_${{log.id}}.txt">⬇ Transcript</a>` : '—'}}
          ${{log.recording_url ? `<a class="btn btn-ghost btn-sm" style="text-decoration:none;margin-left:4px;" href="${{log.recording_url}}" target="_blank">🎧 Recording</a>` : ''}}
        </td>
      </tr>`).join('');
  }} catch(e) {{
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#ef4444;">Error loading logs. Check Supabase credentials.</td></tr>';
  }}
}}

// ── Calendar ────────────────────────────────────────────────────────────────
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let allBookings = [];

async function loadCalendar() {{
  try {{ allBookings = await fetch('/api/bookings').then(r => r.json()); }} catch(e) {{ allBookings = []; }}
  renderCalendar();
}}

function changeMonth(dir) {{ calMonth += dir; if (calMonth > 11) {{ calMonth = 0; calYear++; }} else if (calMonth < 0) {{ calMonth = 11; calYear--; }} renderCalendar(); }}

function renderCalendar() {{
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('cal-month-label').textContent = `${{months[calMonth]}} ${{calYear}}`;
  const grid = document.getElementById('cal-grid');
  const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const today = new Date();

  // Build booking map by date string YYYY-MM-DD
  const bookMap = {{}};
  allBookings.forEach(b => {{
    const d = b.created_at ? b.created_at.slice(0,10) : null;
    if (d) {{ bookMap[d] = bookMap[d] || []; bookMap[d].push(b); }}
  }});

  let html = days.map(d => `<div class="cal-day-name">${{d}}</div>`).join('');

  const first = new Date(calYear, calMonth, 1);
  const last = new Date(calYear, calMonth + 1, 0);
  const startPad = first.getDay();

  // Prev month padding
  for (let i = 0; i < startPad; i++) {{
    const d = new Date(calYear, calMonth, -startPad + i + 1);
    html += `<div class="cal-cell other-month"><div class="cal-num">${{d.getDate()}}</div></div>`;
  }}

  for (let day = 1; day <= last.getDate(); day++) {{
    const dateStr = `${{calYear}}-${{String(calMonth+1).padStart(2,'0')}}-${{String(day).padStart(2,'0')}}`;
    const bks = bookMap[dateStr] || [];
    const isToday = today.getFullYear()===calYear && today.getMonth()===calMonth && today.getDate()===day;
    html += `<div class="cal-cell${{isToday?' today':''}}" onclick="showDay('${{dateStr}}', ${{JSON.stringify(bks).replace(/'/g,"&apos;")}})">
      <div class="cal-num">${{day}}</div>
      ${{bks.length ? `<div class="cal-dot"></div><div class="cal-booking-count">${{bks.length}} booking${{bks.length>1?'s':''}}</div>` : ''}}
    </div>`;
  }}

  // Next month padding
  const endPad = 6 - last.getDay();
  for (let i = 1; i <= endPad; i++) {{
    html += `<div class="cal-cell other-month"><div class="cal-num">${{i}}</div></div>`;
  }}

  grid.innerHTML = html;
  document.getElementById('day-panel').classList.remove('show');
}}

function showDay(dateStr, bookings) {{
  // Update old inline panel too
  const panel = document.getElementById('day-panel');
  if (panel) {{
    panel.classList.add('show');
    document.getElementById('day-panel-title').textContent = `Bookings on ${{dateStr}}`;
  }}
  // Open modal overlay
  openDayModal(dateStr, bookings);
}}

function openDayModal(dateStr, bookings) {{
  const modal = document.getElementById('day-modal');
  const dateObj = new Date(dateStr + 'T00:00:00');
  const formatted = dateObj.toLocaleDateString('en-IN', {{weekday:'long', year:'numeric', month:'long', day:'numeric'}});
  document.getElementById('modal-date-title').textContent = formatted;
  document.getElementById('modal-date-sub').textContent =
    bookings.length ? `${{bookings.length}} booking${{bookings.length>1?'s':''}} on this day` : 'No bookings on this day';

  if (!bookings || bookings.length === 0) {{
    document.getElementById('modal-bookings-body').innerHTML =
      '<div style="text-align:center;padding:32px;color:var(--muted);font-size:14px;">📅 No bookings on this day.</div>';
  }} else {{
    document.getElementById('modal-bookings-body').innerHTML = bookings.map(b => `
      <div class="booking-item">
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <div style="font-weight:700;font-size:14px;">📞 ${{b.phone_number || 'Unknown'}}</div>
          <span class="badge badge-green">✅ Booked</span>
        </div>
        <div style="font-size:12px;color:var(--muted);margin-top:6px;">🕐 ${{new Date(b.created_at).toLocaleTimeString('en-IN', {{hour:'2-digit',minute:'2-digit'}})}}</div>
        ${{b.summary ? `<div style="font-size:12px;color:var(--text);margin-top:6px;padding:8px;background:rgba(255,255,255,0.04);border-radius:6px;">💬 ${{b.summary}}</div>` : ''}}
      </div>`).join('');
  }}
  modal.classList.add('open');
}}

function closeDayModal() {{
  document.getElementById('day-modal').classList.remove('open');
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeDayModal(); }});

// ── CRM ─────────────────────────────────────────────────────────────────────
async function loadCRM() {{
  const tbody = document.getElementById('crm-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading contacts...</td></tr>';
  try {{
    const contacts = await fetch('/api/contacts').then(r => r.json());
    if (!contacts.length) {{
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:40px;color:var(--muted);">No contacts yet. They will appear here automatically after calls.</td></tr>';
      return;
    }}
    tbody.innerHTML = contacts.map(c => `
      <tr style="border-bottom:1px solid var(--border);transition:background 0.12s;" onmouseover="this.style.background='rgba(255,255,255,0.025)'" onmouseout="this.style.background=''">
        <td style="padding:14px 16px;font-weight:600;">${{c.caller_name || '<span style="color:var(--muted);font-weight:400;">Unknown</span>'}}</td>
        <td style="padding:14px 16px;font-family:monospace;font-size:13px;">${{c.phone_number || '—'}}</td>
        <td style="padding:14px 16px;text-align:center;"><span style="background:rgba(108,99,255,0.12);color:var(--accent);padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">${{c.total_calls}}</span></td>
        <td style="padding:14px 16px;color:var(--muted);font-size:12px;">${{c.last_seen ? new Date(c.last_seen).toLocaleString('en-IN') : '—'}}</td>
        <td style="padding:14px 16px;">${{c.is_booked
          ? '<span class="badge badge-green">✅ Booked</span>'
          : '<span class="badge badge-gray">📵 No booking</span>'}}</td>
      </tr>`).join('');
  }} catch(e) {{
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#ef4444;">Error loading contacts. Check Supabase credentials.</td></tr>';
  }}
}}

// ── Save Config ─────────────────────────────────────────────────────────────
async function saveConfig(section) {{
  const get = id => {{ const el = document.getElementById(id); return el ? el.value : null; }};

  const payload = {{}};

  if (section === 'agent') {{
    Object.assign(payload, {{
      first_line: get('first_line'),
      agent_instructions: get('agent_instructions'),
      stt_min_endpointing_delay: parseFloat(get('stt_min_endpointing_delay')),
    }});
  }} else if (section === 'models') {{
    Object.assign(payload, {{
      llm_model: get('llm_model'),
      tts_voice: get('tts_voice'),
      tts_language: get('tts_language'),
      stt_language: get('tts_language') || 'hi-IN',
    }});
  }} else if (section === 'credentials') {{
    Object.assign(payload, {{
      livekit_url: get('livekit_url'), sip_trunk_id: get('sip_trunk_id'),
      livekit_api_key: get('livekit_api_key'), livekit_api_secret: get('livekit_api_secret'),
      openai_api_key: get('openai_api_key'), sarvam_api_key: get('sarvam_api_key'),
      cal_api_key: get('cal_api_key'), cal_event_type_id: get('cal_event_type_id'),
      telegram_bot_token: get('telegram_bot_token'), telegram_chat_id: get('telegram_chat_id'),
      supabase_url: get('supabase_url'), supabase_key: get('supabase_key'),
    }});
  }}

  const res = await fetch('/api/config', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload)
  }});

  const statusEl = document.getElementById('save-status-' + section);
  if (res.ok) {{
    statusEl.style.opacity = '1';
    setTimeout(() => {{ statusEl.style.opacity = '0'; }}, 2500);
  }} else {{
    alert('Failed to save. Check server logs.');
  }}
}}

// ── Language Presets ─────────────────────────────────────────────────────────
const PRESETS = {{
  hindi:       {{stt:'hi-IN',tts:'hi-IN',voice:'rohan', label:'Hindi',    greeting:"Namaste! Daisy's Med Spa mein aapka swagat hai. Main aapki kaise madad kar sakti hoon?"}},
  english:     {{stt:'en-IN',tts:'en-IN',voice:'dev',   label:'English',  greeting:"Hello! Welcome to Daisy's Med Spa. How can I help you today?"}},
  tamil:       {{stt:'ta-IN',tts:'ta-IN',voice:'kavya', label:'Tamil',    greeting:"Vanakkam! Daisy's Med Spa-vil ungalai varkarpom. Naan ungalukku eppadi udavalam?"}},
  telugu:      {{stt:'te-IN',tts:'te-IN',voice:'shreya',label:'Telugu',   greeting:"Namaskaram! Daisy's Med Spa ki swaagatam. Meeru ela help kavalaano?"}},
  kannada:     {{stt:'kn-IN',tts:'kn-IN',voice:'neha',  label:'Kannada',  greeting:"Namaskara! Daisy's Med Spa ge swaagatha. Naanu nimage hege sahaya maadali?"}},
  gujarati:    {{stt:'gu-IN',tts:'gu-IN',voice:'priya', label:'Gujarati', greeting:"Namaste! Daisy's Med Spa ma apnu swagat che. Hu tamne kevi rite madad kari shakun?"}},
  bengali:     {{stt:'bn-IN',tts:'bn-IN',voice:'ritu',  label:'Bengali',  greeting:"Namaskar! Daisy's Med Spa-te apnake swagat. Ami apnake kemon sahajata korte pari?"}},
  marathi:     {{stt:'mr-IN',tts:'mr-IN',voice:'kavya', label:'Marathi',  greeting:"Namaskar! Daisy's Med Spa madhe aapale swagat aahe. Mi tumhala kashi madad karu shkto?"}},
  malayalam:   {{stt:'ml-IN',tts:'ml-IN',voice:'priya', label:'Malayalam',greeting:"Namaskaram! Daisy's Med Spa-il swagatham. Ente sahayam enthu?"}},
  hinglish:    {{stt:'hi-IN',tts:'hi-IN',voice:'rohan', label:'Hinglish', greeting:"Namaste! Welcome to Daisy's Med Spa. Main aapki kaise help kar sakti hoon?"}},
  multilingual:{{stt:'hi-IN',tts:'hi-IN',voice:'rohan', label:'Multilingual',greeting:"Namaste! Welcome to Daisy's Med Spa. Please speak any language — Hindi, English, Tamil, Telugu — and I'll respond in the same language."}},
}};

async function applyPreset(key) {{
  const p = PRESETS[key]; if (!p) return;
  const setV = (id,v) => {{ const e=document.getElementById(id); if(e) e.value=v; }};
  setV('tts_language', p.tts); setV('tts_voice', p.voice); setV('first_line', p.greeting);
  const res = await fetch('/api/config', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{tts_language:p.tts, stt_language:p.stt, tts_voice:p.voice, first_line:p.greeting}})
  }});
  const st = document.getElementById('preset-status');
  if (st) {{ st.textContent = res.ok ? '✅ '+p.label+' preset applied!' : '❌ Failed'; st.style.color = res.ok ? 'var(--green)':'var(--red)'; }}
}}

// ── Agents ────────────────────────────────────────────────────────────────────
let editingAgentId = null;

async function loadAgents() {{
  const g = document.getElementById('agents-grid'); if (!g) return;
  g.innerHTML = '<div style="color:var(--muted);padding:20px;">Loading...</div>';
  const agents = await fetch('/api/agents').then(r=>r.json()).catch(()=>[]);
  if (!agents.length) {{ g.innerHTML='<div style="color:var(--muted);padding:20px;">No agents yet.</div>'; return; }}
  window._agentMap = {{}};
  agents.forEach(a => {{ window._agentMap[a.id] = a; }});
  g.innerHTML = agents.map(a=>`
    <div class="agent-card${{a.active?' active':''}}">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <div style="font-weight:700;">🤖 ${{a.name}}</div>
        ${{a.active?'<span class="badge badge-green">● Live</span>':''}}
      </div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:12px;">
        🌐 ${{a.tts_language||'hi-IN'}} · 🎙 ${{a.tts_voice||'rohan'}} · 🧠 ${{a.llm_model||'gpt-4o-mini'}}
      </div>
      <div style="display:flex;gap:8px;">
        ${{!a.active?`<button class="btn btn-primary btn-sm" onclick="activateAgent('${{a.id}}')">▶ Activate</button>`:''}}
        <button class="btn btn-ghost btn-sm" onclick="editAgent('${{a.id}}')">✏ Edit</button>
        ${{a.id!=='default'?`<button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deleteAgent('${{a.id}}')">🗑</button>`:''}}
      </div>
    </div>`).join('');
}}

async function activateAgent(id) {{ await fetch('/api/agents/'+id+'/activate',{{method:'POST'}}); loadAgents(); }}
async function deleteAgent(id) {{
  if (!confirm('Delete this agent?')) return;
  await fetch('/api/agents/'+id,{{method:'DELETE'}}); loadAgents();
}}
function editAgent(agentId) {{
  var agent = (window._agentMap && window._agentMap[agentId]) || {{}};
  editingAgentId = agentId;
  ['am-name','am-tts-lang','am-voice','am-llm','am-first-line','am-instructions'].forEach(id => {{
    const el=document.getElementById(id); if(!el) return;
    const key = {{
      'am-name':'name','am-tts-lang':'tts_language','am-voice':'tts_voice',
      'am-llm':'llm_model','am-first-line':'first_line','am-instructions':'agent_instructions'
    }}[id];
    if (key) el.value = agent[key]||'';
  }});
  document.getElementById('agent-modal').classList.add('open');
}}
function openAgentModal() {{ editingAgentId=null; document.getElementById('agent-modal').classList.add('open'); }}
function closeAgentModal() {{ document.getElementById('agent-modal').classList.remove('open'); }}
async function saveAgent() {{
  const g = id => {{ const e=document.getElementById(id); return e?e.value:''; }};
  const data = {{ name:g('am-name'), tts_language:g('am-tts-lang'), stt_language:g('am-tts-lang'),
    tts_voice:g('am-voice'), llm_model:g('am-llm'), first_line:g('am-first-line'), agent_instructions:g('am-instructions') }};
  const url = editingAgentId ? '/api/agents/'+editingAgentId : '/api/agents';
  const method = editingAgentId ? 'PUT' : 'POST';
  await fetch(url,{{method,headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
  closeAgentModal(); loadAgents();
}}

// ── Outbound Calls ────────────────────────────────────────────────────────────
let activeBulkJobId = null, bulkPollTimer = null;

async function dispatchSingleCall() {{
  const phone = (document.getElementById('single-phone')||{{}}).value||''.trim();
  const st = document.getElementById('single-call-status');
  if (!phone) {{ st.textContent='❌ Enter a phone number'; return; }}
  st.textContent='⏳ Dispatching...';
  const res = await fetch('/api/call/outbound',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{phone_number:phone}})}}).catch(e=>{{st.textContent='❌ '+e.message;return null;}});
  if (!res) return;
  const d = await res.json();
  if (res.ok) {{
    st.innerHTML='<span style="color:var(--green)">✅ Dispatched! Room: '+d.room+'</span>';
    const log=document.getElementById('outbound-log');
    log.innerHTML='<div style="padding:10px;background:rgba(34,197,94,0.08);border-radius:8px;margin-bottom:8px;">📞 '+phone+' — Dispatched</div>'+log.innerHTML;
  }} else st.innerHTML='<span style="color:var(--red)">❌ '+(d.detail||'Error')+'</span>';
}}

async function startBulkCampaign() {{
  const raw=(document.getElementById('bulk-phones')||{{}}).value||'';
  const numbers=raw.split(String.fromCharCode(10)).map(n=>n.trim()).filter(Boolean);
  if (!numbers.length) return;
  const res = await fetch('/api/call/bulk',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{numbers}})}});
  const d = await res.json();
  activeBulkJobId = d.job_id;
  document.getElementById('bulk-progress').innerHTML='<span style="color:var(--accent)">🚀 Campaign '+d.job_id+' — '+d.total+' numbers</span>';
  bulkPollTimer = setInterval(pollBulkStatus, 3000);
}}

async function pollBulkStatus() {{
  if (!activeBulkJobId) return;
  const d = await fetch('/api/call/bulk/'+activeBulkJobId).then(r=>r.json()).catch(()=>null);
  if (!d) return;
  const log=document.getElementById('outbound-log');
  log.innerHTML = d.results.map(r=>'<div style="padding:8px 12px;border-bottom:1px solid var(--border);font-size:12px;">📞 '+r.phone+' — <b style="color:'+(r.status==='dispatched'?'var(--green)':'var(--red)')+'">'+r.status+'</b></div>').join('');
  document.getElementById('bulk-progress').innerHTML='Progress: '+d.done+'/'+d.total+' — <b>'+d.status+'</b>';
  if (['completed','stopped'].includes(d.status)) {{ clearInterval(bulkPollTimer); activeBulkJobId=null; }}
}}

async function stopBulkCampaign() {{
  if (!activeBulkJobId) return;
  await fetch('/api/call/bulk/'+activeBulkJobId+'/stop',{{method:'POST'}});
  clearInterval(bulkPollTimer);
}}

// ── Demo Links ────────────────────────────────────────────────────────────────
async function loadDemos() {{
  const g=document.getElementById('demos-grid'); if(!g) return;
  g.innerHTML='<div style="color:var(--muted);padding:20px;">Loading...</div>';
  const demos=await fetch('/api/demo/list').then(r=>r.json()).catch(()=>[]);
  if (!demos.length) {{ g.innerHTML='<div style="color:var(--muted);padding:20px;">No demo links yet. Create one to share with prospects!</div>'; return; }}
  const LANG_NAMES = {{
    'auto':'Auto-detect 🌐','hi-IN':'Hindi','en-IN':'English','ta-IN':'Tamil',
    'te-IN':'Telugu','bn-IN':'Bengali','gu-IN':'Gujarati','kn-IN':'Kannada',
    'ml-IN':'Malayalam','mr-IN':'Marathi','pa-IN':'Punjabi','od-IN':'Odia','ur-IN':'Urdu'
  }};
  g.innerHTML=demos.map(d=>`
    <div class="demo-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <div style="font-weight:700;font-size:15px;">🎙️ ${{d.label||d.name||'Demo Link'}}</div>
        <span style="font-size:11px;background:rgba(108,99,255,0.15);color:#a78bfa;padding:2px 8px;border-radius:20px;">
          ${{LANG_NAMES[d.language||'auto']||d.language||'Auto'}}
        </span>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px;">
        🔗 /demo/${{d.slug||d.token}} &nbsp;·&nbsp; 📞 ${{d.total_sessions||0}} sessions &nbsp;·&nbsp;
        <span style="color:${{d.is_active?'#22c55e':'#ef4444'}}">●${{d.is_active?' Active':' Inactive'}}</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn btn-primary btn-sm" onclick="copyDemo('${{d.slug||d.token}}')">📋 Copy Link</button>
        <a class="btn btn-ghost btn-sm" href="/demo/${{d.slug||d.token}}" target="_blank" style="text-decoration:none;">👁 Preview</a>
        <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deleteDemo('${{d.slug||d.token}}')">🗑 Deactivate</button>
      </div>
    </div>`).join('');
}}

function copyDemo(token) {{
  const url=location.protocol+'//'+location.host+'/demo/'+token;
  navigator.clipboard.writeText(url).then(()=>alert('✅ Copied: '+url));
}}

async function deleteDemo(token) {{
  if (!confirm('Delete this demo link?')) return;
  await fetch('/api/demo/'+token,{{method:'DELETE'}}); loadDemos();
}}

function openDemoModal() {{ document.getElementById('demo-modal').classList.add('open'); }}
function closeDemoModal() {{ document.getElementById('demo-modal').classList.remove('open'); }}

async function createDemo() {{
  const g=id=>{{ const e=document.getElementById(id); return e?e.value:''; }};
  await fetch('/api/demo/create',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{label:g('dm-name'),language:g('dm-language')}})}});
  closeDemoModal(); loadDemos();
}}

// ── Boot ────────────────────────────────────────────────────────────────────
loadDashboard();
</script>
</body>
</html>"""

    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=True)
