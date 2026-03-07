import json
import logging
import os
import asyncio
import uuid
import csv
import io
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, Response
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(title="Med Spa AI Dashboard")

# ── WebSocket Connection Manager ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = ConnectionManager()

# In-memory map of running dialer tasks: campaign_id -> asyncio.Task
_dialer_tasks: dict = {}

@app.on_event("startup")
async def startup_event():
    import db
    try:
        db.init_db()
        logger.info("Database initialized successfully on startup.")
    except Exception as e:
        logger.error(f"Failed to initialize database on startup: {e}")

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
        "vobiz_sip_domain": get_val("vobiz_sip_domain", "VOBIZ_SIP_DOMAIN", ""),
        "vobiz_username": get_val("vobiz_username", "VOBIZ_USERNAME", ""),
        "vobiz_password": get_val("vobiz_password", "VOBIZ_PASSWORD", ""),
        "vobiz_outbound_number": get_val("vobiz_outbound_number", "VOBIZ_OUTBOUND_NUMBER", ""),
        "vobiz_number_pool": get_val("vobiz_number_pool", "VOBIZ_NUMBER_POOL", ""),
        **config
    }

def write_config(data):
    config = read_config()
    config.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ── Part C: Agent Helpers & Active Agent Route ──
def get_active_agent_name():
    import db
    ag = db.get_active_agent()
    return ag["name"] if ag else "AI Assistant"

def get_active_agent_subtitle():
    import db
    ag = db.get_active_agent()
    return ag.get("subtitle", "Voice Agent") if ag else "Voice Agent"

@app.get("/api/active-agent")
def api_active_agent():
    return {"name": get_active_agent_name(), "subtitle": get_active_agent_subtitle()}

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
    config = read_config()
    # Prefer env vars (Coolify) over config.json
    if not os.environ.get("SUPABASE_URL"):
        os.environ["SUPABASE_URL"] = config.get("supabase_url", "")
    if not os.environ.get("SUPABASE_KEY"):
        os.environ["SUPABASE_KEY"] = config.get("supabase_key", "")
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
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
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
        return db.fetch_bookings()
    except Exception as e:
        logger.error(f"Error fetching bookings: {e}")
        return []

@app.get("/api/stats")
async def api_get_stats():
    import db
    try:
        return db.fetch_stats()
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}

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
    import db
    if db.is_in_dnc(phone_number):
        logger.warning(f"Blocked outbound call to {phone_number} (DNC)")
        raise HTTPException(403, "Number is on the Do-Not-Call list")
        
    config = read_config()
    url = config.get("livekit_url") or os.getenv("LIVEKIT_URL", "")
    api_key = config.get("livekit_api_key") or os.getenv("LIVEKIT_API_KEY", "")
    api_secret = config.get("livekit_api_secret") or os.getenv("LIVEKIT_API_SECRET", "")
    if not (url and api_key and api_secret):
        raise HTTPException(400, "LiveKit credentials not configured")
        
    sip_trunk_id = config.get("sip_trunk_id", "")
    
    # --- Masking Logic ---
    from_number = config.get("vobiz_outbound_number", "").strip()
    pool_str = config.get("vobiz_number_pool", "")
    if pool_str:
        pool = [n.strip() for n in pool_str.replace(" ", "").split(",") if n.strip()]
        if pool:
            import random
            from_number = random.choice(pool)
            logger.info(f"Picked random mask number: {from_number}")
            
    try:
        import random
        from livekit import api as lk_api_mod
        lk = lk_api_mod.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
        room = f"call-{phone_number.replace('+','')}-{random.randint(1000,9999)}"
        
        md = {"phone_number": phone_number}
        if sip_trunk_id:
            md["sip_trunk_id"] = sip_trunk_id
        if from_number:
            md["from_number"] = from_number
            
        dispatch = await lk.agent_dispatch.create_dispatch(
            lk_api_mod.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room,
                metadata=json.dumps(md)
            )
        )
        await lk.aclose()
        return {"status": "dispatched", "dispatch_id": dispatch.id, "room": room, "phone": phone_number, "mask": from_number}
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
    sip_trunk_id = config.get("sip_trunk_id", "")
    
    # --- Masking Preparation ---
    base_from_number = config.get("vobiz_outbound_number", "").strip()
    pool_str = config.get("vobiz_number_pool", "")
    pool = [n.strip() for n in pool_str.replace(" ", "").split(",") if n.strip()] if pool_str else []
    
    import db
    for phone in numbers:
        if bulk_campaigns.get(job_id, {}).get("status") == "stopped":
            break
            
        if db.is_in_dnc(phone):
            logger.warning(f"Skipping {phone} in bulk campaign (DNC)")
            bulk_campaigns[job_id]["results"].append({"phone": phone, "status": "skipped (DNC)"})
            bulk_campaigns[job_id]["done"] += 1
            continue
            
        result = {"phone": phone, "status": "pending"}
        try:
            import random
            from livekit import api as lk_api_mod
            lk = lk_api_mod.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
            room = f"bulk-{phone.replace('+','')}-{random.randint(1000,9999)}"
            
            # Select trunk/mask dynamically for each call
            from_number = random.choice(pool) if pool else base_from_number
            md = {"phone_number": phone}
            if sip_trunk_id: md["sip_trunk_id"] = sip_trunk_id
            if from_number: md["from_number"] = from_number
            
            await lk.agent_dispatch.create_dispatch(
                lk_api_mod.CreateAgentDispatchRequest(
                    agent_name="outbound-caller", room=room,
                    metadata=json.dumps(md)
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

@app.post("/api/dnc")
async def api_dnc_toggle(request: Request):
    data = await request.json()
    phone = data.get("phone", "").strip()
    action = data.get("action", "add") # 'add' or 'remove'
    if not phone:
        raise HTTPException(400, "Phone number required")
    
    import db
    if action == "add":
        success = db.add_to_dnc(phone, reason="Added via Dashboard")
    else:
        success = db.remove_from_dnc(phone)
        
    if success:
        return {"status": "success", "phone": phone, "action": action}
    else:
        raise HTTPException(500, "Database operation failed")

# ── SIP Trunks API ────────────────────────────────────────────────────────────

@app.get("/api/sip-trunks")
async def api_get_sip_trunks():
    import db
    return {"trunks": db.get_sip_trunks()}

@app.post("/api/sip-trunks")
async def api_create_sip_trunk(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    provider = data.get("provider", "").strip()
    sip_uri = data.get("sip_uri", "").strip()
    username = data.get("username", "").strip() or None
    password = data.get("password", "").strip() or None
    caller_id_number = data.get("caller_id_number", "").strip() or None
    if not name or not provider or not sip_uri:
        raise HTTPException(400, "name, provider, and sip_uri are required")
    import db
    try:
        trunk = db.create_sip_trunk(name, provider, sip_uri, username, password, caller_id_number)
        return trunk
    except Exception as e:
        raise HTTPException(500, f"Failed to create SIP trunk: {str(e)}")

@app.delete("/api/sip-trunks/{trunk_id}")
async def api_delete_sip_trunk(trunk_id: int):
    import db
    ok = db.delete_sip_trunk(trunk_id)
    return {"status": "deleted" if ok else "error"}

# ── WebSocket: Live Call Status ───────────────────────────────────────────────

@app.websocket("/ws/calls")
async def websocket_calls(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive ping/pong
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# ── Campaigns API ─────────────────────────────────────────────────────────────

@app.get("/api/campaigns")
async def api_get_campaigns():
    import db
    return {"campaigns": db.get_campaigns()}

@app.get("/api/campaigns/{campaign_id}")
async def api_get_campaign(campaign_id: int):
    import db
    c = db.get_campaign_full(campaign_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c

@app.post("/api/campaigns")
async def api_create_campaign(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    import db
    campaign = db.create_campaign(
        name=name,
        phone_numbers=data.get("phone_numbers", "").strip(),
        sip_trunk_id=data.get("sip_trunk_id") or None,
        max_concurrent_calls=int(data.get("max_concurrent_calls", 5)),
        notes=data.get("notes", "").strip() or None,
        agent_id=data.get("agent_id") or None,
        calls_per_minute=int(data.get("calls_per_minute", 5)),
        retry_failed=bool(data.get("retry_failed", True)),
        max_retries=int(data.get("max_retries", 2)),
    )
    if not campaign:
        raise HTTPException(500, "Failed to create campaign")
    return campaign

@app.patch("/api/campaigns/{campaign_id}")
async def api_update_campaign(campaign_id: int, request: Request):
    data = await request.json()
    status = data.get("status", "")
    if status not in ("draft", "scheduled", "active", "running", "paused", "completed"):
        raise HTTPException(400, "Invalid status")
    import db
    ok = db.update_campaign_status(campaign_id, status)
    return {"status": "updated" if ok else "error"}

@app.post("/api/campaigns/{campaign_id}/start")
async def api_start_campaign(campaign_id: int):
    """Mark campaign active and launch the dialer engine as an asyncio task."""
    import db
    ok = db.update_campaign_status(campaign_id, "active")
    if not ok:
        raise HTTPException(500, "Failed to start campaign")

    # Cancel any existing dialer task for this campaign
    if campaign_id in _dialer_tasks:
        existing_task = _dialer_tasks.get(campaign_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()

    from dialer import run_dialer_for_campaign
    task = asyncio.create_task(
        run_dialer_for_campaign(campaign_id, ws_manager.broadcast)
    )
    _dialer_tasks[campaign_id] = task
    logger.info(f"[CAMPAIGN] Dialer started for campaign {campaign_id}")
    return {"status": "started", "campaign_id": campaign_id}

@app.post("/api/campaigns/{campaign_id}/pause")
async def api_pause_campaign(campaign_id: int):
    """Pause campaign — dialer loop detects the status change and stops."""
    import db
    ok = db.update_campaign_status(campaign_id, "paused")
    if not ok:
        raise HTTPException(500, "Failed to pause campaign")
    # Cancel the running task if present
    if campaign_id in _dialer_tasks:
        task = _dialer_tasks.get(campaign_id)
        if task and not task.done():
            task.cancel()
        del _dialer_tasks[campaign_id]
    return {"status": "paused", "campaign_id": campaign_id}

# ── Leads API ─────────────────────────────────────────────────────────────────

@app.post("/api/campaigns/{campaign_id}/leads/upload")
async def api_upload_leads(campaign_id: int, file: UploadFile = File(...)):
    """
    Upload a CSV file with leads for a campaign.
    Required column: phone
    Optional columns: name, email  (all other columns stored in custom_data JSONB)
    """
    import db
    # Ensure campaign exists
    c = db.get_campaign_full(campaign_id)
    if not c:
        raise HTTPException(404, "Campaign not found")

    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    inserted = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader):
        phone = (row.get("phone") or row.get("Phone") or row.get("PHONE") or "").strip()
        if not phone:
            skipped += 1
            continue
        name  = (row.get("name")  or row.get("Name")  or "").strip()
        email = (row.get("email") or row.get("Email") or "").strip()
        custom = {k: v for k, v in row.items()
                  if k.lower() not in ("phone", "name", "email")}
        try:
            db.create_lead(campaign_id, phone, name, email, custom)
            inserted += 1
        except Exception as e:
            errors.append(f"Row {i+2}: {e}")

    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors[:10],  # cap error list
        "message": f"Uploaded {inserted} leads, skipped {skipped} empty rows",
    }

@app.get("/api/campaigns/{campaign_id}/leads")
async def api_get_leads(campaign_id: int, status: Optional[str] = None):
    import db
    leads = db.get_leads(campaign_id, status=status)
    return {"leads": leads, "total": len(leads)}

@app.get("/api/campaigns/{campaign_id}/stats")
async def api_campaign_stats(campaign_id: int):
    import db
    stats = db.get_leads_stats(campaign_id)
    c = db.get_campaign_full(campaign_id)
    return {
        "campaign_id": campaign_id,
        "status": c.get("status") if c else "unknown",
        "leads": stats,
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
    lk_url        = os.getenv("LIVEKIT_URL", "")

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

        # Step 2 — Generate visitor token
        token = (
            lk_api.AccessToken(lk_api_key, lk_api_secret)
            .with_identity(f"visitor-{secrets.token_hex(4)}")
            .with_name("Demo Visitor")
            .with_grants(lk_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            ))
            .to_jwt()
        )
    except Exception as e:
        logger.error(f"[DEMO] Token/Dispatch error: {e}")
        raise HTTPException(500, f"Token generation failed: {e}")

    return {"token": token, "room": room_name, "ws_url": lk_url}

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
    <title>{label} — Live AI Demo</title>
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
        .mic-bar-wrap {{
            margin-top: 16px; display: none; align-items: center; gap: 10px;
            font-size: 12px; color: #8b949e;
        }}
        .mic-bar-wrap.visible {{ display: flex; }}
        .mic-bar {{
            flex: 1; height: 6px; background: rgba(255,255,255,0.08);
            border-radius: 3px; overflow: hidden;
        }}
        .mic-fill {{
            height: 100%; width: 0%;
            background: linear-gradient(90deg, #22c55e, #16a34a);
            border-radius: 3px; transition: width 0.08s ease;
        }}
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

        <div class="mic-bar-wrap" id="micBarWrap">
            🎤
            <div class="mic-bar">
                <div class="mic-fill" id="micFill"></div>
            </div>
        </div>

        <p class="hint">🔒 Your audio is private and not permanently stored.<br>Microphone access is required to speak with the agent.</p>
        <p class="powered">Powered by AI Voice Agent Platform</p>
    </div>

    <audio id="agentAudio" autoplay playsinline style="display:none"></audio>

    <script>
        const SLUG = "{slug}";
        let room = null;
        let micAnalyser = null, micAnimFrame = null;

        function setStatus(text, state) {{
            document.getElementById('statusText').textContent = text;
            const dot = document.getElementById('statusDot');
            dot.className = 'dot' + (state ? ' ' + state : '');
        }}

        function startMicVisualizer(stream) {{
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const src = ctx.createMediaStreamSource(stream);
            micAnalyser = ctx.createAnalyser();
            micAnalyser.fftSize = 256;
            src.connect(micAnalyser);
            const data = new Uint8Array(micAnalyser.frequencyBinCount);
            const fill = document.getElementById('micFill');
            document.getElementById('micBarWrap').classList.add('visible');
            function tick() {{
                micAnalyser.getByteFrequencyData(data);
                const avg = data.reduce((a,b)=>a+b,0)/data.length;
                fill.style.width = Math.min(100, avg * 2) + '%';
                micAnimFrame = requestAnimationFrame(tick);
            }}
            tick();
        }}

        function stopMicVisualizer() {{
            if (micAnimFrame) cancelAnimationFrame(micAnimFrame);
            document.getElementById('micBarWrap').classList.remove('visible');
        }}

        function attachRemoteAudio(track) {{
            const audio = document.getElementById('agentAudio');
            const ms = new MediaStream([track.mediaStreamTrack]);
            audio.srcObject = ms;
            audio.play().catch(e => console.warn('Autoplay blocked:', e));
        }}

        async function startDemo() {{
            const startBtn = document.getElementById('startBtn');
            const stopBtn  = document.getElementById('stopBtn');
            startBtn.disabled = true;
            setStatus('Connecting…', 'connecting');

            try {{
                const res = await fetch('/api/demo/token/' + SLUG);
                if (!res.ok) throw new Error('Demo link expired or invalid.');
                const {{ token, room: roomName, ws_url }} = await res.json();

                room = new LivekitClient.Room({{
                    adaptiveStream: true,
                    dynacast: true,
                }});

                room.on(LivekitClient.RoomEvent.Disconnected, () => {{
                    setStatus('Call ended.', '');
                    startBtn.style.display = 'flex';
                    startBtn.disabled = false;
                    stopBtn.style.display = 'none';
                    stopMicVisualizer();
                }});

                // Detect agent joining — any participant that is NOT our visitor is the agent
                room.on(LivekitClient.RoomEvent.ParticipantConnected, (p) => {{
                    if (!p.identity.startsWith('visitor-')) {{
                        setStatus('Agent is live — speak now 🎙️', 'live');
                    }}
                }});

                // If agent is already in the room when we join, trigger immediately
                room.on(LivekitClient.RoomEvent.Connected, () => {{
                    const remoteParticipants = [...room.remoteParticipants.values()];
                    const agentPresent = remoteParticipants.some(p => !p.identity.startsWith('visitor-'));
                    if (agentPresent) {{
                        setStatus('Agent is live — speak now 🎙️', 'live');
                    }} else {{
                        setStatus('Waiting for agent to join…', 'connecting');
                    }}
                }});

                // Subscribe to remote audio tracks for TTS playback
                room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, pub, participant) => {{
                    if (track.kind === LivekitClient.Track.Kind.Audio && !participant.identity.startsWith('visitor-')) {{
                        attachRemoteAudio(track);
                    }}
                }});

                await room.connect(ws_url, token);

                // Enable mic and start visualizer
                const stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
                startMicVisualizer(stream);
                await room.localParticipant.setMicrophoneEnabled(true);

                startBtn.style.display = 'none';
                stopBtn.style.display = 'flex';

            }} catch (err) {{
                setStatus('⚠ ' + err.message, 'error');
                startBtn.disabled = false;
                stopMicVisualizer();
            }}
        }}

        async function stopDemo() {{
            if (room) {{ await room.disconnect(); room = null; }}
            stopMicVisualizer();
            document.getElementById('startBtn').style.display = 'flex';
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').style.display = 'none';
            setStatus('Call ended — click to start again', '');
        }}
    </script>
</body>
</html>""")

import db

# ── Agent Management Endpoints ─────────────────────────────────────────────────

@app.get("/api/agents")
def api_get_agents():
    import db
    return {"agents": db.get_all_agents()}

@app.post("/api/agents")
async def api_create_agent(req: Request):
    import db
    data = await req.json()
    new_agent = db.create_agent(
        name=data.get("name", "New Agent"),
        subtitle=data.get("subtitle", ""),
        config=data.get("config", {}),
        stt_provider=data.get("stt_provider", "sarvam"),
        phone_numbers=data.get("phone_numbers", [])
    )
    return {"status": "ok", "agent": new_agent}

@app.put("/api/agents/{agent_id}")
async def api_update_agent(agent_id: str, req: Request):
    import db
    data = await req.json()
    updated = db.update_agent(
        agent_id,
        name=data.get("name"),
        subtitle=data.get("subtitle"),
        config=data.get("config"),
        stt_provider=data.get("stt_provider"),
        phone_numbers=data.get("phone_numbers")
    )
    return {"status": "ok", "agent": updated}

@app.post("/api/agents/{agent_id}/activate")
def api_activate_agent(agent_id: str):
    import db
    db.set_active_agent(agent_id)
    return {"status": "activated"}

@app.delete("/api/agents/{agent_id}")
def api_delete_agent(agent_id: str):
    import db
    db.delete_agent(agent_id)
    return {"status": "deleted"}

# ── Main Dashboard HTML ────────────────────────────────────────────────────────

BASE_HEAD = """
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/recharts/umd/Recharts.js"></script>
  <script src="https://unpkg.com/babel-standalone@6/babel.min.js"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background-color: #f3f4f6; color: #111827; }
  </style>
"""

def render_sidebar(active="dashboard"):
    ag_name = get_active_agent_name()
    ag_sub = get_active_agent_subtitle()
    return f"""
    <div class="w-64 bg-gray-900 text-white min-h-screen p-4 flex flex-col">
        <div class="mb-8">
            <h1 class="text-xl font-bold">{ag_name}</h1>
            <p class="text-sm text-gray-400">{ag_sub}</p>
        </div>
        <nav class="flex-1 space-y-2">
            <a href="/" class="block px-4 py-2 rounded {'bg-gray-800' if active=='dashboard' else 'hover:bg-gray-800'}">Dashboard</a>
            <a href="/agents" class="block px-4 py-2 rounded {'bg-gray-800' if active=='agents' else 'hover:bg-gray-800'}">Agent Library</a>
        </nav>
    </div>
    """

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    import db
    stats = db.get_dashboard_stats()
    recent_calls = db.get_recent_call_logs(10)
    
    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Dashboard | AI Voice Agent</title>
  {BASE_HEAD}
</head>
<body class="flex min-h-screen">
  {render_sidebar("dashboard")}
  
  <main class="flex-1 p-8 overflow-auto">
    <h2 class="text-2xl font-bold mb-6">Overview</h2>
    
    <div class="grid grid-cols-3 gap-6 mb-8">
      <div class="bg-white p-6 rounded-lg shadow">
         <h3 class="text-gray-500 text-sm font-medium">Total Calls</h3>
         <p class="text-3xl font-bold mt-2">{stats['total_calls']}</p>
      </div>
      <div class="bg-white p-6 rounded-lg shadow">
         <h3 class="text-gray-500 text-sm font-medium">Active Campaigns</h3>
         <p class="text-3xl font-bold mt-2">{stats['active_campaigns']}</p>
      </div>
      <div class="bg-white p-6 rounded-lg shadow">
         <h3 class="text-gray-500 text-sm font-medium">Total Agents</h3>
         <p class="text-3xl font-bold mt-2">{stats['total_agents']}</p>
      </div>
    </div>
    
    <div class="bg-white rounded-lg shadow p-6">
      <h3 class="text-lg font-medium mb-4">Recent Calls</h3>
      <table class="w-full text-left">
        <thead>
          <tr class="text-gray-500 border-b">
            <th class="pb-3 font-medium">Phone</th>
            <th class="pb-3 font-medium">Duration</th>
            <th class="pb-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {"".join([f'<tr class="border-b last:border-0"><td class="py-3">{c.get("phone_number","")}</td><td class="py-3">{c.get("duration_seconds",0)}s</td><td class="py-3">{"Booked" if c.get("was_booked") else "Completed"}</td></tr>' for c in recent_calls])}
        </tbody>
      </table>
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html)

@app.get("/agents", response_class=HTMLResponse)
async def get_agents_page():
    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Agent Library | AI Voice Agent</title>
  {BASE_HEAD}
</head>
<body class="flex min-h-screen">
  {render_sidebar("agents")}
  
  <main class="flex-1 p-8 overflow-auto" id="react-root"></main>
  
""" + """
  <script type="text/babel">
    const { useState, useEffect } = React;
    
    function AgentLibrary() {
        const [agents, setAgents] = useState([]);
        const [loading, setLoading] = useState(true);
        
        const loadAgents = async () => {
            setLoading(true);
            try {
                const res = await fetch('/api/agents');
                const data = await res.json();
                setAgents(data.agents || []);
            } catch(e) {
                console.error(e);
            } finally { setLoading(false); }
        };
        
        useEffect(() => { loadAgents(); }, []);
        
        const handleActivate = async (id) => {
            await fetch(`/api/agents/${id}/activate`, { method: 'POST' });
            window.location.reload();
        };
        
        return (
            <div>
                <div className="flex justify-between items-center mb-6">
                    <h2 className="text-2xl font-bold">Agent Library</h2>
                </div>
                
                {loading ? <p>Loading agents...</p> : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {agents.map(a => (
                            <div key={a.id} className="bg-white p-6 rounded-lg shadow border border-gray-100 flex flex-col">
                                <div className="flex justify-between items-start mb-4">
                                    <div>
                                        <h3 className="text-lg font-bold">{a.name}</h3>
                                        <p className="text-sm text-gray-500">{a.subtitle}</p>
                                    </div>
                                    {a.is_active && (
                                        <span className="bg-green-100 text-green-800 text-xs px-2 py-1 rounded-full font-medium">Active</span>
                                    )}
                                </div>
                                <div className="text-sm text-gray-600 space-y-2 mb-6 flex-1">
                                    <p><strong>Provider:</strong> {a.stt_provider || 'sarvam'}</p>
                                    <p><strong>Configured Phones:</strong> {(a.phone_numbers || []).join(', ') || 'None'}</p>
                                </div>
                                <div className="mt-auto">
                                    {!a.is_active && (
                                        <button onClick={() => handleActivate(a.id)} className="w-full bg-blue-50 text-blue-600 hover:bg-blue-100 font-medium py-2 rounded transition">
                                            Set as Active Agent
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
    }
    
    const root = ReactDOM.createRoot(document.getElementById('react-root'));
    root.render(<AgentLibrary />);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=True)
