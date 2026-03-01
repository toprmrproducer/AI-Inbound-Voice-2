import os
import json
import logging

# ── Structured JSON Logging (#24) ─────────────────────────────────────────────
try:
    from pythonjsonlogger import jsonlogger
    def _setup_logging():
        handler = logging.StreamHandler()
        handler.setFormatter(jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"
        ))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    _setup_logging()
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

import certifi
import pytz
import re
import asyncio
import time
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

# Fix for macOS SSL certificate verification
os.environ["SSL_CERT_FILE"] = certifi.where()

# ── Sentry error tracking (#21) ───────────────────────────────────────────────
try:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    _sentry_dsn = os.environ.get("SENTRY_DSN", "")
    if _sentry_dsn:
        sentry_sdk.init(
            dsn=_sentry_dsn,
            traces_sample_rate=0.1,
            integrations=[AsyncioIntegration()],
            environment=os.environ.get("ENVIRONMENT", "production"),
        )
except ImportError:
    pass

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
try:
    from livekit.agents import noise_cancellation as _nc
except ImportError:
    _nc = None

from livekit.plugins import openai, sarvam, silero
try:
    from livekit.plugins import deepgram as deepgram_plugin
except ImportError:
    deepgram_plugin = None
try:
    from livekit.plugins import elevenlabs as elevenlabs_plugin
except ImportError:
    elevenlabs_plugin = None

from typing import Annotated

CONFIG_FILE = "config.json"

# ── Rate limiter (#37) ────────────────────────────────────────────────────────
_call_timestamps: dict = defaultdict(list)
RATE_LIMIT_CALLS = 3
RATE_LIMIT_WINDOW = 3600  # 1 hour

def is_rate_limited(phone: str) -> bool:
    if not phone or phone == "unknown":
        return False
    now = time.time()
    _call_timestamps[phone] = [t for t in _call_timestamps[phone] if now - t < RATE_LIMIT_WINDOW]
    if len(_call_timestamps[phone]) >= RATE_LIMIT_CALLS:
        return True
    _call_timestamps[phone].append(now)
    return False


# ══════════════════════════════════════════════════════════════════════════════
# MULTILINGUAL AUTO-DETECTION CONFIG
# ══════════════════════════════════════════════════════════════════════════════

# Sarvam Bulbul v3 — best voice per language
LANGUAGE_CONFIG = {
    "hi-IN": {"speaker": "rohan",      "tts_lang": "hi-IN", "name": "Hindi"},
    "bn-IN": {"speaker": "arnav",      "tts_lang": "bn-IN", "name": "Bengali"},
    "ta-IN": {"speaker": "pavithra",   "tts_lang": "ta-IN", "name": "Tamil"},
    "te-IN": {"speaker": "ananya",     "tts_lang": "te-IN", "name": "Telugu"},
    "gu-IN": {"speaker": "avni",       "tts_lang": "gu-IN", "name": "Gujarati"},
    "kn-IN": {"speaker": "suresh",     "tts_lang": "kn-IN", "name": "Kannada"},
    "ml-IN": {"speaker": "aswin",      "tts_lang": "ml-IN", "name": "Malayalam"},
    "mr-IN": {"speaker": "aarohi",     "tts_lang": "mr-IN", "name": "Marathi"},
    "pa-IN": {"speaker": "gurpreet",   "tts_lang": "pa-IN", "name": "Punjabi"},
    "od-IN": {"speaker": "subhashini", "tts_lang": "od-IN", "name": "Odia"},
    "en-IN": {"speaker": "anushka",    "tts_lang": "en-IN", "name": "English"},
    # STT-only (no Bulbul TTS — fallback)
    "ur-IN": {"speaker": "rohan",      "tts_lang": "hi-IN", "name": "Urdu"},
    "as-IN": {"speaker": "aarohi",     "tts_lang": "mr-IN", "name": "Assamese"},
    "ne-IN": {"speaker": "rohan",      "tts_lang": "hi-IN", "name": "Nepali"},
}

GREETINGS = {
    "hi-IN": "नमस्ते! मैं आपका मेड स्पा असिस्टेंट हूं। आज मैं आपकी कैसे मदद कर सकता हूं?",
    "ta-IN": "வணக்கம்! நான் உங்கள் மெட் ஸ்பா உதவியாளர். நான் உங்களுக்கு எப்படி உதவலாம்?",
    "bn-IN": "নমস্কার! আমি আপনার মেড স্পা সহকারী। আজ আমি আপনাকে কীভাবে সাহায্য করতে পারি?",
    "te-IN": "నమస్కారం! నేను మీ మెడ్ స్పా అసిస్టెంట్. నేను మీకు ఎలా సహాయం చేయగలను?",
    "gu-IN": "નમસ્તે! હું તમારો મેડ સ્પા સહાયક છું. આજ હું તમારી કેવી રીતે મદદ કરી શકું?",
    "kn-IN": "ನಮಸ್ಕಾರ! ನಾನು ನಿಮ್ಮ ಮೆಡ್ ಸ್ಪಾ ಸಹಾಯಕ. ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?",
    "ml-IN": "നമസ്കാരം! ഞാൻ നിങ്ങളുടെ മെഡ് സ്പാ അസിസ്റ്റന്റ് ആണ്. ഞാൻ നിങ്ങളെ എങ്ങനെ സഹായിക്കാം?",
    "mr-IN": "नमस्कार! मी तुमचा मेड स्पा सहाय्यक आहे. आज मी तुम्हाला कशी मदद करू शकतो?",
    "pa-IN": "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਤੁਹਾਡਾ ਮੇਡ ਸਪਾ ਸਹਾਇਕ ਹਾਂ। ਮੈਂ ਅੱਜ ਤੁਹਾਡੀ ਕਿਵੇਂ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ?",
    "od-IN": "ନମସ୍କାର! ମୁଁ ଆପଣଙ୍କ ମେଡ ସ୍ପା ସହାୟକ। ଆଜି ମୁଁ ଆପଣଙ୍କୁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି?",
    "en-IN": "Hello! I'm your Med Spa assistant. How can I help you today?",
    "auto": "Hello! Namaste! I'm your Med Spa assistant — you can speak in any Indian language.",
}

MED_SPA_SERVICES = "facials, laser treatments, Botox, skin care, hair removal, body contouring"

def get_lang_config(lang_code: str) -> dict:
    return LANGUAGE_CONFIG.get(lang_code, LANGUAGE_CONFIG["hi-IN"])

def get_multilingual_greeting(lang: str) -> str:
    return GREETINGS.get(lang, GREETINGS["auto"])

def build_multilang_system_prompt(lang_code: str, base_instructions: str = "") -> str:
    """Build a language-locked system prompt for the given detected language."""
    cfg = get_lang_config(lang_code)
    lang_name = cfg["name"]
    core = f"""

═══════════════════════════════════════════
CRITICAL LANGUAGE RULE — NON-NEGOTIABLE:
You MUST speak ONLY in {lang_name}.
NEVER switch to Hindi, English, or any other language.
Even if the user mixes languages, ALWAYS reply in {lang_name}.
If a medical term has no {lang_name} equivalent, say it in English only for that word.
Language: {lang_name} ONLY.
═══════════════════════════════════════════

"""
    return (base_instructions or "") + core


def get_live_config(phone_number: str = None):
    """Load config: try per-client config first, then default (#17)."""
    config = {}
    config_paths = []
    if phone_number:
        clean = phone_number.replace("+", "").replace(" ", "")
        config_paths.append(f"configs/{clean}.json")
    config_paths += ["configs/default.json", CONFIG_FILE]
    for path in config_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                break
            except Exception as e:
                logging.getLogger("agent").error(f"Failed to read {path}: {e}")
    return {
        "agent_instructions": config.get("agent_instructions", ""),
        "stt_min_endpointing_delay": config.get("stt_min_endpointing_delay", 0.05),
        "llm_model": config.get("llm_model", "gpt-4o-mini"),
        "llm_provider": config.get("llm_provider", "openai"),
        "tts_voice": config.get("tts_voice", "kavya"),
        "tts_language": config.get("tts_language", "hi-IN"),
        "tts_provider": config.get("tts_provider", "sarvam"),
        "stt_provider": config.get("stt_provider", "sarvam"),
        "stt_language": config.get("stt_language", "hi-IN"),
        **config
    }

def get_ist_time_context():
    """Returns current IST date/time AND the next 7 days so the agent
    can resolve 'this Thursday' / 'next Monday' to exact ISO dates."""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    today_str = now.strftime('%A, %B %d, %Y')
    time_str  = now.strftime('%I:%M %p')

    # Build a day-by-day lookup for the next 7 days
    from datetime import timedelta
    days_lines = []
    for i in range(7):
        day = now + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime('%A'))
        days_lines.append(f"  {label}: {day.strftime('%A %d %B %Y')} → ISO {day.strftime('%Y-%m-%d')}")
    days_block = "\n".join(days_lines)

    return (
        f"\n\n[SYSTEM CONTEXT]\n"
        f"Current date & time: {today_str} at {time_str} IST\n"
        f"Use the table below to resolve ANY relative day reference (e.g. 'this Friday', 'next Monday', 'day after tomorrow') to the correct ISO date:\n"
        f"{days_block}\n"
        f"Always use the ISO date from this table when calling save_booking_intent. Appointments are in IST (+05:30).]"
    )

from calendar_tools import get_available_slots, create_booking, cancel_booking
from notify import (
    notify_booking_confirmed,
    notify_booking_cancelled,
    notify_call_no_booking,
    notify_agent_error,
)

load_dotenv()
logger = logging.getLogger("outbound-agent")


# ══════════════════════════════════════════════════════════════════════════════
# TOOL CONTEXT — All AI-callable functions
# ══════════════════════════════════════════════════════════════════════════════

class AgentTools(llm.ToolContext):

    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone = caller_phone
        self.caller_name = caller_name
        self.booking_intent: dict | None = None  # Stores details for post-call booking

        # ── State tracked across the call ──────────────────────────────────
        self.sip_domain             = os.getenv("VOBIZ_SIP_DOMAIN")
        self.ctx_api                = None
        self.room_name              = None
        self._sip_identity          = None # Will be set in entrypoint if needed for transfer

    # ── Tool 1: Transfer to Human ──────────────────────────────────────────

    @llm.function_tool(
        description=(
            "Transfer this call to a human agent immediately. "
            "Use this if: the caller explicitly asks for a human, "
            "the caller is angry or frustrated, or the query is outside your scope."
        )
    )
    async def transfer_call(self):
        logger.info("[TOOL] transfer_call triggered")
        destination = os.getenv("DEFAULT_TRANSFER_NUMBER")
            
        if destination and self.sip_domain and "@" not in destination:
            clean_dest = destination.replace("tel:", "").replace("sip:", "")
            destination = f"sip:{clean_dest}@{self.sip_domain}"
        
        if destination and not destination.startswith("sip:"):
            destination = f"sip:{destination}"
            
        try:
            if self.ctx_api and self.room_name and destination and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to=destination,
                        play_dialtone=False
                    )
                )
                return "Transfer initiated successfully."
            else:
                return "I'm having trouble transferring right now. Please hold on."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return "I'm having trouble transferring right now. Please hold on."


    # ── Tool 2: End Call (auto-hangup) ────────────────────────────────────

    @llm.function_tool(
        description=(
            "End the call and hang up. Use this ONLY when: "
            "(1) the caller explicitly says 'bye', 'goodbye', 'cut the call', or has confirmed their booking and the conversation is complete. "
            "(2) the caller says they don't need anything else. "
            "Say a short goodbye BEFORE calling this tool."
        )
    )
    async def end_call(self) -> str:
        logger.info("[TOOL] end_call triggered — hanging up.")
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to="tel:+0",  # Sends REFER to empty destination = hangup
                        play_dialtone=False,
                    )
                )
            return "Call ended."
        except Exception as e:
            logger.warning(f"[end_call] Graceful hangup failed, forcing disconnect: {e}")
            return "Goodbye!"


    # ── Tool 3: Save Booking Intent ────────────────────────────────────────

    @llm.function_tool(
        description="Save the caller's intent to book an appointment for a specific date and time. Do this ONLY AFTER the caller has verbally confirmed the date, time, full name, and email address. This queues the booking to be confirmed right after the call.",
    )
    async def save_booking_intent(
        self,
        start_time: Annotated[str, "The exact ISO 8601 start time with IST offset. Example: '2026-02-24T10:00:00+05:30'"],
        caller_name: Annotated[str, "Full name of the caller as they stated it."],
        caller_email: Annotated[str, "Email address of the caller for booking confirmation."] = "",
        treatment_notes: Annotated[str, "Any relevant notes — service needed, preferences, etc."] = "",
    ) -> str:
        logger.info(f"Booking intent saved: {start_time} for {caller_name}")
        if caller_name and len(caller_name) > 1:
            self.caller_name = caller_name
        self.booking_intent = {
            "start_time": start_time,
            "caller_name": self.caller_name,
            "caller_phone": self.caller_phone,
            "caller_email": caller_email,
            "notes": treatment_notes,
        }
        return f"Booking intent saved for {start_time}. Tell the caller their appointment is confirmed and they'll receive a confirmation text shortly."


    # ── Tool 4: Cancel Appointment ─────────────────────────────────────────

    @llm.function_tool(
        description=(
            "Cancel the appointment that was booked during THIS call. "
            "Use this if the caller changes their mind after booking. "
            "Only works if a booking was already made in this session."
        )
    )
    async def cancel_appointment(
        self,
        reason: Annotated[str, "Reason for cancellation as stated by the caller."] = "Caller changed their mind",
    ):
        logger.info(f"[TOOL] cancel_appointment: reason={reason}")
        if not self.booking_intent:
            return "I don't have an active booking from this call to cancel."
        self.booking_intent = None
        return "No problem — I've cancelled your booking. Would you like to reschedule for another time?"

    # ── Tool 5: Check Availability (#13) ─────────────────────────────────
    @llm.function_tool(
        description=(
            "Check available appointment slots for a given date. "
            "Call this when the user asks about availability or wants to book. "
            "Returns a list of open time slots."
        )
    )
    async def check_availability(
        self,
        date: Annotated[str, "Date to check in YYYY-MM-DD format, e.g. '2026-03-01'"],
    ) -> str:
        logger.info(f"[TOOL] check_availability: date={date}")
        try:
            from calendar_tools import get_available_slots
            slots = await get_available_slots(date)
            if not slots:
                return f"No available slots on {date}. Would you like to check another date?"
            slot_strings = [s.get("start_time", str(s)) for s in slots[:6]]
            return f"Available slots on {date}: {', '.join(slot_strings)}"
        except Exception as e:
            logger.error(f"[TOOL] check_availability failed: {e}")
            return "I'm having trouble checking the calendar right now."

    # ── Tool 6: Business Hours (#31) ──────────────────────────────────────
    @llm.function_tool(
        description="Check if the business is currently open and what the operating hours are."
    )
    async def get_business_hours(self) -> str:
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        hours = {
            0: ("Monday",    "10:00", "19:00"),
            1: ("Tuesday",   "10:00", "19:00"),
            2: ("Wednesday", "10:00", "19:00"),
            3: ("Thursday",  "10:00", "19:00"),
            4: ("Friday",    "10:00", "19:00"),
            5: ("Saturday",  "10:00", "17:00"),
            6: ("Sunday",    None,    None),
        }
        day_name, open_t, close_t = hours[now.weekday()]
        if open_t is None:
            return "We are closed on Sundays. Next opening is Monday at 10:00 AM IST."
        current_time = now.strftime("%H:%M")
        if open_t <= current_time <= close_t:
            return f"We are currently OPEN. Hours today ({day_name}): {open_t}–{close_t} IST."
        return f"We are currently CLOSED. Today's hours ({day_name}): {open_t}–{close_t} IST."


# ══════════════════════════════════════════════════════════════════════════════
# SARVAM-POWERED VOICE AGENT
# ══════════════════════════════════════════════════════════════════════════════

class OutboundAssistant(Agent):

    def __init__(self, agent_tools: AgentTools, first_line: str = "", live_config: dict = None):
        tools = llm.find_function_tools(agent_tools)
        self._first_line = first_line
        self._live_config = live_config or get_live_config()
        base_instructions = self._live_config.get("agent_instructions", "")
        ist_context = get_ist_time_context()
        final_instructions = base_instructions + ist_context
        # #11 — Token counter
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model("gpt-4o")
            tc = len(enc.encode(final_instructions))
            logging.getLogger("agent").info(f"[PROMPT] System prompt is {tc} tokens")
            if tc > 400:
                logging.getLogger("agent").warning(f"[PROMPT] Prompt exceeds 400 tokens ({tc}) — consider trimming")
        except Exception:
            pass
        super().__init__(instructions=final_instructions, tools=tools)

    async def on_enter(self):
        # #28 — Dynamic greeting from config
        greeting = (
            self._live_config.get("opening_greeting")
            or self._first_line
            or (
                "Namaste! Welcome to Daisy's Med Spa. "
                "Main aapki kaise madad kar sakti hoon? "
                "I can answer questions about our treatments or help you book an appointment."
            )
        )
        await self.session.generate_reply(
            instructions=f"Say exactly this phrase: '{greeting}'"
        )




# ══════════════════════════════════════════════════════════════════════════════
# AUTO-LANGUAGE AGENT (for demo + future inbound use)
# ══════════════════════════════════════════════════════════════════════════════

class AutoLanguageAgent(Agent):
    """
    Wraps OutboundAssistant logic for demo sessions.
    Listens for Saaras v3 language detection on first user utterance,
    then dynamically switches TTS speaker + system prompt to match.
    """

    def __init__(self, base_instructions: str = "", first_line: str = ""):
        self._base_instructions = base_instructions
        self._first_line = first_line
        self.detected_language: str | None = None
        self.language_locked = False
        self._session_ref = None          # set after session.start()
        super().__init__(instructions=base_instructions or build_multilang_system_prompt("hi-IN", base_instructions))

    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Fires after every user utterance. We detect language here."""
        if not self.language_locked:
            detected = getattr(new_message, "language", None)
            if not detected:
                # Try nested transcript attribute
                try:
                    detected = new_message.content[0].language  # type: ignore[attr-defined]
                except Exception:
                    detected = None
            if detected and detected not in ("unknown", "", None) and detected in LANGUAGE_CONFIG:
                self.detected_language = detected
                self.language_locked = True
                logger.info(f"[LANG] Detected: {detected} ({get_lang_config(detected)['name']}) — locking in")
                # Update system prompt to enforce the detected language
                self.instructions = build_multilang_system_prompt(detected, self._base_instructions)
                # Swap TTS on the session if available
                if self._session_ref is not None:
                    cfg = get_lang_config(detected)
                    try:
                        self._session_ref._tts = sarvam.TTS(
                            model="bulbul:v3",
                            speaker=cfg["speaker"],
                            target_language_code=cfg["tts_lang"],
                        )
                        logger.info(f"[LANG] TTS swapped to {cfg['name']} (speaker={cfg['speaker']})")
                    except Exception as e:
                        logger.warning(f"[LANG] TTS swap failed: {e}")
        await super().on_user_turn_completed(turn_ctx, new_message)


# ══════════════════════════════════════════════════════════════════════════════
# DEMO SESSION (Browser-based LiveKit call)
# ══════════════════════════════════════════════════════════════════════════════

async def run_demo_session(ctx: JobContext):
    """
    Handles a browser-based demo call via LiveKit WebRTC.
    Visitor has already joined the room from /demo/<slug>.
    Uses the same voice pipeline as regular inbound calls.
    """
    logger.info(f"[DEMO] Browser demo session in room: {ctx.room.name}")
    live_config  = get_live_config()
    llm_model    = live_config.get("llm_model",    "gpt-4o-mini")
    tts_language = live_config.get("tts_language", "hi-IN")
    tts_voice    = live_config.get("tts_voice",    "rohan")
    stt_language = live_config.get("stt_language", "hi-IN")
    first_line   = live_config.get("first_line",   "Namaste! Welcome. How can I help you today?")

    base_instructions = live_config.get("agent_instructions", "")

    agent = AutoLanguageAgent(
        base_instructions=base_instructions,
        first_line=first_line,
    )

    session = AgentSession(
        # language="unknown" → Saaras v3 auto-detects from first utterance
        stt=sarvam.STT(model="saaras:v3", language="unknown", mode="transcribe"),
        llm=openai.LLM(model=llm_model),
        tts=sarvam.TTS(
            model="bulbul:v3",
            speaker=get_lang_config("hi-IN")["speaker"],   # default until detected
            target_language_code="hi-IN",
        ),
        turn_detection="stt",
        allow_interruptions=True,
    )

    # Give agent a reference to session so it can swap TTS on detection
    agent._session_ref = session

    await session.start(room=ctx.room, agent=agent)

    # Greet in "auto" neutral language before detection
    greeting = live_config.get("first_line") or get_multilingual_greeting("auto")
    await session.say(greeting, allow_interruptions=True)
    logger.info("[DEMO] Session live.")
    await session.wait_for_disconnect()
    logger.info(f"[DEMO] Session ended: {ctx.room.name}")


# ══════════════════════════════════════════════════════════════════════════════
# JOB ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

async def entrypoint(ctx: JobContext):
    logger.info(f"[JOB] id={ctx.job.id}")
    logger.info(f"[JOB] raw metadata='{ctx.job.metadata}'")

    # ── Demo-room routing: browser WebRTC sessions ───────────────────────
    # Connect first so we can read ctx.room.name
    await ctx.connect()
    logger.info(f"[ROOM] Connected: {ctx.room.name}")
    if ctx.room.name.startswith("demo-"):
        await run_demo_session(ctx)
        return

    # ── Parse metadata ─────────────────────────────────────────────────────
    phone_number = None
    call_type    = "inbound"
    raw_meta     = ctx.job.metadata or ""
    caller_name  = "Unknown"

    if raw_meta.strip():
        try:
            meta = json.loads(raw_meta)
            phone_number = (
                meta.get("phone_number")
                or meta.get("to")
                or meta.get("destination")
            )
            caller_name = meta.get("name", caller_name)
            if phone_number:
                call_type = "outbound"
                logger.info(f"[CALL] Outbound → {phone_number}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"[METADATA] Parse error: {e} — treating as inbound")

    # ── Rate limiting (#37) ────────────────────────────────────────────────
    caller_phone = phone_number or "unknown"
    if is_rate_limited(caller_phone):
        logger.warning(f"[RATE-LIMIT] Blocked {caller_phone} — too many calls in window")
        return

    # ── (already connected above for demo routing) ──────────────────────

    # ── Outbound: dial via Vobiz SIP trunk ────────────────────────────────
    if call_type == "outbound" and phone_number:
        try:
            lk_api = api.LiveKitAPI(
                url=os.environ["LIVEKIT_URL"],
                api_key=os.environ["LIVEKIT_API_KEY"],
                api_secret=os.environ["LIVEKIT_API_SECRET"],
            )
            
            sip_trunk_id = os.environ.get("OUTBOUND_TRUNK_ID", os.environ.get("SIP_TRUNK_ID", ""))
            
            await lk_api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=sip_trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number.replace('+', '')}",
                    participant_name="Caller",
                    wait_until_answered=True,
                )
            )
            await lk_api.aclose()
            logger.info(f"[SIP] Outbound call dispatched to {phone_number} and answered.")
        except Exception as e:
            logger.error(f"[SIP] Dispatch failed: {e}")
            notify_agent_error(phone_number or "unknown", str(e))
            return

    # ── Instantiate tools ─────────────────────────────────────────────────
    caller_phone = phone_number or "unknown"

    # #32 — Extract name from SIP Caller-ID if available
    for identity, participant in ctx.room.remote_participants.items():
        if participant.name and participant.name not in ["", "Caller", "Unknown"]:
            caller_name = participant.name
            logger.info(f"[CALLER-ID] Name from SIP: {caller_name}")
            break

    participant_identity = (
        f"sip_{caller_phone.replace('+', '')}"
        if phone_number else "inbound_caller"
    )

    agent_tools = AgentTools(
        caller_phone=caller_phone,
        caller_name=caller_name,
    )
    agent_tools._sip_identity = participant_identity
    agent_tools.ctx_api = ctx.api
    agent_tools.room_name = ctx.room.name
    


    # ── Read live configuration ───────────────────────────────────────────
    live_config = get_live_config(phone_number)
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.05)
    llm_model = live_config.get("llm_model", "gpt-4o-mini")
    llm_provider = live_config.get("llm_provider", "openai")
    tts_voice = live_config.get("tts_voice", "rohan")
    tts_language = live_config.get("tts_language", "hi-IN")
    tts_provider = live_config.get("tts_provider", "sarvam")
    stt_provider = live_config.get("stt_provider", "sarvam")
    stt_language = live_config.get("stt_language", "hi-IN")
    first_line = live_config.get("first_line", "")

    # #15 — Caller memory: inject last call summary into prompt
    async def get_caller_history(phone: str) -> str:
        try:
            from db import fetch_call_logs
            logs = fetch_call_logs(limit=1)
            # filter for this phone number
            matching = [l for l in logs if l.get("phone") == phone]
            if matching:
                last = matching[0]
                return f"\n\n[CALLER HISTORY: Last call on {str(last.get('created_at', ''))[:10]}. Summary: {last.get('summary', '')}]"
        except Exception as e:
            logger.warning(f"[MEMORY] Could not load caller history: {e}")
        return ""

    if caller_phone != "unknown":
        caller_history = await get_caller_history(caller_phone)
        if caller_history:
            current = live_config.get("agent_instructions", "")
            live_config["agent_instructions"] = current + caller_history

    # ── Build LLM (#8/#27) ────────────────────────────────────────────────
    if llm_provider == "groq":
        active_llm = openai.LLM.with_groq(
            model=llm_model or "llama-3.3-70b-versatile",
        )
    elif llm_provider == "claude":
        active_llm = openai.LLM(
            model="claude-haiku-3-5-latest",
            base_url="https://api.anthropic.com/v1/",
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )
    else:
        active_llm = openai.LLM(model=llm_model)

    # ── Build STT (#9/#20) ────────────────────────────────────────────────
    if stt_provider == "deepgram" and deepgram_plugin:
        active_stt = deepgram_plugin.STT(
            model="nova-2",
            language="hi",
            interim_results=False,
        )
    else:
        active_stt = sarvam.STT(
            language="unknown",   # auto-detect via Saaras v3
            model="saaras:v3",
            mode="transcribe",    # transcribe preserves original language for detection
            flush_signal=True,
        )

    # ── Build TTS (#10) ───────────────────────────────────────────────────
    if tts_provider == "elevenlabs" and elevenlabs_plugin:
        active_tts = elevenlabs_plugin.TTS(
            model="eleven_turbo_v2_5",
            voice_id=live_config.get("elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM"),
        )
    else:
        active_tts = sarvam.TTS(
            target_language_code=tts_language,
            model="bulbul:v3",
            speaker=tts_voice,
        )

    # ── Build agent ───────────────────────────────────────────────────────
    agent = OutboundAssistant(agent_tools=agent_tools, first_line=first_line, live_config=live_config)

    # --- Interruption state tracking ---
    global agent_is_speaking
    agent_is_speaking = False

    async def on_user_speech_started(session):
        """Fires instantly when VAD detects user started speaking."""
        global agent_is_speaking
        if agent_is_speaking and session.current_speech:
            await session.current_speech.interrupt()
            logger.debug("[INTERRUPT] Cut off agent — user started speaking")

    def before_tts_cb(agent_response: str) -> str:
        """
        Returns only the FIRST sentence to TTS.
        Remaining sentences are queued as separate interruptible chunks.
        """
        sentences = re.split(r'(?<=[।.!?])\s+', agent_response.strip())
        return sentences[0] if sentences else agent_response

    # ── Start Sarvam-powered session (#1 #2 #3 #6) ────────────────────────
    _room_input_opts = RoomInputOptions(close_on_disconnect=False)
    if _nc is not None:
        try:
            _room_input_opts = RoomInputOptions(
                close_on_disconnect=False,
                noise_cancellation=_nc.BVC(),
            )
            logger.info("[AUDIO] BVC noise cancellation enabled")
        except Exception as e:
            logger.warning(f"[AUDIO] BVC unavailable: {e}")

    session = AgentSession(
        stt=active_stt,
        llm=active_llm,
        tts=active_tts,
        turn_detection="stt",
        min_endpointing_delay=delay_setting,
        allow_interruptions=True,
    )

    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=_room_input_opts,
    )

    # #12 — TTS pre-warming
    try:
        await session.tts.prewarm()
        logger.info("[TTS] Pre-warmed successfully")
    except Exception as e:
        logger.warning(f"[TTS] Pre-warm failed (non-critical): {e}")

    logger.info("[AGENT] Session live — waiting for caller audio.")
    call_start_time = datetime.now()

    # #38 — Track active call (in-memory log; no separate DB table needed)
    async def upsert_active_call(status: str):
        logger.info(f"[ACTIVE-CALL] {ctx.room.name} status={status}")
    asyncio.create_task(upsert_active_call("active"))

    # ── Start call recording → Cloudflare R2 (via LiveKit egress) ─────────────
    # Requires: R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET in env.
    egress_id = None
    try:
        rec_api = api.LiveKitAPI(
            url=os.environ["LIVEKIT_URL"],
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )
        egress_resp = await rec_api.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=f"recordings/{ctx.room.name}.ogg",
                    s3=api.S3Upload(
                        access_key=os.environ.get("R2_ACCESS_KEY", ""),
                        secret=os.environ.get("R2_SECRET_KEY", ""),
                        bucket=os.environ.get("R2_BUCKET", "call-recordings"),
                        region="auto",
                        endpoint=os.environ.get("R2_ENDPOINT", ""),
                        force_path_style=False,
                    )
                )]
            )
        )
        egress_id = egress_resp.egress_id
        await rec_api.aclose()
        logger.info(f"[RECORDING] Started egress: {egress_id}")
    except Exception as e:
        logger.warning(f"[RECORDING] Failed to start recording: {e}")

    @session.on("agent_speech_started")
    def _agent_speech_started(ev):
        global agent_is_speaking
        agent_is_speaking = True
        logger.debug("[STATE] Agent speaking: True")

    @session.on("agent_speech_finished")
    def _agent_speech_finished(ev):
        global agent_is_speaking
        agent_is_speaking = False
        logger.debug("[STATE] Agent speaking: False")

    # ── #29 Turn counter + auto-close ─────────────────────────────────────
    turn_count = 0
    MAX_TURNS = live_config.get("max_turns", 20)

    # ── #30 Interrupt counter ─────────────────────────────────────────────
    interrupt_count = 0

    @session.on("agent_speech_interrupted")
    def on_interrupted(ev):
        nonlocal interrupt_count
        interrupt_count += 1
        logger.info(f"[INTERRUPT] Agent interrupted. Total: {interrupt_count}")

    FILLER_WORDS = {
        "okay.", "okay", "ok", "uh", "hmm", "hm", "yeah", "yes",
        "no", "um", "ah", "oh", "right", "sure", "fine", "good",
        "haan", "han", "theek", "theek hai", "accha", "ji", "ha",
    }

    @session.on("user_speech_committed")
    def on_user_speech_committed(ev):
        global agent_is_speaking
        nonlocal turn_count

        transcript = ev.user_transcript.strip()
        transcript_lower = transcript.lower().rstrip(".")

        if agent_is_speaking:
            logger.debug(f"[FILTER-ECHO] Dropped: '{transcript}'")
            return
        if not transcript or len(transcript) < 3:
            logger.debug(f"[FILTER-EMPTY] Dropped empty transcript")
            return
        if transcript_lower in FILLER_WORDS:
            logger.debug(f"[FILTER-FILLER] Dropped filler: '{transcript}'")
            return

        logger.info(f"[TRANSCRIPT] Passing to LLM: '{transcript}'")
        turn_count += 1

        # #33 — Stream transcript to Supabase
        asyncio.create_task(_log_transcript_to_db(ctx.room.name, caller_phone, "user", transcript))

        # #29 — Auto-close on too many turns
        if turn_count >= MAX_TURNS:
            logger.info(f"[LIMIT] {MAX_TURNS} turns reached — wrapping up")
            asyncio.create_task(
                session.generate_reply(
                    instructions="Politely wrap up. Tell the user they can call back anytime. Say goodbye warmly."
                )
            )

    @session.on("agent_speech_committed")
    def on_agent_speech_committed(ev):
        # #33 — Stream agent transcript
        content = getattr(ev, 'agent_transcript', '') or getattr(ev, 'text', '')
        if content:
            asyncio.create_task(_log_transcript_to_db(ctx.room.name, caller_phone, "assistant", content))

    async def _log_transcript_to_db(room_id, phone, role, content):
        try:
            from db import log_transcript_line
            log_transcript_line(room_id, phone, role, content)
        except Exception:
            pass

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        logger.info(f"[HANGUP] Participant disconnected: {participant.identity}")
        # Set flag so transcript filter ignores any final flush
        global agent_is_speaking
        agent_is_speaking = False  # Clear any stuck state
        # Trigger graceful shutdown
        asyncio.create_task(unified_shutdown_hook(ctx))

    # ══════════════════════════════════════════════════════════════════════
    # POST-CALL SHUTDOWN HOOK
    # ══════════════════════════════════════════════════════════════════════

    async def unified_shutdown_hook(shutdown_ctx: JobContext):
        logger.info("Agent shutdown sequence started. Checking for pending bookings...")
        
        booking_status_msg = "No booking"
        if agent_tools.booking_intent:
            from calendar_tools import async_create_booking
            intent = agent_tools.booking_intent
            logger.info(f"Executing post-call booking intent for {intent['start_time']}")
            result = await async_create_booking(
                start_time=intent["start_time"],
                caller_name=intent["caller_name"] or "Unknown Caller",
                caller_phone=intent["caller_phone"],
                notes=intent["notes"],
            )
            if result.get("success"):
                # Build short AI summary from transcript
                short_summary = transcript_text[:300].strip() if 'transcript_text' in dir() else ""
                notify_booking_confirmed(
                    caller_name=intent["caller_name"],
                    caller_phone=intent["caller_phone"],
                    booking_time_iso=intent["start_time"],
                    booking_id=result.get("booking_id"),
                    notes=intent["notes"],
                    tts_voice=tts_voice,
                    ai_summary=short_summary,
                )
                logger.info("Post-call booking executed and notification sent.")
                booking_status_msg = f"Booking Confirmed: {result.get('booking_id')}"
            else:
                logger.error(f"Failed to execute post-call booking: {result.get('message')}")
                booking_status_msg = f"Booking Failed: {result.get('message')}"
        else:
            logger.info("[SHUTDOWN] No booking made — sending follow-up notification.")
            notify_call_no_booking(
                caller_name=agent_tools.caller_name,
                caller_phone=agent_tools.caller_phone,
                call_summary="Caller did not schedule an appointment during this call.",
                tts_voice=tts_voice,
                duration_seconds=int((datetime.now() - call_start_time).total_seconds()),
            )

        # Build Transcript & Save to Supabase
        duration = int((datetime.now() - call_start_time).total_seconds())
        transcript_text = ""
        
        try:
            messages = agent.chat_ctx.messages   
            if callable(messages):
                messages = messages()            

            transcript_lines = []
            for msg in messages:
                if getattr(msg, 'role', None) in ["user", "assistant"]:
                    content = getattr(msg, 'content', "")
                    if isinstance(content, list):
                        content = " ".join([str(c) for c in content if isinstance(c, str)])
                    transcript_lines.append(f"[{msg.role.upper()}] {content}")
            transcript_text = "\n".join(transcript_lines)
        except Exception as e:
            logger.error(f"[SHUTDOWN] Could not read chat history: {e}")
            transcript_text = "unavailable"

        # #14 — Post-call sentiment analysis
        sentiment = "unknown"
        if transcript_text and transcript_text != "unavailable":
            try:
                import openai as oai
                oai_client = oai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
                sent_resp = await oai_client.chat.completions.create(
                    model="gpt-4o-mini", max_tokens=5,
                    messages=[{"role": "user", "content": (
                        "Classify this call transcript as exactly one word: "
                        "positive, neutral, negative, or frustrated.\n\n"
                        f"{transcript_text[:1000]}"
                    )}]
                )
                sentiment = sent_resp.choices[0].message.content.strip().lower()
                logger.info(f"[SENTIMENT] {sentiment}")
            except Exception as e:
                logger.warning(f"[SENTIMENT] Failed: {e}")

        # #34 — Call cost estimation
        def estimate_cost(dur: int, chars: int) -> float:
            return round(
                (dur / 60) * 0.002 + (dur / 60) * 0.006 +
                (chars / 1000) * 0.003 + (chars / 4000) * 0.0001, 5
            )
        estimated_cost = estimate_cost(duration, len(transcript_text))
        logger.info(f"[COST] Estimated: ${estimated_cost}")
        
        # ── Stop recording + build Supabase URL ────────────────────────────────
        recording_url = ""
        if egress_id:
            try:
                stop_api = api.LiveKitAPI(
                    url=os.environ["LIVEKIT_URL"],
                    api_key=os.environ["LIVEKIT_API_KEY"],
                    api_secret=os.environ["LIVEKIT_API_SECRET"],
                )
                await stop_api.egress.stop_egress(
                    api.StopEgressRequest(egress_id=egress_id)
                )
                await stop_api.aclose()
                recording_url = (
                    f"{os.environ.get('R2_PUBLIC_URL', '')}/recordings/{ctx.room.name}.ogg"
                )
                logger.info(f"[RECORDING] Stopped. URL: {recording_url}")
            except Exception as e:
                logger.warning(f"[RECORDING] Failed to stop egress: {e}")

        # #19 — IST time analytics
        ist = pytz.timezone('Asia/Kolkata')
        call_dt = call_start_time.astimezone(ist) if hasattr(call_start_time, 'astimezone') else call_start_time.replace(tzinfo=pytz.utc).astimezone(ist)

        from db import save_call_log
        save_call_log(
            phone=caller_phone,
            duration=duration,
            transcript=transcript_text,
            summary=booking_status_msg,
            recording_url=recording_url,
            sentiment=sentiment,
            interrupt_count=interrupt_count,
            estimated_cost_usd=estimated_cost,
            call_date=call_dt.date().isoformat(),
            call_hour=call_dt.hour,
            call_day_of_week=call_dt.strftime("%A"),
            was_booked=bool(agent_tools.booking_intent),
            stt_provider=live_config.get("stt_provider", "sarvam"),
            tts_provider=live_config.get("tts_provider", "sarvam"),
        )

        # #38 — Mark call as completed
        asyncio.create_task(upsert_active_call("completed"))

        # #18 — Missed-call callback if call was < 5s
        if duration < 5 and caller_phone != "unknown":
            logger.info(f"[MISSED-CALL] Call lasted {duration}s — scheduling callback")
            asyncio.create_task(_schedule_callback(caller_phone))

        # #39 — n8n webhook
        _trigger_n8n({
            "event": "call_completed",
            "phone": caller_phone,
            "caller_name": agent_tools.caller_name,
            "duration": duration,
            "booked": bool(agent_tools.booking_intent),
            "sentiment": sentiment,
            "summary": booking_status_msg,
        })

    async def _schedule_callback(phone: str, delay: int = 300):
        await asyncio.sleep(delay)
        try:
            lk_api = api.LiveKitAPI(
                url=os.environ["LIVEKIT_URL"],
                api_key=os.environ["LIVEKIT_API_KEY"],
                api_secret=os.environ["LIVEKIT_API_SECRET"],
            )
            from livekit.agents import api as agents_api
            await lk_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name="outbound-caller",
                    room=f"callback_{phone}_{int(datetime.now().timestamp())}",
                    metadata=json.dumps({"phone_number": phone, "call_type": "callback"})
                )
            )
            await lk_api.aclose()
            logger.info(f"[CALLBACK] Dispatched to {phone}")
        except Exception as e:
            logger.error(f"[CALLBACK] Failed: {e}")

    def _trigger_n8n(data: dict):
        webhook_url = os.getenv("N8N_WEBHOOK_URL", "")
        if not webhook_url:
            return
        try:
            import httpx
            httpx.post(webhook_url, json=data, timeout=5.0)
            logger.info("[N8N] Webhook triggered")
        except Exception as e:
            logger.warning(f"[N8N] Failed: {e}")

    # NOTE: unified_shutdown_hook is already triggered by the
    # participant_disconnected event above. ctx.add_shutdown_callback
    # is intentionally NOT registered here to prevent double execution.


# ══════════════════════════════════════════════════════════════════════════════
# WORKER ENTRY
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Initialise the database schema on startup (idempotent)
    try:
        from db import init_db
        init_db()
    except Exception as _db_err:
        logging.getLogger("agent").warning(f"[DB] init_db skipped: {_db_err}")
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="outbound-caller" 
    ))
