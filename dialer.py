"""
dialer.py — Campaign Dialer Engine
-----------------------------------
Runs as an asyncio task launched by ui_server.py when a campaign is started.
Fetches pending leads, creates LiveKit SIP participants, throttles per campaign
`calls_per_minute`, and updates lead status in the DB.

Usage (called from ui_server.py):
    asyncio.create_task(run_dialer_for_campaign(campaign_id, broadcast_fn))
"""

import os
import json
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("dialer")


async def run_dialer_for_campaign(campaign_id: int, broadcast_fn=None):
    """
    Main dialer loop for a campaign.

    Args:
        campaign_id: DB id of the campaign row.
        broadcast_fn: async callable(dict) to notify WebSocket clients.
                      Can be None — the dialer works without it.
    """
    import db

    logger.info(f"[DIALER] Starting dialer for campaign {campaign_id}")

    # ── Load campaign config ─────────────────────────────────────────────────
    campaign = db.get_campaign_full(campaign_id)
    if not campaign:
        logger.error(f"[DIALER] Campaign {campaign_id} not found — aborting")
        return

    if campaign.get("status") != "active":
        logger.warning(f"[DIALER] Campaign {campaign_id} is not active (status={campaign.get('status')}) — aborting")
        return

    sip_trunk_id = campaign.get("sip_trunk_id")
    if not sip_trunk_id:
        logger.error(f"[DIALER] Campaign {campaign_id} has no SIP trunk configured")
        await _finish_campaign(campaign_id, "completed")
        return

    # Resolve the LiveKit SIP trunk ID from env or config
    livekit_sip_trunk_id = (
        os.environ.get("OUTBOUND_TRUNK_ID")
        or os.environ.get("SIP_TRUNK_ID")
        or ""
    )
    if not livekit_sip_trunk_id:
        logger.error("[DIALER] OUTBOUND_TRUNK_ID / SIP_TRUNK_ID not set — aborting")
        return

    agent_id = campaign.get("agent_id")
    calls_per_minute = int(campaign.get("calls_per_minute") or 5)
    retry_failed = bool(campaign.get("retry_failed", True))
    max_retries = int(campaign.get("max_retries") or 2)

    # Interval between calls (seconds)
    call_interval = 60.0 / max(calls_per_minute, 1)

    # ── Import LiveKit API ────────────────────────────────────────────────────
    try:
        from livekit import api as lk_api
        lk = lk_api.LiveKitAPI(
            url=os.environ["LIVEKIT_URL"],
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )
    except Exception as e:
        logger.error(f"[DIALER] Failed to create LiveKit client: {e}")
        return

    dispatched = 0
    retried = False

    try:
        while True:
            # ── Check if campaign is still active ────────────────────────────
            fresh = db.get_campaign_full(campaign_id)
            if not fresh or fresh.get("status") != "active":
                logger.info(f"[DIALER] Campaign {campaign_id} status={fresh.get('status') if fresh else 'gone'} — stopping")
                break

            # ── Fetch next pending lead ───────────────────────────────────────
            leads = db.get_pending_leads(campaign_id, limit=1)

            if not leads:
                # If retry_failed is on and we haven't retried yet
                if retry_failed and not retried:
                    requeued = db.requeue_failed_leads(campaign_id)
                    if requeued > 0:
                        logger.info(f"[DIALER] Requeued {requeued} failed leads for retry")
                        retried = True
                        continue

                logger.info(f"[DIALER] No more pending leads for campaign {campaign_id} — completing")
                await _finish_campaign(campaign_id, "completed")
                break

            lead = leads[0]
            lead_id = str(lead["id"])
            phone = lead["phone"]
            name = lead.get("name") or "Unknown"

            # ── DNC check ────────────────────────────────────────────────────
            if db.is_in_dnc(phone):
                logger.warning(f"[DIALER] Skipping {phone} — on DNC list")
                db.update_lead_status(lead_id, "failed")
                continue

            # ── Mark lead as calling ─────────────────────────────────────────
            db.update_lead_status(lead_id, "calling")

            # ── Build metadata for agent.py ──────────────────────────────────
            metadata = {
                "phone_number": phone,
                "name": name,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
            }
            if agent_id:
                metadata["agent_id"] = str(agent_id)

            room_name = f"campaign-{campaign_id}-lead-{lead_id[:8]}"

            # ── Dispatch LiveKit SIP participant ─────────────────────────────
            try:
                await lk.room.create_room(
                    lk_api.CreateRoomRequest(name=room_name)
                )
                await lk.agent_dispatch.create_dispatch(
                    lk_api.CreateAgentDispatchRequest(
                        agent_name="outbound-caller",
                        room=room_name,
                        metadata=json.dumps(metadata),
                    )
                )
                logger.info(f"[DIALER] Dispatched call to {phone} | room={room_name} | lead={lead_id}")
                dispatched += 1

                # Broadcast to WebSocket clients
                if broadcast_fn:
                    try:
                        await broadcast_fn({
                            "type": "call_status",
                            "lead_id": lead_id,
                            "campaign_id": campaign_id,
                            "phone": phone,
                            "name": name,
                            "room": room_name,
                            "status": "calling",
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    except Exception as ws_err:
                        logger.warning(f"[DIALER] WS broadcast failed: {ws_err}")

            except Exception as e:
                logger.error(f"[DIALER] Failed to dispatch call for {phone}: {e}")
                db.update_lead_status(lead_id, "failed")

                if broadcast_fn:
                    try:
                        await broadcast_fn({
                            "type": "call_status",
                            "lead_id": lead_id,
                            "campaign_id": campaign_id,
                            "phone": phone,
                            "status": "failed",
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    except Exception:
                        pass

            # ── Throttle ─────────────────────────────────────────────────────
            await asyncio.sleep(call_interval)

    except asyncio.CancelledError:
        logger.info(f"[DIALER] Campaign {campaign_id} dialer task cancelled")
    except Exception as e:
        logger.error(f"[DIALER] Unexpected error in dialer loop: {e}", exc_info=True)
    finally:
        try:
            await lk.aclose()
        except Exception:
            pass
        logger.info(
            f"[DIALER] Campaign {campaign_id} dialer stopped. "
            f"Dispatched {dispatched} calls."
        )


async def _finish_campaign(campaign_id: int, status: str):
    """Mark campaign complete in the DB."""
    try:
        import db
        db.update_campaign_status(campaign_id, status)
        logger.info(f"[DIALER] Campaign {campaign_id} marked as {status}")
    except Exception as e:
        logger.error(f"[DIALER] Failed to mark campaign {campaign_id} as {status}: {e}")
