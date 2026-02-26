import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger("notify")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_URL       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


# ─── Core sender ───────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    """
    Fire a single POST to Telegram. No library needed.
    Supports Markdown formatting: *bold*, _italic_, `code`
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TELEGRAM] Token or Chat ID not set in .env — skipping.")
        return False
    try:
        resp = requests.post(
            TELEGRAM_URL,
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "Markdown",
            },
            timeout=5,
        )
        resp.raise_for_status()
        logger.info("[TELEGRAM] Message sent successfully.")
        return True
    except Exception as e:
        logger.error(f"[TELEGRAM] Failed: {e}")
        return False


# ─── Message Templates ─────────────────────────────────────────────────────────

def notify_booking_confirmed(
    caller_name: str,
    caller_phone: str,
    booking_time_iso: str,
    booking_id: str,
    notes: str = "",
    tts_voice: str = "",
    ai_summary: str = "",
) -> bool:
    """
    Sends a rich, formatted Telegram message when a booking is confirmed.
    """
    try:
        dt = datetime.fromisoformat(booking_time_iso)
        readable = dt.strftime("%A, %d %B %Y at %-I:%M %p IST")
    except Exception:
        readable = booking_time_iso

    message = (
        f"✅ *New Booking Confirmed!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:*        {caller_name}\n"
        f"📞 *Phone:*       `{caller_phone}`\n"
        f"📅 *Time:*        {readable}\n"
        f"🔖 *Booking ID:*  `{booking_id}`\n"
        f"📝 *Notes:*       {notes or '—'}\n"
        f"🎙️ *Voice Model:* {tts_voice or '—'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        + (f"💬 *AI Summary:*\n_{ai_summary}_\n\n" if ai_summary else "")
        + f"_Booked via RapidXAI Voice Agent_ 🤖"
    )
    return send_telegram(message)


def notify_booking_cancelled(
    caller_name: str,
    caller_phone: str,
    booking_id: str,
    reason: str = "",
) -> bool:
    """
    Sends a Telegram message when a booking is cancelled during the call.
    """
    message = (
        f"❌ *Booking Cancelled*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:*      {caller_name}\n"
        f"📞 *Phone:*     `{caller_phone}`\n"
        f"🔖 *Booking ID:* `{booking_id}`\n"
        f"💬 *Reason:*    {reason or 'Caller changed mind'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_RapidXAI Voice Agent_ 🤖"
    )
    return send_telegram(message)


def notify_call_no_booking(
    caller_name: str,
    caller_phone: str,
    call_summary: str = "",
    tts_voice: str = "",
    ai_summary: str = "",
    duration_seconds: int = 0,
) -> bool:
    """
    Fires when a call ends WITHOUT any booking being made.
    """
    message = (
        f"📵 *Call Ended — No Booking*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:*        {caller_name or 'Unknown'}\n"
        f"📞 *Phone:*       `{caller_phone}`\n"
        f"⏱️ *Duration:*    {duration_seconds}s\n"
        f"🎙️ *Voice Model:* {tts_voice or '—'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        + (f"💬 *AI Summary:*\n_{ai_summary or call_summary or 'Caller did not schedule.'}_\n\n")
        + f"_Consider a manual follow-up call_ 📲\n"
        f"_RapidXAI Voice Agent_ 🤖"
    )
    return send_telegram(message)


def notify_agent_error(caller_phone: str, error: str) -> bool:
    """
    Fires if something crashes mid-call so you always know about failures.
    """
    message = (
        f"⚠️ *Agent Error During Call*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 *Phone:*  `{caller_phone}`\n"
        f"🔴 *Error:*  `{error}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_RapidXAI Voice Agent_ 🤖"
    )
    return send_telegram(message)


# ── WhatsApp Confirmation (#16) ────────────────────────────────────────────────

def notify_whatsapp_booking(caller_phone: str, booking_time: str, caller_name: str) -> None:
    """Send WhatsApp confirmation via Twilio API (non-blocking)."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    if not account_sid or not auth_token:
        return
    message = (
        f"Hi {caller_name}! Your appointment has been confirmed for "
        f"{booking_time}. We look forward to seeing you! 💆"
    )
    try:
        import requests as req
        resp = req.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={"From": from_number, "To": f"whatsapp:{caller_phone}", "Body": message},
            timeout=5,
        )
        resp.raise_for_status()
        logger.info(f"[WHATSAPP] Sent to {caller_phone}")
    except Exception as e:
        logger.error(f"[WHATSAPP] Failed: {e}")


# ── Generic Webhook Delivery (#35) ────────────────────────────────────────────

async def send_webhook(webhook_url: str, event_type: str, payload: dict) -> None:
    """Deliver an event to a client's webhook URL."""
    if not webhook_url:
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook_url,
                json={"event": event_type, "timestamp": datetime.utcnow().isoformat(), "data": payload},
                timeout=5.0,
                headers={"Content-Type": "application/json"},
            )
            logger.info(f"[WEBHOOK] {event_type} → {resp.status_code}")
    except Exception as e:
        logger.warning(f"[WEBHOOK] Failed to deliver {event_type}: {e}")
