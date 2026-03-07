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

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    config = read_config()

    try:
        import db
        active = db.get_active_agent()
    except Exception:
        active = None

    agent_name_display = (active or {}).get("name", "Voice Agent")
    agent_subtitle_display = (active or {}).get("subtitle", "AI Assistant")

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
    _vobiz_sip_domain = e(config.get("vobiz_sip_domain", ""))
    _vobiz_username   = e(config.get("vobiz_username", ""))
    _vobiz_password   = e(config.get("vobiz_password", ""))
    _vobiz_outbound_number = e(config.get("vobiz_outbound_number", ""))
    _vobiz_pool       = e(config.get("vobiz_number_pool", ""))

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
      <div class="brand-text">{agent_name_display}</div>
      <div class="brand-sub">{agent_subtitle_display}</div>
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
    <div class="nav-item" onclick="goTo('campaigns', this); loadCampaigns();"><span class="icon">📋</span> Campaigns</div>
    <div class="nav-item" onclick="goTo('sip-trunks', this); loadSipTrunks();"><span class="icon">🔌</span> SIP Trunks</div>
    <div class="nav-item" onclick="goTo('demos', this); loadDemos();"><span class="icon">🔗</span> Demo Links</div>
    <div class="nav-section" style="margin-top:12px;">Data</div>
    <div class="nav-item" onclick="goTo('logs', this); loadLogs();"><span class="icon">📞</span> Call Logs</div>
    <div class="nav-item" onclick="goTo('crm', this); loadCRM();"><span class="icon">👥</span> CRM Contacts</div>
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
    <!-- Voice Synthesis moved to Agent Settings -->
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

  <!-- ── Campaigns Page ── -->
  <div id="page-campaigns" class="page">
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div class="page-title">📋 Campaigns</div>
          <div class="page-sub">Create outbound calling campaigns, upload leads, and track progress</div>
        </div>
        <button class="btn btn-primary" onclick="openCampaignModal()">＋ New Campaign</button>
      </div>
    </div>
    <!-- Live Calls Panel -->
    <div class="section-card" style="margin-bottom:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <div class="section-title" style="margin:0;">📞 Live Calls <span id="live-calls-badge" style="font-size:12px;background:rgba(108,99,255,0.15);color:var(--accent);padding:2px 8px;border-radius:20px;margin-left:6px;">0 active</span></div>
        <div style="font-size:12px;color:var(--muted);" id="ws-status">⚪ Connecting...</div>
      </div>
      <div id="live-calls-table" style="font-size:13px;color:var(--muted);">No active calls right now.</div>
    </div>
    <div id="campaigns-list" style="display:grid;gap:12px;"></div>
  </div>

  <!-- ── SIP Trunks Page ── -->
  <div id="page-sip-trunks" class="page">
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div class="page-title">🔌 SIP Trunks</div>
          <div class="page-sub">Configure SIP trunks for masked outbound calling</div>
        </div>
        <button class="btn btn-primary" onclick="openSipTrunkModal()">＋ Add SIP Trunk</button>
      </div>
    </div>
    <div id="sip-trunks-list" style="display:grid;gap:12px;"></div>
  </div>

  <!-- ── SIP Trunk Modal ── -->
  <div id="sip-modal-overlay" onclick="closeSipTrunkModal(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
    <div style="background:var(--surface);border:1px solid rgba(108,99,255,0.3);border-radius:18px;padding:36px 32px;width:min(500px,94vw);max-height:90vh;overflow-y:auto;" onclick="event.stopPropagation()">
      <div style="font-size:18px;font-weight:700;margin-bottom:24px;">🔌 Add SIP Trunk</div>
      <div class="form-group">
        <label>Trunk Name <span style="color:#f87171">*</span></label>
        <input type="text" id="sip-name" placeholder="e.g. Vobiz Primary">
      </div>
      <div class="form-group">
        <label>Provider <span style="color:#f87171">*</span></label>
        <input type="text" id="sip-provider" placeholder="e.g. twilio, exotel, vobiz">
      </div>
      <div class="form-group">
        <label>SIP URI <span style="color:#f87171">*</span></label>
        <input type="text" id="sip-uri" placeholder="sip:user@sip.provider.com">
      </div>
      <div class="form-group">
        <label>Masked Caller ID (E.164)</label>
        <input type="text" id="sip-callerid" placeholder="+919876543210">
        <div class="hint">Number shown to the callee — leave blank to use provider default</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div class="form-group">
          <label>SIP Username</label>
          <input type="text" id="sip-username" placeholder="Optional">
        </div>
        <div class="form-group">
          <label>SIP Password</label>
          <input type="password" id="sip-password" placeholder="Optional">
        </div>
      </div>
      <div id="sip-modal-error" style="color:#f87171;font-size:13px;margin-bottom:12px;display:none;"></div>
      <div style="display:flex;gap:10px;margin-top:8px;">
        <button class="btn btn-primary" style="flex:1" onclick="submitSipTrunk()">Save Trunk</button>
        <button class="btn btn-ghost" onclick="closeSipTrunkModal()" style="width:100px;">Cancel</button>
      </div>
    </div>
  </div>

  <!-- ── Campaign Modal ── -->
  <div id="campaign-modal-overlay" onclick="closeCampaignModal(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
    <div style="background:var(--surface);border:1px solid rgba(108,99,255,0.3);border-radius:18px;padding:36px 32px;width:min(560px,94vw);max-height:90vh;overflow-y:auto;" onclick="event.stopPropagation()">
      <div style="font-size:18px;font-weight:700;margin-bottom:24px;">📋 New Campaign</div>
      <div class="form-group">
        <label>Campaign Name <span style="color:#f87171">*</span></label>
        <input type="text" id="camp-name" placeholder="e.g. March Follow-up Campaign">
      </div>
      <div class="form-group">
        <label>Agent</label>
        <select id="camp-agent-id" style="width:100%;padding:10px 14px;background:var(--bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;"><option value="">— No agent assigned —</option></select>
        <div class="hint">Which agent will handle these calls</div>
      </div>
      <div class="form-group">
        <label>SIP Trunk</label>
        <select id="camp-trunk-id" style="width:100%;padding:10px 14px;background:var(--bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;"><option value="">— Default trunk —</option></select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
        <div class="form-group">
          <label>Max Concurrent</label>
          <input type="number" id="camp-maxconc" value="5" min="1" max="50">
        </div>
        <div class="form-group">
          <label>Calls / min</label>
          <input type="number" id="camp-cpm" value="5" min="1" max="60">
        </div>
        <div class="form-group">
          <label>Max Retries</label>
          <input type="number" id="camp-maxretries" value="2" min="0" max="10">
        </div>
      </div>
      <div class="form-group" style="display:flex;align-items:center;gap:10px;">
        <input type="checkbox" id="camp-retry" style="width:auto;" checked>
        <label style="font-size:13px;margin:0;">Retry failed calls</label>
      </div>
      <div class="form-group">
        <label>Notes</label>
        <input type="text" id="camp-notes" placeholder="Optional internal notes">
      </div>
      <div id="camp-modal-error" style="color:#f87171;font-size:13px;margin-bottom:12px;display:none;"></div>
      <div style="display:flex;gap:10px;margin-top:8px;">
        <button class="btn btn-primary" style="flex:1" onclick="submitCampaign()">Create Campaign</button>
        <button class="btn btn-ghost" onclick="document.getElementById('campaign-modal-overlay').style.display='none'" style="width:100px;">Cancel</button>
      </div>
    </div>
  </div>

  <!-- ── Lead Upload Modal ── -->
  <div id="leads-modal-overlay" onclick="if(event.target===this)closeLeadsModal()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
    <div style="background:var(--surface);border:1px solid rgba(108,99,255,0.3);border-radius:18px;padding:36px 32px;width:min(500px,94vw);max-height:90vh;overflow-y:auto;" onclick="event.stopPropagation()">
      <div style="font-size:18px;font-weight:700;margin-bottom:8px;">📁 Upload Leads</div>
      <div style="font-size:13px;color:var(--muted);margin-bottom:20px;">CSV with <code>phone</code> column required. Optional: <code>name</code>, <code>email</code>. Extra columns go to custom_data.</div>
      <input type="hidden" id="leads-upload-campaign-id">
      <div class="form-group">
        <label>CSV File <span style="color:#f87171">*</span></label>
        <input type="file" id="leads-file" accept=".csv,.txt" style="width:100%;padding:10px 14px;background:var(--bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;">
      </div>
      <div id="leads-upload-result" style="font-size:13px;margin-bottom:12px;display:none;"></div>
      <div style="display:flex;gap:10px;margin-top:8px;">
        <button class="btn btn-primary" style="flex:1" onclick="uploadLeads()">Upload</button>
        <button class="btn btn-ghost" onclick="closeLeadsModal()" style="width:100px;">Close</button>
      </div>
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
    <div class="section-card">
      <div class="section-title">SIP Outbound & Masking</div>
      <div class="form-row">
        <div class="form-group"><label>Vobiz SIP Domain / IP</label><input type="text" id="vobiz_sip_domain" value="{_vobiz_sip_domain}"></div>
        <div class="form-group"><label>Vobiz SIP Username</label><input type="text" id="vobiz_username" value="{_vobiz_username}"></div>
        <div class="form-group"><label>Vobiz SIP Password</label><input type="password" id="vobiz_password" value="{_vobiz_password}"></div>
        <div class="form-group"><label>Default Outbound Caller ID</label><input type="text" id="vobiz_outbound_number" value="{_vobiz_outbound_number}"></div>
      </div>
      <div class="form-group" style="margin-top:12px;">
        <label>Masking Number Pool (Comma-separated)</label>
        <textarea id="vobiz_number_pool" rows="2" placeholder="+911234567890, +910987654321">{_vobiz_pool}</textarea>
        <div class="hint">If provided, outbound calls will randomly select one of these numbers as the Caller ID.</div>
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
  // Optimistically clear Loading...
  const tbody = document.getElementById('dash-table-body');
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr>';

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

  // ── Update stat cards ──────────────────────────────────────────────────
  if (stats !== null) {{
    const fmt = (v, suffix='') => (v !== null && v !== undefined) ? v + suffix : '0' + suffix;
    document.getElementById('stat-calls').textContent    = fmt(stats.total_calls);
    document.getElementById('stat-bookings').textContent = fmt(stats.total_bookings);
    document.getElementById('stat-duration').textContent = fmt(stats.avg_duration, 's');
    document.getElementById('stat-rate').textContent     = fmt(stats.booking_rate, '%');
  }} else {{
    ['stat-calls','stat-bookings','stat-duration','stat-rate'].forEach(id => {{
      document.getElementById(id).textContent = '!';
      document.getElementById(id).title = 'Could not load — check Supabase credentials';
    }});
  }}

  // ── Update calls table ─────────────────────────────────────────────────
  if (logs === null) {{
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#e06c75;">⚠ Could not load calls — check Supabase URL and KEY in API Credentials.</td></tr>';
    return;
  }}
  if (!logs.length) {{
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">No calls yet. Make a test call!</td></tr>';
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
          <button class="btn btn-ghost btn-sm" style="color:var(--red);margin-left:4px;" onclick="toggleDNC('${{log.phone_number}}')">🚫 Block</button>
        </td>
      </tr>`).join('');
  }} catch(e) {{
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#ef4444;">Error loading logs. Check Supabase credentials.</td></tr>';
  }}
}}

async function toggleDNC(phone) {{
  if (!confirm(`Add ${{phone}} to the Do-Not-Call (DNC) list? Outbound campaigns will skip this number.`)) return;
  try {{
    const r = await fetch('/api/dnc', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{phone: phone, action: 'add'}})
    }});
    if (r.ok) {{
      alert(`✅ ${{phone}} added to DNC list`);
    }} else {{
      const d = await r.json();
      alert(`❌ Error: ${{d.detail || 'Failed to add to DNC'}}`);
    }}
  }} catch(e) {{
    alert(`❌ Request failed: ${{e}}`);
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

  if (section === 'agent' || section === 'system') {{
    Object.assign(payload, {{
      agent_name: get('agentName'),
      opening_greeting: get('greeting'),
      first_line: get('first_line'),
      agent_instructions: get('agent_instructions') || get('instructions'),
      stt_min_endpointing_delay: parseFloat(get('stt_min_endpointing_delay')),
      tts_voice: get('ttsVoice') || get('tts_voice'),
      tts_language: get('tts_language'),
      stt_language: get('sttModel') || get('tts_language') || 'unknown',
    }});
  }} else if (section === 'models') {{
    Object.assign(payload, {{
      llm_model: get('llm_model'),
    }});
  }} else if (section === 'credentials') {{
    Object.assign(payload, {{
      livekit_url: get('livekit_url'), sip_trunk_id: get('sip_trunk_id'),
      livekit_api_key: get('livekit_api_key'), livekit_api_secret: get('livekit_api_secret'),
      openai_api_key: get('openai_api_key'), sarvam_api_key: get('sarvam_api_key'),
      cal_api_key: get('cal_api_key'), cal_event_type_id: get('cal_event_type_id'),
      telegram_bot_token: get('telegram_bot_token'), telegram_chat_id: get('telegram_chat_id'),
      supabase_url: get('supabase_url'), supabase_key: get('supabase_key'),
      vobiz_sip_domain: get('vobiz_sip_domain'), vobiz_username: get('vobiz_username'),
      vobiz_password: get('vobiz_password'), vobiz_outbound_number: get('vobiz_outbound_number'),
      vobiz_number_pool: get('vobiz_number_pool'),
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
        <button class="btn btn-ghost btn-sm" onclick='editAgent(${{JSON.stringify(a)}})'>✏ Edit</button>
        ${{a.id!=='default'?`<button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deleteAgent('${{a.id}}')">🗑</button>`:''}}
      </div>
    </div>`).join('');
}}

async function activateAgent(id) {{ await fetch('/api/agents/'+id+'/activate',{{method:'POST'}}); loadAgents(); }}
async function deleteAgent(id) {{
  if (!confirm('Delete this agent?')) return;
  await fetch('/api/agents/'+id,{{method:'DELETE'}}); loadAgents();
}}
function editAgent(agent) {{
  editingAgentId = agent.id;
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

// ── Campaigns ─────────────────────────────────────────────────────────────────
let _liveCalls = {{}};

async function loadCampaigns() {{
  const el = document.getElementById('campaigns-list'); if (!el) return;
  el.innerHTML = '<div style="color:var(--muted);padding:20px;">Loading...</div>';

  // Also populate agent + trunk selectors in the modal
  try {{
    const [agentsData, trunksData] = await Promise.all([
      fetch('/api/agents').then(r=>r.json()),
      fetch('/api/sip-trunks').then(r=>r.json()).catch(()=>({{trunks:[]}}))
    ]);
    const asel = document.getElementById('camp-agent-id');
    if (asel) {{
      asel.innerHTML = '<option value="">— No agent assigned —</option>' +
        (Array.isArray(agentsData)?agentsData:(agentsData.agents||[])).map(a=>`<option value="${{a.id}}">${{a.name}}${{a.is_active?' ★':''}}</option>`).join('');
    }}
    const tsel = document.getElementById('camp-trunk-id');
    if (tsel) {{
      tsel.innerHTML = '<option value="">— Default trunk —</option>' +
        (trunksData.trunks||[]).map(t=>`<option value="${{t.id}}">${{t.name}} (${{t.provider}})</option>`).join('');
    }}
  }} catch(e) {{ /* ignore */ }}

  const data = await fetch('/api/campaigns').then(r=>r.json()).catch(()=>({{campaigns:[]}}));
  const campaigns = data.campaigns || [];
  if (!campaigns.length) {{
    el.innerHTML = '<div class="section-card" style="color:var(--muted)">No campaigns yet. Click "+ New Campaign" to create one.</div>';
    return;
  }}

  // Load stats for each campaign
  const statsAll = await Promise.all(campaigns.map(c =>
    fetch(`/api/campaigns/${{c.id}}/stats`).then(r=>r.json()).catch(()=>({{leads:{{total:0,pending:0,calling:0,completed:0,failed:0}}}}))
  ));

  el.innerHTML = campaigns.map((c, idx) => {{
    const s = (statsAll[idx]||{{}}).leads || {{}};
    const statusColor = {{'active':'#4ade80','paused':'#fbbf24','completed':'#6b7280','draft':'#a78bfa'}}[c.status]||'#94a3b8';
    const isActive = c.status === 'active';
    return `
    <div class="section-card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <div style="font-weight:700;font-size:15px;">📋 ${{c.name}}</div>
            <span style="font-size:11px;font-weight:600;color:${{statusColor}};background:${{statusColor}}22;padding:2px 8px;border-radius:12px;text-transform:uppercase;">●&nbsp;${{c.status}}</span>
            ${{c.agent_name ? `<span style="font-size:11px;color:var(--muted);">${{c.agent_name}}</span>` : ''}}
          </div>
          <div style="margin-top:8px;display:flex;gap:20px;font-size:12px;color:var(--muted);flex-wrap:wrap;">
            <span>📋 Total: <strong style="color:var(--text);">${{s.total||0}}</strong></span>
            <span>⏳ Pending: <strong style="color:#fbbf24;">${{s.pending||0}}</strong></span>
            <span>📞 Calling: <strong style="color:#4ade80;">${{s.calling||0}}</strong></span>
            <span>✅ Done: <strong style="color:#6b7280;">${{s.completed||0}}</strong></span>
            <span>❌ Failed: <strong style="color:#f87171;">${{s.failed||0}}</strong></span>
          </div>
          ${{c.notes ? `<div style="font-size:12px;color:var(--muted);margin-top:4px;">📝 ${{c.notes}}</div>` : ''}}
        </div>
        <div style="display:flex;gap:8px;flex-shrink:0;flex-wrap:wrap;">
          <button class="btn btn-ghost" style="font-size:12px;padding:6px 12px;" onclick="openLeadsModal(${{c.id}})">📁 Leads</button>
          ${{isActive
            ? `<button class="btn btn-ghost" style="font-size:12px;padding:6px 12px;color:#fbbf24;" onclick="pauseCampaign(${{c.id}})">⏸ Pause</button>`
            : `<button class="btn btn-primary" style="font-size:12px;padding:6px 12px;" onclick="startCampaign(${{c.id}})">▶ Start</button>`
          }}
        </div>
      </div>
    </div>`;
  }}).join('');
}}

async function startCampaign(id) {{
  const r = await fetch(`/api/campaigns/${{id}}/start`, {{method:'POST'}});
  if (!r.ok) {{ alert('Failed to start campaign'); return; }}
  loadCampaigns();
}}

async function pauseCampaign(id) {{
  await fetch(`/api/campaigns/${{id}}/pause`, {{method:'POST'}});
  loadCampaigns();
}}

function openCampaignModal() {{
  document.getElementById('camp-name').value = '';
  document.getElementById('camp-maxconc').value = '5';
  document.getElementById('camp-cpm').value = '5';
  document.getElementById('camp-maxretries').value = '2';
  document.getElementById('camp-retry').checked = true;
  document.getElementById('camp-notes').value = '';
  document.getElementById('camp-modal-error').style.display = 'none';
  document.getElementById('campaign-modal-overlay').style.display = 'flex';
}}

function closeCampaignModal(e) {{
  if (e && e.target !== document.getElementById('campaign-modal-overlay')) return;
  document.getElementById('campaign-modal-overlay').style.display = 'none';
}}

async function submitCampaign() {{
  const name     = document.getElementById('camp-name').value.trim();
  const agentId  = document.getElementById('camp-agent-id')?.value || '';
  const trunkId  = document.getElementById('camp-trunk-id')?.value || '';
  const maxConc  = parseInt(document.getElementById('camp-maxconc').value) || 5;
  const cpm      = parseInt(document.getElementById('camp-cpm').value) || 5;
  const retries  = parseInt(document.getElementById('camp-maxretries').value) ?? 2;
  const retry    = document.getElementById('camp-retry')?.checked ?? true;
  const notes    = document.getElementById('camp-notes').value.trim();
  const errEl    = document.getElementById('camp-modal-error');
  if (!name) {{ errEl.textContent = 'Campaign name is required.'; errEl.style.display='block'; return; }}
  errEl.style.display = 'none';
  try {{
    const r = await fetch('/api/campaigns', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{
        name,
        agent_id: agentId || null,
        sip_trunk_id: trunkId ? parseInt(trunkId) : null,
        max_concurrent_calls: maxConc,
        calls_per_minute: cpm,
        max_retries: retries,
        retry_failed: retry,
        notes
      }})
    }});
    if (!r.ok) throw new Error('Server error ' + r.status);
    document.getElementById('campaign-modal-overlay').style.display = 'none';
    loadCampaigns();
  }} catch(e) {{ errEl.textContent = '❌ Failed: ' + e.message; errEl.style.display='block'; }}
}}

function openLeadsModal(campaignId) {{
  document.getElementById('leads-upload-campaign-id').value = campaignId;
  document.getElementById('leads-file').value = '';
  document.getElementById('leads-upload-result').style.display = 'none';
  document.getElementById('leads-modal-overlay').style.display = 'flex';
}}

function closeLeadsModal() {{
  document.getElementById('leads-modal-overlay').style.display = 'none';
}}

async function uploadLeads() {{
  const campaignId = document.getElementById('leads-upload-campaign-id').value;
  const fileInput  = document.getElementById('leads-file');
  const resultEl   = document.getElementById('leads-upload-result');
  if (!fileInput.files.length) {{ resultEl.textContent = '⚠ Please select a CSV file'; resultEl.style.color='#fbbf24'; resultEl.style.display='block'; return; }}
  resultEl.textContent = 'Uploading...';
  resultEl.style.color = 'var(--muted)';
  resultEl.style.display = 'block';
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  try {{
    const r = await fetch(`/api/campaigns/${{campaignId}}/leads/upload`, {{method:'POST', body:formData}});
    const d = await r.json();
    if (!r.ok) {{ resultEl.textContent = '❌ ' + (d.detail || 'Upload failed'); resultEl.style.color='#f87171'; return; }}
    resultEl.textContent = `✅ ${{d.message}}`;
    resultEl.style.color = '#4ade80';
    loadCampaigns();
  }} catch(e) {{ resultEl.textContent = '❌ ' + e.message; resultEl.style.color='#f87171'; }}
}}

// ── WebSocket Live Calls ──────────────────────────────────────────────────────
function initLiveCallsWS() {{
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${{proto}}://${{location.host}}/ws/calls`);
  const badge = document.getElementById('live-calls-badge');
  const tbl   = document.getElementById('live-calls-table');
  const wsStatus = document.getElementById('ws-status');

  ws.onopen = () => {{ if(wsStatus) wsStatus.textContent = '🟢 Connected'; }};
  ws.onclose = () => {{
    if(wsStatus) wsStatus.textContent = '🔴 Disconnected';
    setTimeout(initLiveCallsWS, 5000); // reconnect
  }};
  ws.onerror = () => {{ if(wsStatus) wsStatus.textContent = '⚠ WS Error'; }};

  ws.onmessage = (evt) => {{
    try {{
      const msg = JSON.parse(evt.data);
      if (msg.type === 'call_status') {{
        if (msg.status === 'calling') {{
          _liveCalls[msg.lead_id] = msg;
        }} else {{
          delete _liveCalls[msg.lead_id];
        }}
        renderLiveCalls();
      }}
    }} catch(e) {{ /* ignore */ }}
  }};
  // Keep-alive ping every 20s
  setInterval(() => {{ if(ws.readyState===1) ws.send('ping'); }}, 20000);
}}

function renderLiveCalls() {{
  const calls = Object.values(_liveCalls);
  const badge = document.getElementById('live-calls-badge');
  const tbl   = document.getElementById('live-calls-table');
  if (!tbl) return;
  if (badge) badge.textContent = `${{calls.length}} active`;
  if (!calls.length) {{ tbl.innerHTML = '<span style="color:var(--muted);">No active calls right now.</span>'; return; }}
  tbl.innerHTML = `<table style="width:100%;border-collapse:collapse;">
    <thead><tr style="border-bottom:1px solid var(--border);">
      <th style="padding:8px 12px;text-align:left;color:var(--muted);font-weight:500;font-size:12px;">Phone</th>
      <th style="padding:8px 12px;text-align:left;color:var(--muted);font-weight:500;font-size:12px;">Name</th>
      <th style="padding:8px 12px;text-align:left;color:var(--muted);font-weight:500;font-size:12px;">Room</th>
      <th style="padding:8px 12px;text-align:left;color:var(--muted);font-weight:500;font-size:12px;">Started</th>
    </tr></thead>
    <tbody>${{calls.map(c=>`<tr style="border-bottom:1px solid var(--border);">
      <td style="padding:8px 12px;">${{c.phone}}</td>
      <td style="padding:8px 12px;">${{c.name||'—'}}</td>
      <td style="padding:8px 12px;font-size:11px;color:var(--muted);"><code>${{c.room||''}}</code></td>
      <td style="padding:8px 12px;font-size:11px;color:var(--muted);">${{c.timestamp?.substring(11,19)||''}}</td>
    </tr>`).join('')}}</tbody>
  </table>`;
}}

// ── SIP Trunks ────────────────────────────────────────────────────────────────
async function loadSipTrunks() {{
  const el = document.getElementById('sip-trunks-list'); if (!el) return;
  el.innerHTML = '<div style="color:var(--muted);padding:20px;">Loading...</div>';
  const data = await fetch('/api/sip-trunks').then(r=>r.json()).catch(()=>({{trunks:[]}}));
  const trunks = data.trunks || [];
  if (!trunks.length) {{
    el.innerHTML = '<div class="section-card" style="color:var(--muted)">No SIP trunks yet. Click "+ Add SIP Trunk" to configure one.</div>';
    return;
  }}
  el.innerHTML = trunks.map(t => `
    <div class="section-card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div>
          <div style="font-weight:700;font-size:15px;">🔌 ${{t.name}}</div>
          <div style="font-size:12px;color:var(--muted);margin-top:4px;">Provider: ${{t.provider}} · Caller ID: ${{t.caller_id_number || 'N/A'}}</div>
          <div style="font-size:12px;color:var(--muted);">SIP URI: ${{t.sip_uri}}</div>
        </div>
        <button class="btn btn-ghost" onclick="deleteSipTrunk(${{t.id}})" style="color:#f87171;">🗑 Remove</button>
      </div>
    </div>
  `).join('');
}}

async function deleteSipTrunk(id) {{
  if (!confirm('Remove this SIP trunk?')) return;
  await fetch('/api/sip-trunks/'+id, {{method:'DELETE'}});
  loadSipTrunks();
}}

function openSipTrunkModal() {{
  ['sip-name','sip-provider','sip-uri','sip-callerid','sip-username','sip-password'].forEach(id => {{
    document.getElementById(id).value = '';
  }});
  document.getElementById('sip-modal-error').style.display = 'none';
  document.getElementById('sip-modal-overlay').style.display = 'flex';
}}
function closeSipTrunkModal(e) {{
  if (e && e.target !== document.getElementById('sip-modal-overlay')) return;
  document.getElementById('sip-modal-overlay').style.display = 'none';
}}
async function submitSipTrunk() {{
  const name = document.getElementById('sip-name').value.trim();
  const provider = document.getElementById('sip-provider').value.trim();
  const sip_uri = document.getElementById('sip-uri').value.trim();
  const caller_id_number = document.getElementById('sip-callerid').value.trim();
  const username = document.getElementById('sip-username').value.trim();
  const password = document.getElementById('sip-password').value.trim();
  const errEl = document.getElementById('sip-modal-error');
  if (!name || !provider || !sip_uri) {{ errEl.textContent = 'Name, Provider, and SIP URI are required.'; errEl.style.display='block'; return; }}
  errEl.style.display = 'none';
  try {{
    const r = await fetch('/api/sip-trunks', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{name, provider, sip_uri, caller_id_number, username, password}})
    }});
    if (!r.ok) throw new Error('Server error ' + r.status);
    document.getElementById('sip-modal-overlay').style.display = 'none';
    loadSipTrunks();
  }} catch(e) {{ errEl.textContent = '❌ Failed: ' + e.message; errEl.style.display='block'; }}
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
// Start live calls WebSocket (auto-reconnects)
initLiveCallsWS();

async function syncSidebarName() {{
  try {{
    const res = await fetch('/api/active-agent');
    if (!res.ok) return;
    const a = await res.json();
    const nameEl = document.querySelector('.brand-text');
    const subEl  = document.querySelector('.brand-sub');
    if (a.name && nameEl) nameEl.textContent = a.name;
    if (subEl) subEl.textContent = a.subtitle || 'AI Assistant';
  }} catch(e) {{}}
}}
setInterval(syncSidebarName, 5000);
syncSidebarName();
</script>
</body>
</html>"""

    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=True)
