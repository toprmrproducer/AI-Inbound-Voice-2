# agent.py — Clean Multilingual Voice Agent
# Removed: watchdog, agent_is_speaking global, AutoLanguageAgent, filler filter,
#           sentence splitter, before_tts_cb, double-say bug, RoomInputOptions (deprecated)
import os, json, logging, asyncio, time, re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated

import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()

try:
    from pythonjsonlogger import jsonlogger
    _h = logging.StreamHandler()
    _h.setFormatter(jsonlogger.JsonFormatter(
        fmt='%(asctime)s %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S'))
    logging.getLogger().addHandler(_h)
    logging.getLogger().setLevel(logging.INFO)
except ImportError:
    logging.basicConfig(level=logging.INFO)

try:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    _dsn = os.environ.get('SENTRY_DSN', '')
    if _dsn:
        sentry_sdk.init(dsn=_dsn, traces_sample_rate=0.1,
                        integrations=[AsyncioIntegration()])
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

import pytz
from livekit import api
from livekit.agents import Agent, AgentSession, JobContext, RoomInputOptions, WorkerOptions, cli, llm
try:
    from livekit.agents import noisecancellation as nc
except ImportError:
    nc = None
from livekit.plugins import openai as openai_plugin, sarvam, silero

logger      = logging.getLogger('outbound-agent')
agent_logger = logging.getLogger('agent')

# ─────────────────────────── LANGUAGE CONFIG ──────────────────────────────────
LANGUAGE_CONFIG = {
    'hi-IN': {'speaker': 'rohan',    'name': 'Hindi'},
    'en-IN': {'speaker': 'anushka',  'name': 'English'},
    'te-IN': {'speaker': 'ananya',   'name': 'Telugu'},
    'ta-IN': {'speaker': 'pavithra', 'name': 'Tamil'},
    'bn-IN': {'speaker': 'arnav',    'name': 'Bengali'},
    'gu-IN': {'speaker': 'avni',     'name': 'Gujarati'},
    'kn-IN': {'speaker': 'suresh',   'name': 'Kannada'},
    'ml-IN': {'speaker': 'aswin',    'name': 'Malayalam'},
    'mr-IN': {'speaker': 'aarohi',   'name': 'Marathi'},
    'pa-IN': {'speaker': 'gurpreet', 'name': 'Punjabi'},
}

def get_lang_config(code: str) -> dict:
    return LANGUAGE_CONFIG.get(code, LANGUAGE_CONFIG['hi-IN'])

# ─────────────────────────── RATE LIMITER ─────────────────────────────────────
_call_ts: dict = defaultdict(list)

def is_rate_limited(phone: str) -> bool:
    if not phone or phone in ('unknown', 'demo'):
        return False
    now = time.time()
    _call_ts[phone] = [t for t in _call_ts[phone] if now - t < 3600]
    if len(_call_ts[phone]) >= 3:
        return True
    _call_ts[phone].append(now)
    return False

# ─────────────────────────── CONFIG LOADER ────────────────────────────────────
def get_live_config(phone: str = None) -> dict:
    """Load active agent config from DB, then fallback to config.json / env."""
    try:
        import db as _db
        active = _db.get_active_agent()
        if active:
            return {
                'agent_instructions':        active.get('agentinstructions', ''),
                'opening_greeting':          active.get('openinggreeting', ''),
                'first_line':                active.get('firstline', ''),
                'llm_model':                 active.get('llmmodel', 'gpt-4.1-mini'),
                'llm_provider':              active.get('llmprovider', 'openai'),
                'tts_voice':                 active.get('ttsvoice', 'rohan'),
                'tts_language':              active.get('ttslanguage', 'hi-IN'),
                'tts_provider':              active.get('ttsprovider', 'sarvam'),
                'stt_provider':              active.get('sttprovider', 'sarvam'),
                'stt_language':              active.get('sttlanguage', 'unknown'),
                'stt_min_endpointing_delay': float(active.get('sttminendpointingdelay', 0.5)),
                'temperature':               float(active.get('temperature', 0.4)),
                'max_tokens':                int(active.get('max_tokens', 400)),
                'max_turns':                 int(active.get('maxturns', 25)),
            }
    except Exception as e:
        logger.warning(f'[CONFIG] DB load failed, fallback: {e}')

    cfg = {}
    for path in ([f'configs/{phone.replace("+","")}.json'] if phone else []) + ['configs/default.json', 'config.json']:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    cfg = json.load(f)
                break
            except Exception:
                pass

    def v(key, env_key, default):
        return cfg.get(key) or os.getenv(env_key, default)

    return {
        'agent_instructions':        v('agent_instructions', 'AGENT_INSTRUCTIONS', ''),
        'opening_greeting':          v('opening_greeting', 'OPENING_GREETING', ''),
        'first_line':                v('first_line', 'FIRST_LINE', 'Namaste! How can I help you today?'),
        'llm_model':                 v('llm_model', 'LLM_MODEL', 'gpt-4.1-mini'),
        'llm_provider':              v('llm_provider', 'LLM_PROVIDER', 'openai'),
        'tts_voice':                 v('tts_voice', 'TTS_VOICE', 'rohan'),
        'tts_language':              v('tts_language', 'TTS_LANGUAGE', 'hi-IN'),
        'tts_provider':              v('tts_provider', 'TTS_PROVIDER', 'sarvam'),
        'stt_provider':              v('stt_provider', 'STT_PROVIDER', 'sarvam'),
        'stt_language':              v('stt_language', 'STT_LANGUAGE', 'unknown'),
        'stt_min_endpointing_delay': float(v('stt_min_endpointing_delay', 'STT_MIN_ENDPOINTING_DELAY', '0.5')),
        'temperature':               float(v('temperature', 'TEMPERATURE', '0.4')),
        'max_tokens':                int(v('max_tokens', 'MAX_TOKENS', '400')),
        'max_turns':                 int(v('max_turns', 'MAX_TURNS', '25')),
    }

# ─────────────────────────── IST TIME CONTEXT ────────────────────────────────
def get_ist_context() -> str:
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    rows = []
    for i in range(7):
        d     = now + timedelta(days=i)
        label = 'Today' if i == 0 else 'Tomorrow' if i == 1 else d.strftime('%A')
        rows.append(f'{label}: {d.strftime("%A %d %B %Y")} | ISO: {d.strftime("%Y-%m-%d")}')
    return (
        f'\n[SYSTEM] {now.strftime("%A %d %B %Y, %I:%M %p")} IST\n'
        + '\n'.join(rows)
        + '\nUse ISO date in save_booking_intent. All times are IST (+05:30).\n'
    )

# ─────────────────────────── TOOL CONTEXT ─────────────────────────────────────
from calendar_tools import get_available_slots
from notify import (notify_booking_confirmed, notify_call_no_booking, notify_agent_error)


class AgentTools(llm.ToolContext):
    def __init__(self, caller_phone: str, caller_name: str):
        super().__init__(tools=[])
        self.caller_phone    = caller_phone
        self.caller_name     = caller_name
        self.booking_intent  = None
        self.sip_identity    = None
        self.ctx_api         = None
        self.room_name       = None
        self._sip_domain     = os.getenv('VOBIZ_SIP_DOMAIN', '')

    @llm.function_tool(description=(
        'Transfer this call to a human agent. Use when caller explicitly asks for human, '
        'is angry, or the query is completely outside your scope.'
    ))
    async def transfer_call(self) -> str:
        dest = os.getenv('DEFAULT_TRANSFER_NUMBER', '')
        if dest and self._sip_domain and '@' not in dest:
            dest = f'sip:{dest}@{self._sip_domain}'
        try:
            if self.ctx_api and self.room_name and self.sip_identity and dest:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self.sip_identity,
                        transfer_to=dest,
                        play_dialtone=False,
                    ))
                return 'Transfer initiated.'
        except Exception as e:
            logger.error(f'[TRANSFER] Failed: {e}')
        return "I'm having trouble transferring right now. Please hold on."

    @llm.function_tool(description=(
        'End the call. Only use AFTER saying a brief goodbye AND after the caller confirms '
        'they need nothing else. Do NOT use mid-conversation.'
    ))
    async def end_call(self) -> str:
        try:
            if self.ctx_api and self.room_name and self.sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self.sip_identity,
                        transfer_to='tel:0',
                        play_dialtone=False,
                    ))
        except Exception as e:
            logger.warning(f'[END_CALL] Graceful hangup failed: {e}')
        return 'Call ended.'

    @llm.function_tool(description=(
        'Save the caller\'s booking intent. Call ONLY after caller has confirmed: '
        'full name, email address, AND specific date+time. All three required.'
    ))
    async def save_booking_intent(
        self,
        start_time: Annotated[str, 'ISO 8601 datetime with IST offset e.g. 2026-03-10T10:00:00+05:30'],
        caller_name: Annotated[str, 'Full name of caller'],
        caller_email: Annotated[str, 'Email address of caller'],
        notes: Annotated[str, 'Any service or treatment notes'] = '',
    ) -> str:
        if caller_name and len(caller_name.strip()) > 1:
            self.caller_name = caller_name.strip()
        self.booking_intent = {
            'start_time':   start_time,
            'caller_name':  self.caller_name,
            'caller_phone': self.caller_phone,
            'caller_email': caller_email,
            'notes':        notes,
        }
        logger.info(f'Booking intent saved: {start_time} for {self.caller_name}')
        return (
            f'Booking intent saved for {start_time}. '
            'Tell the caller their appointment is confirmed and they will receive a confirmation shortly.'
        )

    @llm.function_tool(description='Cancel booking made in THIS call only.')
    async def cancel_appointment(
        self,
        reason: Annotated[str, 'Reason for cancellation'] = 'Caller changed their mind',
    ) -> str:
        if not self.booking_intent:
            return "I don't have an active booking from this call to cancel."
        self.booking_intent = None
        return "I've cancelled your booking. Would you like to reschedule?"

    @llm.function_tool(description=(
        'Check available appointment slots for a given date. '
        'Call when caller asks about availability or wants to book.'
    ))
    async def check_availability(
        self,
        date: Annotated[str, 'Date in YYYY-MM-DD format'],
    ) -> str:
        try:
            slots = await get_available_slots(date)
            if not slots:
                return f'No available slots on {date}. Would you like to check another date?'
            slot_strs = [s.get('start_time', str(s)) for s in slots[:6]]
            return f'Available slots on {date}: {", ".join(slot_strs)}'
        except Exception as e:
            logger.error(f'[AVAILABILITY] {e}')
            return "I'm having trouble checking the calendar right now."

    @llm.function_tool(description='Get business operating hours and whether currently open.')
    async def get_business_hours(self) -> str:
        ist   = pytz.timezone('Asia/Kolkata')
        now   = datetime.now(ist)
        hours = {
            0: ('Monday', '10:00', '19:00'),
            1: ('Tuesday', '10:00', '19:00'),
            2: ('Wednesday', '10:00', '19:00'),
            3: ('Thursday', '10:00', '19:00'),
            4: ('Friday', '10:00', '19:00'),
            5: ('Saturday', '10:00', '17:00'),
            6: ('Sunday', None, None),
        }
        name, o, c = hours[now.weekday()]
        if o is None:
            return 'We are closed on Sundays. We reopen Monday at 10:00 AM.'
        cur = now.strftime('%H%M')
        status = 'OPEN' if o.replace(':', '') <= cur <= c.replace(':', '') else 'CLOSED'
        return f'We are currently {status}. {name} hours: {o}–{c} IST.'


# ─────────────────────────── VOICE AGENT ──────────────────────────────────────
class VoiceAgent(Agent):
    """
    Clean single-class multilingual voice agent.
    Language detection fires on FIRST user utterance only — never re-detects.
    TTS speaker swaps to match detected language.
    No watchdog, no global state, no filler filter.
    """

    def __init__(self, tools: AgentTools, config: dict):
        self._tools       = tools
        self._config      = config
        self._session     = None     # set after session.start()
        self._lang_locked = False
        self._lang        = config.get('tts_language', 'hi-IN')

        instructions = self._build_instructions(config.get('agent_instructions', ''))

        try:
            import tiktoken
            enc = tiktoken.encoding_for_model('gpt-4o')
            tc  = len(enc.encode(instructions))
            agent_logger.info(f'[PROMPT] System prompt is {tc} tokens')
            if tc > 600:
                agent_logger.warning(f'[PROMPT] Prompt is {tc} tokens — consider trimming')
        except Exception:
            pass

        super().__init__(
            instructions=instructions,
            tools=llm.find_function_tools(tools),
        )

    def _build_instructions(self, base: str) -> str:
        voice_rules = (
            '\n[VOICE RULES — CRITICAL]\n'
            '- Speak in SHORT sentences. Max 12 words per response.\n'
            '- NEVER produce more than ONE sentence per turn.\n'
            '- NEVER repeat yourself or say the same thing twice.\n'
            '- NEVER say "confirmed" more than once in a call.\n'
            '- Answer the caller\'s question FIRST. Suggest booking ONLY after their question is answered.\n'
            '- Stay in the SAME language the caller first speaks in. NEVER switch languages mid-call.\n'
            '- If you don\'t know something, say so honestly. Don\'t deflect to booking.\n'
        )
        return base + get_ist_context() + voice_rules

    async def on_enter(self):
        """Called once by AgentSession after session.start(). Speak greeting, nothing else."""
        greeting = (
            self._config.get('opening_greeting')
            or self._config.get('first_line')
            or 'Namaste! How can I help you today?'
        )
        await self.session.say(greeting, allow_interruptions=True)

    async def on_user_turn_completed(self, turn_ctx, new_message):
        """
        Lock language on FIRST utterance only.
        After locking, update instructions and swap TTS speaker.
        Never re-detect after that.
        """
        if not self._lang_locked:
            detected = None
            try:
                detected = getattr(new_message, 'language', None)
                if not detected:
                    detected = new_message.content[0].language
            except Exception:
                pass

            if detected and detected not in ('unknown', '', None) and detected in LANGUAGE_CONFIG:
                self._lang        = detected
                self._lang_locked = True
                cfg_lang          = get_lang_config(detected)
                logger.info(f'[LANG] Locked to {cfg_lang["name"]} ({detected}) on first utterance')

                # Update instructions to hard-lock language
                lang_lock = (
                    f'CRITICAL: The caller speaks {cfg_lang["name"]}. '
                    f'You MUST respond ONLY in {cfg_lang["name"]} for the ENTIRE call. '
                    f'Do NOT switch to any other language under any circumstances.\n'
                )
                self.instructions = lang_lock + self._build_instructions(
                    self._config.get('agent_instructions', '')
                )

                # Swap TTS speaker to match language
                if self._session is not None:
                    try:
                        self._session.tts = sarvam.TTS(
                            model='bulbul:v3',
                            speaker=cfg_lang['speaker'],
                            target_language_code=detected,
                            enable_preprocessing=True,
                            pace=0.95,
                        )
                        logger.info(f'[LANG] TTS swapped to speaker={cfg_lang["speaker"]}')
                    except Exception as e:
                        logger.warning(f'[LANG] TTS swap failed (non-critical): {e}')

        await super().on_user_turn_completed(turn_ctx, new_message)


# ─────────────────────────── DEMO SESSION ────────────────────────────────────
async def run_demo_session(ctx: JobContext):
    logger.info(f'[DEMO] Browser session: {ctx.room.name}')
    config      = get_live_config()
    agent_tools = AgentTools(caller_phone='demo', caller_name='Demo Visitor')
    agent       = VoiceAgent(tools=agent_tools, config=config)

    session = AgentSession(
        stt=sarvam.STT(model='saaras:v3', language='unknown', mode='transcribe', flush_signal=True),
        llm=openai_plugin.LLM(
            model=config.get('llm_model', 'gpt-4.1-mini'),
            temperature=config.get('temperature', 0.4),
            max_tokens=config.get('max_tokens', 400),
        ),
        tts=sarvam.TTS(
            model='bulbul:v3',
            speaker=get_lang_config(config.get('tts_language', 'hi-IN'))['speaker'],
            target_language_code=config.get('tts_language', 'hi-IN'),
            enable_preprocessing=True,
            pace=0.95,
        ),
        vad=silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=0.8,
            activation_threshold=0.55,
            deactivation_threshold=0.40,
            prefix_padding_duration=0.1,
        ),
        turn_detection='stt',
        min_interruption_duration=0.0,
        min_interruption_words=0,
        min_endpointing_delay=0.5,
        allow_interruptions=True,
    )
    agent._session = session
    await session.start(room=ctx.room, agent=agent)
    # on_enter fires automatically — do NOT call session.say() here again
    await ctx.wait_for_disconnect()
    logger.info(f'[DEMO] Session ended: {ctx.room.name}')


# ─────────────────────────── MAIN ENTRYPOINT ─────────────────────────────────
async def entrypoint(ctx: JobContext):
    logger.info(f'[JOB] id={ctx.job.id}')
    raw_meta = ctx.job.metadata or ''
    logger.info(f'[JOB] id={ctx.job.id} — metadata parsed securely')

    await ctx.connect()
    logger.info(f'[ROOM] Connected: {ctx.room.name}')

    # ── Demo path ─────────────────────────────────────────────────────────────
    if ctx.room.name.startswith('demo-') or raw_meta.strip() == 'demo':
        await run_demo_session(ctx)
        return

    # ── Parse metadata ────────────────────────────────────────────────────────
    phone_number = None
    caller_name  = 'Unknown'
    sip_trunk_id = os.environ.get('OUTBOUND_TRUNK_ID') or os.environ.get('SIP_TRUNK_ID', '')

    if raw_meta.strip():
        try:
            meta         = json.loads(raw_meta)
            phone_number = meta.get('phone_number') or meta.get('to') or meta.get('destination')
            caller_name  = meta.get('name', caller_name)
            if meta.get('sip_trunk_id'):
                sip_trunk_id = meta['sip_trunk_id']
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f'[METADATA] Parse error: {e} — treating as inbound')

    caller_phone = phone_number or 'unknown'

    if is_rate_limited(caller_phone):
        logger.warning(f'[RATE-LIMIT] Blocked {caller_phone}')
        return

    # ── Outbound SIP dial ─────────────────────────────────────────────────────
    if phone_number:
        logger.info(f'[CALL] Outbound → {phone_number}')
        try:
            lk = api.LiveKitAPI(
                url=os.environ['LIVEKIT_URL'],
                api_key=os.environ['LIVEKIT_API_KEY'],
                api_secret=os.environ['LIVEKIT_API_SECRET'],
            )
            await lk.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=sip_trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=f'sip_{phone_number.replace("+", "")}',
                    participant_name='Caller',
                    wait_until_answered=True,
                ))
            await lk.aclose()
            logger.info(f'[SIP] Outbound call dispatched to {phone_number} and answered.')
        except Exception as e:
            logger.error(f'[SIP] Dispatch failed: {e}')
            notify_agent_error(phone_number or 'unknown', str(e))
            return

    # ── Caller identity from SIP ──────────────────────────────────────────────
    participant_identity = f'sip_{caller_phone.replace("+", "")}' if phone_number else 'inbound_caller'
    for identity, p in ctx.room.remote_participants.items():
        if p.name and p.name not in ('', 'Caller', 'Unknown'):
            caller_name = p.name
            logger.info(f'[CALLER-ID] {caller_name}')
            break

    # ── Load config ───────────────────────────────────────────────────────────
    config = get_live_config(phone_number)

    # Inject caller history if exists
    if caller_phone != 'unknown':
        try:
            from db import fetch_call_logs
            logs = fetch_call_logs(limit=50)
            prev = [l for l in logs if l.get('phone') == caller_phone]
            if prev:
                last = prev[0]
                history_note = (
                    f'\n[CALLER HISTORY] Last call: {str(last.get("created_at",""))[:10]}. '
                    f'Summary: {last.get("summary", "N/A")}\n'
                )
                config['agent_instructions'] = config.get('agent_instructions', '') + history_note
        except Exception as e:
            logger.warning(f'[HISTORY] Could not load: {e}')

    # ── Build agent and tools ──────────────────────────────────────────────────
    agent_tools              = AgentTools(caller_phone=caller_phone, caller_name=caller_name)
    agent_tools.sip_identity = participant_identity
    agent_tools.ctx_api      = ctx.api
    agent_tools.room_name    = ctx.room.name

    agent = VoiceAgent(tools=agent_tools, config=config)

    # ── Build LLM ─────────────────────────────────────────────────────────────
    llm_provider = config.get('llm_provider', 'openai')
    llm_model    = config.get('llm_model', 'gpt-4.1-mini')
    temperature  = config.get('temperature', 0.4)
    max_tokens   = config.get('max_tokens', 400)

    if llm_provider == 'groq':
        active_llm = openai_plugin.LLM.with_groq(model=llm_model or 'llama-3.3-70b-versatile')
    elif llm_provider == 'anthropic':
        active_llm = openai_plugin.LLM(
            model='claude-haiku-3-5-latest',
            base_url='https://api.anthropic.com/v1',
            api_key=os.environ.get('ANTHROPIC_API_KEY', ''),
        )
    else:
        active_llm = openai_plugin.LLM(
            model=llm_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ── Build STT ─────────────────────────────────────────────────────────────
    active_stt = sarvam.STT(
        model='saaras:v3',
        language='unknown',   # auto-detect, locked after first turn in agent
        mode='transcribe',
        flush_signal=True,
    )

    # ── Build TTS ─────────────────────────────────────────────────────────────
    tts_lang   = config.get('tts_language', 'hi-IN')
    active_tts = sarvam.TTS(
        model='bulbul:v3',
        speaker=get_lang_config(tts_lang)['speaker'],
        target_language_code=tts_lang,
        enable_preprocessing=True,
        pace=0.95,
    )

    # ── VAD ───────────────────────────────────────────────────────────────────
    vad = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.8,
        activation_threshold=0.55,
        deactivation_threshold=0.40,
        prefix_padding_duration=0.1,
    )

    # ── Noise cancellation (uses RoomInputOptions for LiveKit 1.4.2 compatibility) ──
    room_options = RoomInputOptions()
    if nc is not None:
        try:
            room_options = RoomInputOptions(
                noise_cancellation=nc.BVC(),
            )
            logger.info('[AUDIO] BVC noise cancellation enabled')
        except Exception as e:
            logger.warning(f'[AUDIO] BVC unavailable: {e}')

    # ── Session ───────────────────────────────────────────────────────────────
    session = AgentSession(
        stt=active_stt,
        llm=active_llm,
        tts=active_tts,
        vad=vad,
        turn_detection='stt',
        min_interruption_duration=0.0,
        min_interruption_words=0,
        min_endpointing_delay=config.get('stt_min_endpointing_delay', 0.5),
        preemptive_generation=True,
        allow_interruptions=True,
    )
    agent._session = session  # so on_user_turn_completed can swap TTS

    # ── Recording ─────────────────────────────────────────────────────────────
    egress_id     = None
    recording_url = ''
    call_start    = datetime.now()

    try:
        rec_api = api.LiveKitAPI(
            url=os.environ['LIVEKIT_URL'],
            api_key=os.environ['LIVEKIT_API_KEY'],
            api_secret=os.environ['LIVEKIT_API_SECRET'],
        )
        egress_resp = await rec_api.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=f'recordings/{ctx.room.name}.ogg',
                    s3=api.S3Upload(
                        access_key=os.environ.get('R2_ACCESS_KEY', ''),
                        secret=os.environ.get('R2_SECRET_KEY', ''),
                        bucket=os.environ.get('R2_BUCKET', 'call-recordings'),
                        region='auto',
                        endpoint=os.environ.get('R2_ENDPOINT', ''),
                        force_path_style=False,
                    )
                )]
            ))
        egress_id = egress_resp.egress_id
        await rec_api.aclose()
        logger.info(f'[RECORDING] Started egress: {egress_id}')
    except Exception as e:
        logger.warning(f'[RECORDING] Failed to start: {e}')

    # ── Start session (on_enter fires automatically — do NOT call session.say() here) ──
    await session.start(room=ctx.room, agent=agent, room_input_options=room_options)
    logger.info('[AGENT] Session live — waiting for caller audio.')

    try:
        from db import init_db
        init_db()
    except Exception:
        pass

    # ── Shutdown callback ─────────────────────────────────────────────────────
    async def on_shutdown(shutdown_ctx):
        logger.info('Agent shutdown sequence started. Checking for pending bookings...')

        # Execute booking
        if agent_tools.booking_intent:
            intent = agent_tools.booking_intent
            logger.info(f'Executing post-call booking intent for {intent["start_time"]}')
            try:
                from calendar_tools import async_create_booking
                result = await async_create_booking(
                    start_time=intent['start_time'],
                    caller_name=intent['caller_name'] or 'Unknown',
                    caller_phone=intent['caller_phone'],
                    notes=intent.get('notes', ''),
                )
                if result.get('success'):
                    notify_booking_confirmed(
                        caller_name=intent['caller_name'],
                        caller_phone=intent['caller_phone'],
                        booking_time_iso=intent['start_time'],
                        booking_id=result.get('booking_id'),
                        notes=intent.get('notes', ''),
                        tts_voice=config.get('tts_voice', 'rohan'),
                        ai_summary='',
                    )
                    logger.info(f'Post-call booking executed and notification sent.')
                else:
                    logger.error(f'Booking failed: {result.get("message")}')
            except Exception as e:
                logger.error(f'[BOOKING] async_create_booking failed: {e}')
        else:
            logger.info('[SHUTDOWN] No booking made — sending follow-up notification.')
            notify_call_no_booking(
                caller_name=agent_tools.caller_name,
                caller_phone=agent_tools.caller_phone,
                call_summary='Caller did not schedule an appointment.',
                tts_voice=config.get('tts_voice', 'rohan'),
                duration_seconds=int((datetime.now() - call_start).total_seconds()),
            )

        # Stop recording
        if egress_id:
            try:
                stop_api = api.LiveKitAPI(
                    url=os.environ['LIVEKIT_URL'],
                    api_key=os.environ['LIVEKIT_API_KEY'],
                    api_secret=os.environ['LIVEKIT_API_SECRET'],
                )
                await stop_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
                await stop_api.aclose()
                nonlocal recording_url
                recording_url = f'{os.environ.get("R2_PUBLIC_URL","")}/recordings/{ctx.room.name}.ogg'
                logger.info(f'[RECORDING] Stopped. URL: {recording_url}')
            except Exception as e:
                logger.warning(f'[RECORDING] Stop failed: {e}')

        # Build transcript from chat history
        transcript_text = ''
        try:
            msgs = []
            for msg in agent.chat_ctx.messages:
                content = getattr(msg, 'content', None)
                if isinstance(content, list):
                    content = ' '.join(str(c) for c in content if isinstance(c, str))
                if content:
                    msgs.append(f'{msg.role.upper()}: {content}')
            transcript_text = '\n'.join(msgs)
        except Exception as e:
            logger.error(f'[TRANSCRIPT] Could not read: {e}')
            transcript_text = 'unavailable'

        # GROQ summarization (fixed: explicit language + robust prompt)
        summary = 'No summary available'
        groq_key = os.getenv('GROQ_API_KEY', '')
        if groq_key and transcript_text and transcript_text != 'unavailable':
            try:
                import httpx
                r = httpx.post(
                    'https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {groq_key}'},
                    json={
                        'model': 'llama-3.3-70b-versatile',
                        'messages': [
                            {
                                'role': 'system',
                                'content': (
                                    'You are a call summarizer. The transcript may be in Hindi, Telugu, '
                                    'Tamil, Bengali, or English. Summarize in English in 2 sentences max. '
                                    'Include: was a booking made, caller intent, outcome.'
                                ),
                            },
                            {'role': 'user', 'content': transcript_text[:3000]},
                        ],
                        'max_tokens': 150,
                        'temperature': 0.1,
                    },
                    timeout=10.0,
                )
                if r.status_code == 200:
                    summary = r.json()['choices'][0]['message']['content'].strip()
                else:
                    logger.warning(f'[GROQ] Summarization failed: HTTP {r.status_code} — {r.text[:200]}')
            except Exception as e:
                logger.warning(f'[GROQ] Summarization error: {e}')

        # Sentiment (simple heuristic)
        tl = transcript_text.lower()
        pos = sum(1 for w in ['thank', 'great', 'perfect', 'confirmed', 'yes', 'please', 'wonderful'] if w in tl)
        neg = sum(1 for w in ['angry', 'frustrated', 'terrible', 'cancel', 'wrong', 'problem', 'waste'] if w in tl)
        sentiment = 'positive' if pos > neg + 1 else 'negative' if neg > pos else 'neutral'
        logger.info(f'[SENTIMENT] {sentiment}')

        # Cost estimate
        try:
            import tiktoken
            enc  = tiktoken.encoding_for_model('gpt-4o')
            toks = len(enc.encode(transcript_text))
            dur  = (datetime.now() - call_start).total_seconds()
            cost = round(toks * 0.00000015 + dur * 0.0000025, 5)
            logger.info(f'[COST] Estimated: ${cost}')
        except Exception:
            cost = 0

        # Save to DB
        ist      = pytz.timezone('Asia/Kolkata')
        call_dt  = call_start.astimezone(ist)
        duration = int((datetime.now() - call_start).total_seconds())
        try:
            from db import save_call_log
            save_call_log(
                phone=caller_phone,
                duration=duration,
                transcript=transcript_text,
                summary=summary,
                recording_url=recording_url,
                sentiment=sentiment,
                interrupt_count=0,
                estimated_cost_usd=cost,
                call_date=call_dt.date().isoformat(),
                call_hour=call_dt.hour,
                call_day_of_week=call_dt.strftime('%A'),
                was_booked=bool(agent_tools.booking_intent),
                stt_provider=config.get('stt_provider', 'sarvam'),
                tts_provider=config.get('tts_provider', 'sarvam'),
            )
            logger.info(f'[DB] Call log saved for {caller_phone}')
        except Exception as e:
            logger.error(f'[DB] save_call_log failed: {e}')

        logger.info(f'[ACTIVE-CALL]  status=completed phone={caller_phone}')

    ctx.add_shutdown_callback(on_shutdown)

    # Wait for the call to finish — this keeps the entrypoint alive
    await ctx.wait_for_disconnect()


# ─────────────────────────── WORKER ENTRY ─────────────────────────────────────
# cli.run MUST be at module level, NOT inside entrypoint()
if __name__ == '__main__':
    cli.run(app=WorkerOptions(entrypoint_fnc=entrypoint, agent_name='outbound-caller'))
