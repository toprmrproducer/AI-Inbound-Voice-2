import os
import logging
import requests
import httpx
from datetime import datetime

logger = logging.getLogger("calendar-tools")

CAL_BASE = "https://api.cal.com/v1"

def get_cal_creds():
    return {
        "api_key": os.environ.get("CAL_API_KEY", ""),
        "event_id": int(os.environ.get("CAL_EVENT_TYPE_ID", "0"))
    }


# ─── Get available slots for a date ────────────────────────────────────────────

def get_available_slots(date_str: str) -> list:
    """
    Fetch open slots for a given date.
    date_str: "YYYY-MM-DD"
    Returns: [{"time": "2026-02-24T10:00:00+05:30", "label": "10:00 AM"}, ...]
    """
    creds = get_cal_creds()
    try:
        resp = requests.get(
            f"{CAL_BASE}/slots",
            headers={"Content-Type": "application/json"},
            params={
                "apiKey": creds["api_key"],
                "eventTypeId": creds["event_id"],
                "startTime": f"{date_str}T00:00:00.000Z",
                "endTime":   f"{date_str}T23:59:59.000Z",
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw_slots = resp.json().get("data", {}).get("slots", {}).get(date_str, [])

        slots = []
        for s in raw_slots:
            dt = datetime.fromisoformat(s["time"])
            slots.append({
                "time":  s["time"],
                "label": dt.strftime("%-I:%M %p"),   # e.g. "10:00 AM"
            })
        logger.info(f"[CAL] {len(slots)} slots found for {date_str}")
        return slots

    except Exception as e:
        logger.error(f"[CAL] get_available_slots error: {e}")
        return []


# ─── Create a booking ──────────────────────────────────────────────────────────

def create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """Synchronous wrapper around the new async create_booking logic."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # If we are already in an event loop (e.g. agent shutdown), create a task.
        return loop.run_until_complete(async_create_booking(start_time, caller_name, caller_phone, notes))
    except RuntimeError:
        # If no loop is running, run one now.
        return asyncio.run(async_create_booking(start_time, caller_name, caller_phone, notes))

async def async_create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """
    Book a slot on Cal.com via a single POST request to the v2 API.
    start_time: ISO 8601 with IST offset e.g. "2026-02-24T10:00:00+05:30"
    Returns: {"success": bool, "booking_id": str|None, "message": str}
    """
    creds = get_cal_creds()
    payload = {
        "eventTypeId": creds["event_id"],
        "start": start_time,
        "attendee": {
            "name": caller_name,
            "email": f"{caller_phone.replace('+', '').replace(' ', '')}@voiceagent.placeholder",
            "phoneNumber": caller_phone,
            "timeZone": "Asia/Kolkata",
            "language": "en",
        },
        "bookingFieldsResponses": {
            "notes": notes or f"Booked via AI voice agent. Phone: {caller_phone}",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://api.cal.com/v2/bookings",
                headers={
                    "Authorization": f"Bearer {creds['api_key']}",
                    "cal-api-version": "2024-08-13",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200 and resp.status_code != 201:
                logger.error(f"[CAL] Booking failed {resp.status_code}: {resp.text}")
                return {"success": False, "booking_id": None, "message": resp.text}
            
            data = resp.json().get("data", {})
            uid = data.get("uid", "unknown")
            logger.info(f"[CAL] Booking created: uid={uid}")
            return {"success": True, "booking_id": uid, "message": "Booking confirmed"}

    except httpx.TimeoutException:
        logger.error("[CAL] Booking request timed out")
        return {"success": False, "booking_id": None, "message": "Booking service timed out."}
    except Exception as e:
        logger.error(f"[CAL] Booking error: {e}")
        return {"success": False, "booking_id": None, "message": str(e)}


# ─── Cancel a booking ──────────────────────────────────────────────────────────

def cancel_booking(booking_id: str, reason: str = "Cancelled by caller") -> dict:
    """
    Cancel a booking by its UID.
    Returns: {"success": bool, "message": str}
    """
    creds = get_cal_creds()
    try:
        url = f"{CAL_BASE}/bookings/{booking_id}/cancel?apiKey={creds['api_key']}"
        resp = requests.delete(
            url,
            headers={"Content-Type": "application/json"},
            json={"reason": reason},
            timeout=8,
        )
        resp.raise_for_status()
        logger.info(f"[CAL] Booking cancelled: {booking_id}")
        return {"success": True, "message": "Cancelled successfully"}

    except Exception as e:
        logger.error(f"[CAL] cancel_booking error: {e}")
        return {"success": False, "message": str(e)}
