# 🧠 RapidXAI Voice Agent — Complete Agent Knowledge Base

> **Last Updated:** 2026-03-04  
> **Purpose:** Complete time-machine context file. Give this to any new AI agent to instantly understand the full codebase, architecture, all bug history, all recent fixes, env variables, infra, and next steps.

---

## 📦 Project Overview

**Name:** RapidXAI Voice Agent (a.k.a. "InboundAIVoice")  
**Purpose:** A fully automated AI inbound/outbound voice call system for a Med Spa (Daisy's Med Spa). The AI agent handles calls, speaks in Hindi/English/Tamil (Hinglish naturally), books appointments on Cal.com, and sends Telegram notifications. It is deployed on **Coolify** via Docker Compose and backed by a **Postgres** database.

**Repository:** `https://github.com/toprmrproducer/AI-Inbound-Voice-2`  
**Production Branch:** `main`

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────━━┐
│                     COOLIFY DEPLOYMENT                             │
│                                                                     │
│   ┌───────────────────┐        ┌───────────────────────────────┐  │
│   │  ui_server.py     │        │  agent.py                     │  │
│   │  (FastAPI - port  │        │  (LiveKit Worker)             │  │
│   │   8080)           │        │                               │  │
│   │                   │        │  - Handles all calls (in/out) │  │
│   │  - Web Dashboard  │        │  - STT: Sarvam saaras:v3      │  │
│   │  - config API     │        │  - TTS: Sarvam bulbul:v3      │  │
│   │  - Agents UI      │        │  - LLM: OpenAI gpt-4o-mini    │  │
│   │  - SIP trunks UI  │        │  - Books via Cal.com          │  │
│   │  - Call logs      │        │  - Notifies via Telegram      │  │
│   └────────┬──────────┘        └──────────────┬────────────────┘  │
│            │                                   │                   │
│            └──────────────┬────────────────────┘                  │
│                           │                                        │
│                    ┌──────▼──────────┐                            │
│                    │  db.py          │                            │
│                    │  PostgreSQL DB  │                            │
│                    │  (Coolify)      │                            │
│                    └─────────────────┘                            │
└────────────────────────────────────────────────────────────────────┘

External Services:
  🎙️ LiveKit Cloud      → wss://testing-yt-d2ot6dgy.livekit.cloud
  🗣️ Sarvam AI          → wss://api.sarvam.ai (STT + TTS)
  🧠 OpenAI             → api.openai.com (LLM: gpt-4o-mini)
  📅 Cal.com            → api.cal.com (appointment booking)
  📨 Telegram Bot       → api.telegram.org (notifications)
  📞 Vobiz SIP          → d575a830.sip.vobiz.ai (phone calls)
  🗃️ Cloudflare R2      → call-recordings bucket (audio files)
  📊 Groq               → api.groq.com (Llama 3.3 70b summaries)
```

---

## 📁 File Structure

```
InboundAIVoice-main 2/
├── agent.py              ← MAIN WORKER: All call logic, LLM, STT, TTS, tools
├── ui_server.py          ← DASHBOARD: FastAPI web server (port 8080)
├── db.py                 ← DATABASE: All Postgres query functions
├── notify.py             ← NOTIFICATIONS: Telegram & WhatsApp alerts
├── calendar_tools.py     ← BOOKING: Cal.com API wrapper
├── storage.py            ← STORAGE: Cloudflare R2 recording upload
├── make_call.py          ← UTIL: Trigger outbound calls via LiveKit
├── setup_trunk.py        ← UTIL: Create LiveKit SIP trunks via API
├── config.json           ← RUNTIME CONFIG: Read by agent at call start
├── configs/
│   └── default.json      ← Fallback config if no per-number config
├── .env                  ← LOCAL ENV VARS (gitignored, not in Docker)
├── Dockerfile            ← Docker build, runs supervisord
├── supervisord.conf      ← Runs both ui_server and agent worker
├── requirements.txt      ← Python dependencies
└── AGENT_KNOWLEDGE_BASE.md ← THIS FILE
```

---

## 🔑 Environment Variables (Complete Reference)

> ⚠️ `.env` is gitignored. All of these MUST be set in Coolify for each service.

### LiveKit (required for all call handling)
| Variable | Value | Notes |
|---|---|---|
| `LIVEKIT_URL` | `wss://testing-yt-d2ot6dgy.livekit.cloud` | LiveKit cloud URL |
| `LIVEKIT_API_KEY` | `APIXA6Q5NCZVwPe` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `q4fbwTNTagqY3ObL...` | LiveKit API secret |
| `SIP_TRUNK_ID` | `ST_7HUFap76wdDT` | **Critical for outbound. Created 2026-03-04.** |
| `OUTBOUND_TRUNK_ID` | `ST_7HUFap76wdDT` | Same as SIP_TRUNK_ID, alternative env name |

### AI Models
| Variable | Value | Notes |
|---|---|---|
| `OPENAI_API_KEY` | `sk-proj-OZzBP383l5bCkUfv...` | Used for LLM (gpt-4o-mini) and optional TTS |
| `SARVAM_API_KEY` | `sk_aci4iu7i_jDjsODMf1vuq...` | **Critical for STT+TTS. Must be in Coolify env.** |
| `GROQ_API_KEY` | `gsk_tJhLeTrrkMef68Ra3I...` | Llama 3.3 70b for cheap call summaries |
| `DEEPGRAM_API_KEY` | `d8f970afdf0a87f8b64ad5...` | Optional STT alternative |

### SIP / Phone
| Variable | Value | Notes |
|---|---|---|
| `VOBIZ_SIP_DOMAIN` | `d575a830.sip.vobiz.ai` | SIP trunk domain |
| `VOBIZ_USERNAME` | `abc@12345` | SIP auth username |
| `VOBIZ_PASSWORD` | `abc@12345` | SIP auth password |
| `VOBIZ_OUTBOUND_NUMBER` | `+918049280319` | Caller ID for outbound |
| `DEFAULT_TRANSFER_NUMBER` | `+919307512816` | Where to transfer if human requested |

### Database
| Variable | Value | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:3VgdwPf4dp...@ycs80kcwoo048kkck...` | Postgres connection string |

### Notifications
| Variable | Value | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `8508875922:AAFaHn8M08qtxF_rVrE8Ib7G08fHUf_q_zo` | Bot token |
| `TELEGRAM_CHAT_ID` | `6933824863` | Chat ID to receive alerts |

### Calendar / Booking
| Variable | Value | Notes |
|---|---|---|
| `CAL_API_KEY` | `cal_live_fd032f5880ba3ef...` | Cal.com API key |
| `CAL_EVENT_TYPE_ID` | `4835163` | Event type ID for appointment slots |

### Storage
| Variable | Value | Notes |
|---|---|---|
| `R2_ENDPOINT` | `https://cd8c7bf0...r2.cloudflarestorage.com` | Cloudflare R2 |
| `R2_ACCESS_KEY` | `065a369d4d...` | R2 credentials |
| `R2_SECRET_KEY` | `5dd391a48c...` | R2 credentials |
| `R2_BUCKET` | `call-recordings` | Bucket name |
| `R2_PUBLIC_URL` | `https://pub-a199...r2.dev` | Public CDN URL |
| `PUBLIC_BASE_URL` | `https://rapid-xai.com` | App domain |

---

## 🗄️ Database Schema (PostgreSQL)

All tables are created by `db.init_db()` which runs automatically at startup of BOTH `ui_server.py` (via FastAPI `@app.on_event("startup")`) and `agent.py` (at worker start).

```sql
-- Main call records
CREATE TABLE call_logs (
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

-- Per-turn conversation transcript
CREATE TABLE call_transcripts (
    id SERIAL PRIMARY KEY,
    call_room_id TEXT NOT NULL,
    phone TEXT,
    role TEXT CHECK (role IN ('user', 'assistant')),
    content TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- AI agent configurations (managed via dashboard)  
CREATE TABLE agents (
    id UUID PRIMARY KEY,          -- Full uuid4(), NOT truncated
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

-- SIP trunks registered in system
CREATE TABLE sip_trunks (
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

-- Outbound call campaigns
CREATE TABLE campaigns (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    phone_numbers TEXT NOT NULL DEFAULT '',
    sip_trunk_id INTEGER REFERENCES sip_trunks(id),
    max_concurrent_calls INTEGER DEFAULT 5,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE campaign_targets (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    scheduled_time TIMESTAMPTZ
);

-- Demo browser call links  
CREATE TABLE demo_links (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    label TEXT,
    language TEXT DEFAULT 'auto',
    is_active BOOLEAN DEFAULT TRUE,
    total_sessions INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Do Not Call list
CREATE TABLE call_dnc (
    phone TEXT PRIMARY KEY,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 🤖 `agent.py` — How the Call Works

### Call Flow (Inbound)

```
LiveKit receives call → JobContext created → entrypoint()
  1. ctx.connect()
  2. If room name starts with "demo-" → run_demo_session()
  3. Parse job.metadata for { phone_number, name } (outbound) or treat as inbound
  4. Rate-limit check (max 3 calls/number/hour)
  5. live_config = get_live_config(phone_number)   ← reads config.json + DB
  6. If outbound: create_sip_participant() using SIP_TRUNK_ID
  7. Build STT (Sarvam saaras:v3), TTS (Sarvam bulbul:v3), LLM (gpt-4o-mini)
  8. Create OutboundAssistant with agent_instructions from live_config
  9. AgentSession.start() → agent speaks first_line greeting → conversation begins
 10. On turn end: save_transcript_turn(), language auto-detection, TTS swap
 11. On call end: log to call_logs, generate Groq summary, notify Telegram
```

### Call Flow (Outbound)

```
make_call.py → POST to LiveKit API → creates job with metadata { phone_number }
→ entrypoint() gets called → step 3 detects phone_number → call_type = "outbound"
→ create_sip_participant(sip_trunk_id="ST_7HUFap76wdDT", sip_call_to=phone_number)
```

### `get_live_config(phone_number)` Logic
1. Try `configs/{phone_clean}.json` (per-caller override)
2. Try `configs/default.json`
3. Try `config.json` (root level)
4. Query DB for active agent (`agents WHERE is_active = TRUE LIMIT 1`)
5. DB values override file values for: `agent_instructions`, `first_line`, `llm_model`, `tts_voice`, `tts_language`, `stt_language`

### Key Agent Tools (LLM function calls)
| Tool | Description |
|---|---|
| `get_available_slots` | Queries Cal.com for open appointment slots |
| `save_booking_intent` | Books appointment on Cal.com, sends Telegram notice |
| `cancel_booking_tool` | Cancels an existing Cal.com booking |
| `end_call` | Hangup helper |
| `transfer_call` | Transfers call to `DEFAULT_TRANSFER_NUMBER` |

### LLM Provider Selection
```python
if llm_provider == "groq":    → openai.LLM.with_groq(model="llama-3.3-70b-versatile")
elif llm_provider == "claude": → openai.LLM(base_url="https://api.anthropic.com/v1/")
else:                          → openai.LLM(model=live_config["llm_model"])   # default
```

### TTS/STT Provider Selection
```python
# STT
if stt_provider == "deepgram": → deepgram_plugin.STT(model="nova-2", language="hi")
else:                           → sarvam.STT(model="saaras:v3", language="unknown",
                                              api_key=os.environ.get("SARVAM_API_KEY"))

# TTS  
if tts_provider == "elevenlabs": → elevenlabs_plugin.TTS(voice_id=...) 
else:                             → sarvam.TTS(model="bulbul:v3", speaker=tts_voice,
                                               api_key=os.environ.get("SARVAM_API_KEY"))
```

---

## 🖥️ `ui_server.py` — Dashboard API & Web UI

**Port:** 8080  
**Framework:** FastAPI  
**Startup:** Calls `db.init_db()` to ensure all tables exist on every container boot.

### Key API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Main dashboard HTML (single-page app) |
| GET | `/health` | Health check for Coolify |
| GET | `/metrics` | Prometheus metrics |
| GET/POST | `/api/config` | Read/write `config.json` |
| GET | `/api/agents` | List all agents |
| POST | `/api/agents` | Create new agent (UUID id) |
| PUT | `/api/agents/{id}` | Update agent |
| DELETE | `/api/agents/{id}` | Delete agent |
| POST | `/api/agents/{id}/activate` | Set as active agent |
| GET | `/api/sip-trunks` | List SIP trunks |
| POST | `/api/sip-trunks` | Create SIP trunk |
| DELETE | `/api/sip-trunks/{id}` | Delete SIP trunk |
| GET | `/api/logs` | Get call log records |
| GET | `/api/logs/{id}/transcript` | Get full transcript |
| POST | `/api/make-call` | Trigger outbound call |
| GET/POST | `/api/campaigns` | Campaign management |
| GET/POST | `/api/demo-links` | Demo WebRTC link management |

### Config System
- `read_config()` → reads `config.json`, merges with env vars via `get_val(key, env_key, default)`
- `write_config(data)` → writes to `config.json`
- UI Dashboard pages: **Overview**, **Agent Settings**, **API Credentials**, **SIP Trunks**, **Campaigns**, **Call Logs**, **Demo Links**

---

## 📣 `notify.py` — Notification System

All notifications fire via **Telegram Bot** (no library, raw HTTP POST).

| Function | When fired |
|---|---|
| `notify_booking_confirmed()` | A booking is confirmed on Cal.com |
| `notify_booking_cancelled()` | A booking is cancelled mid-call |
| `notify_call_no_booking()` | Call ends without booking |
| `notify_agent_error()` | Any mid-call exception or crash |
| `notify_whatsapp_booking()` | Optional WhatsApp (Twilio, if configured) |
| `send_webhook()` | Generic client webhook delivery |

---

## 📅 `calendar_tools.py` — Booking System

Uses **Cal.com API v1/v2**:
- `get_available_slots(date_str)` → GET `/slots` → returns `[{time, label}]`
- `create_booking(start_time, name, phone, notes)` → POST `/v2/bookings`
  - Uses `CAL_API_KEY` + `CAL_EVENT_TYPE_ID` from env
  - Attendee email auto-generated as `{phone}@voiceagent.placeholder`
- `cancel_booking(booking_id)` → DELETE `/v1/bookings/{id}/cancel`

---

## 🔧 Recent Bug Fixes & What Was Changed

> This section is critical for a new agent to understand the history of fixes.

### Bug #1 — SIP trunk 500 error (2026-02-28)
- **Problem:** `sip_trunks` table missing `sip_uri` column.
- **Fix:** Added column to schema in `db.py`. Updated `ui_server.py` to expose raw DB errors in 500 responses.

### Bug #2 — Agents table missing (2026-03-04)
- **Problem:** `agents` table not created because `ALTER TABLE campaigns` ran before `CREATE TABLE campaigns` in `init_db()`, aborting the whole SQL transaction.
- **Fix:** Re-ordered `init_db()` so ALL `CREATE TABLE` statements come first, ALL `ALTER TABLE` statements come last.

### Bug #3 — `max_tokens` crash in LLM (2026-03-04)
- **Problem:** `openai.LLM(max_tokens=60)` — `max_tokens` is not a valid constructor arg.
- **Fix:** Removed `max_tokens` from all `openai.LLM()` calls in `agent.py`.

### Bug #4 — Voice selector missing from Agent Settings (2026-03-04)
- **Feature:** Moved TTS Voice/Language dropdowns into the Agent Settings page.
- **Fix:** Updated HTML and `saveConfig('agent')` JS function in `ui_server.py`.

### Bug #5 — `name 'config' is not defined` crash on outbound calls (2026-03-04)
- **Problem:** In `entrypoint()`, the outbound SIP block (line ~784) referenced `config.get("sip_trunk_id")` but `config` is only a local variable inside `get_live_config()`. The `entrypoint()` function uses `live_config`, which was also loaded 60+ lines AFTER the SIP block.
- **Fix:** 
  1. Moved `live_config = get_live_config(phone_number)` to BEFORE the SIP outbound block.
  2. Changed `config.get(...)` → `live_config.get(...)`.

### Bug #6 — Missing LiveKit SIP Trunk ID (2026-03-04)
- **Problem:** No outbound SIP trunk had EVER been registered with LiveKit. The `.env` had Vobiz credentials but nothing was configured in LiveKit cloud.
- **Fix:** Created LiveKit outbound SIP trunk via API:
  ```python
  trunk = await lk.sip.create_sip_outbound_trunk(
      name="Vobiz Outbound Trunk",
      address="d575a830.sip.vobiz.ai",
      numbers=["+918049280319"],
      auth_username="abc@12345",
      auth_password="abc@12345",
  )
  # → Created: ST_7HUFap76wdDT
  ```
- Set `SIP_TRUNK_ID=ST_7HUFap76wdDT` in `.env` and `config.json`.
- **ACTION REQUIRED:** Must set `SIP_TRUNK_ID=ST_7HUFap76wdDT` and `OUTBOUND_TRUNK_ID=ST_7HUFap76wdDT` in Coolify env vars for the agent service.

### Bug #7 — Sarvam TTS/STT WebSocket timeout (2026-03-04)
- **Problem:** `SARVAM_API_KEY` was only in local `.env` (gitignored). Coolify container never received it, causing WebSocket auth failures that appeared as connection timeouts.
- **Fix:** Explicitly passed `api_key=os.environ.get("SARVAM_API_KEY") or live_config.get("sarvam_api_key")` to all 5 `sarvam.TTS()` and `sarvam.STT()` constructor calls.
- **ACTION REQUIRED:** Must set `SARVAM_API_KEY=sk_aci4iu7i_jDjsODMf1vuqrZGJEfVlDcMW` in Coolify env vars for the agent service.

### Bug #8 — `db.init_db()` not called in `ui_server.py` (2026-03-04)
- **Problem:** The website dashboard started, hit `/api/agents`, but the `agents` table didn't exist yet because only `agent.py` called `init_db()` at startup.
- **Fix:** Added `@app.on_event("startup")` in `ui_server.py` that calls `db.init_db()`.

---

## 🚦 Pending Actions & Known Issues

| # | Issue | Status | Action Needed |
|---|---|---|---|
| 1 | `SIP_TRUNK_ID` not in Coolify | **PENDING** | Add `SIP_TRUNK_ID=ST_7HUFap76wdDT` to Coolify agent env |
| 2 | `SARVAM_API_KEY` not in Coolify | **PENDING** | Add `SARVAM_API_KEY=sk_aci4iu7i_...` to Coolify agent env |
| 3 | Outbound call not tested end-to-end | **PENDING** | Test after env vars added to Coolify |
| 4 | Sarvam TTS timeout on Coolify | **PENDING** | Verify no network firewall blocking `api.sarvam.ai` from container |

---

## 🐳 Docker / Deployment Setup

**File:** `supervisord.conf`
```ini
[program:ui_server]
command=python ui_server.py --host 0.0.0.0 --port 8080
...

[program:agent]
command=python agent.py start
...
```

Both services run in the **same Docker container** via supervisord.

**Dockerfile** installs requirements, copies all Python files, runs supervisord.

**Coolify deployment:** Connected to GitHub `main` branch. Auto-deploys on push.

---

## 🔗 Call Flow Diagram (Outbound)

```
1. POST /api/make-call { phone_number: "+91..." }
         ↓
2. ui_server.py → creates LiveKit room + dispatches job
         ↓
3. LiveKit Worker → agent.py entrypoint()
         ↓
4. live_config = get_live_config("+91...")
         (reads config.json → DB active agent → merges)
         ↓
5. create_sip_participant(
       sip_trunk_id="ST_7HUFap76wdDT",
       sip_call_to="+91..."
   )  ← LiveKit calls Vobiz → Vobiz dials number
         ↓
6. STT (Sarvam saaras:v3) + TTS (Sarvam bulbul:v3) + LLM (gpt-4o-mini)
         ↓
7. Priya greets caller in Hindi/Hinglish
         ↓
8. Conversation → booking/no booking
         ↓
9. Call ends → Groq Llama summary → Telegram notification → DB log
```

---

## 🔗 Call Flow Diagram (Inbound)

```
Phone call to Vobiz DID → Vobiz SIP → LiveKit SIP ingest
         ↓
LiveKit creates job → agent.py entrypoint()
         ↓  (same from step 4 above, but call_type="inbound")
```

---

## 🛠️ Key Python Files Quick Reference

### `agent.py` Key Functions
| Function | Line | Description |
|---|---|---|
| `is_rate_limited(phone)` | ~92 | Blocks >3 calls/number/hour |
| `get_lang_config(lang_code)` | ~143 | Returns TTS speaker/language config |
| `build_system_prompt(instructions)` | ~157 | Adds STYLE & TONE rules to every agent |
| `get_live_config(phone_number)` | ~186 | Main config loader (file + DB merge) |
| `get_ist_time_context()` | ~232 | Returns current IST time + 7-day date table |
| `run_demo_session(ctx)` | ~629 | Handles demo browser calls |
| `entrypoint(ctx)` | ~743 | **Main entry point for ALL calls** |

### `db.py` Key Functions
| Function | Description |
|---|---|
| `init_db()` | Creates all tables (idempotent). Called at startup by BOTH services. |
| `get_active_agent()` | Returns agent WHERE is_active=TRUE |
| `activate_agent(id)` | Sets one agent active, deactivates all others |
| `create_agent(...)` | Creates new agent row (id must be a full UUID) |
| `create_sip_trunk(...)` | Registers an internal SIP trunk record |
| `fetch_call_logs(limit)` | Returns recent call records |

---

## ⚠️ Critical Rules for Future Changes

1. **Never truncate agent IDs** — `id` column in `agents` is `UUID PRIMARY KEY`. Always use `str(uuid.uuid4())` to generate IDs.
2. **Always call `init_db()` at startup** — Both `ui_server.py` (startup event) and `agent.py` (worker start) must call this.
3. **SQL Transaction Order in `init_db()`** — ALL `CREATE TABLE` statements MUST come before ANY `ALTER TABLE` statements. If ALTER runs before CREATE, the entire block aborts silently.
4. **`SARVAM_API_KEY` must be passed explicitly** — Do `api_key=os.environ.get("SARVAM_API_KEY")` in every `sarvam.TTS()` and `sarvam.STT()` constructor. Do NOT rely on auto-discovery.
5. **`SIP_TRUNK_ID` is the LiveKit trunk ID** (format `ST_XXXX`), NOT the Vobiz domain/username. These are separate concepts. The LiveKit trunk wraps the Vobiz SIP credentials.
6. **`live_config` must be loaded before the outbound SIP block in `entrypoint()`** — It is used to look up `sip_trunk_id` among other things.
7. **`.env` is gitignored** — All API keys must be manually entered in Coolify environment variables for EACH service (agent worker + ui_server). Git only deploys code, not secrets.
8. **`openai.LLM()` does NOT accept `max_tokens`** — Pass it in request/generation calls only, not the constructor.

---

## 📋 Current State of Latest `main` Branch Commits

```
b77c0f4  fix: CRITICAL - Move live_config load before outbound SIP block
4067795  fix: Set sip_trunk_id=ST_7HUFap76wdDT in config.json
3ad51f5  fix: Reorder SQL execution block in db.init_db()
acd5f75  fix: Run db.init_db() on web server startup
9915336  fix: Resolve missing SIP_TRUNK_ID TwirpError crash
30af562  fix: Resolve LLM max_tokens crash and missing agents DB table
9f52c5d  fix: Expose raw DB exception in 500 errors
1ff03b3  fix: Explicitly pass SARVAM_API_KEY to all TTS/STT init calls
```

---

*This document was auto-generated as a context snapshot on 2026-03-04. Update after each significant change.*
