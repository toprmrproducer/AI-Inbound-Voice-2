# dialer.py — Campaign dialer engine
import os, json, asyncio, logging, random
from datetime import datetime

logger = logging.getLogger('dialer')

async def run_dialer_for_campaign(campaign_id: int, broadcast_fn=None):
    """
    Core dialer loop for a campaign.
    Reads leads from DB, calls each one, updates status in real-time.
    broadcast_fn is the WebSocket broadcast coroutine from ui_server.
    """
    import db

    campaign = db.get_campaign_full(campaign_id)
    if not campaign:
        logger.error(f'[DIALER] Campaign {campaign_id} not found')
        return

    max_concurrent = int(campaign.get('max_concurrent_calls', 3))
    calls_per_min  = int(campaign.get('calls_per_minute', 5))
    retry_failed   = bool(campaign.get('retry_failed', True))
    max_retries    = int(campaign.get('max_retries', 2))
    sip_trunk_id   = campaign.get('sip_trunk_id') or os.environ.get('SIP_TRUNK_ID', '')

    # Number pool / masking
    base_number = os.environ.get('VOBIZ_OUTBOUND_NUMBER', '').strip()
    pool_str    = os.environ.get('VOBIZ_NUMBER_POOL', '').strip()
    pool        = [n.strip() for n in pool_str.split(',') if n.strip()] if pool_str else []

    # LiveKit credentials
    lk_url    = os.environ.get('LIVEKIT_URL', '')
    lk_key    = os.environ.get('LIVEKIT_API_KEY', '')
    lk_secret = os.environ.get('LIVEKIT_API_SECRET', '')

    # Agent ID for campaign
    agent_id = campaign.get('agent_id')
    agent_cfg = {}
    if agent_id:
        agents = db.get_all_agents()
        match  = [a for a in agents if str(a.get('id')) == str(agent_id)]
        if match:
            agent_cfg = match[0]

    async def dispatch_call(lead: dict) -> bool:
        phone = lead.get('phone', '').strip()
        name  = lead.get('name', '') or ''

        if not phone:
            logger.warning(f'[DIALER] Lead {lead.get("id")} has no phone — skipping')
            return False

        # Pick mask number
        from_number = random.choice(pool) if pool else base_number

        try:
            from livekit import api as lk_api
            room = f'bulk-{phone.replace("+","")}-{random.randint(1000,9999)}'
            metadata = json.dumps({
                'phone_number': phone,
                'sip_trunk_id': sip_trunk_id,
                'name': name,
                'campaign_id': campaign_id,
                'lead_id': lead['id'],
                **(({'from_number': from_number}) if from_number else {}),
            })

            async with lk_api.LiveKitAPI(lk_url, lk_key, lk_secret) as lk:
                await lk.agent_dispatch.create_dispatch(
                    lk_api.CreateAgentDispatchRequest(
                        agent_name='outbound-caller',
                        room=room,
                        metadata=metadata,
                    )
                )

            db.update_lead_status(lead['id'], 'called')
            logger.info(f'[DIALER] Dispatched call to {phone} (room={room})')

            if broadcast_fn:
                await broadcast_fn({
                    'type': 'campaign_progress',
                    'campaign_id': campaign_id,
                    'lead_id': lead['id'],
                    'phone': phone,
                    'status': 'called',
                    'timestamp': datetime.utcnow().isoformat(),
                })
            return True

        except Exception as e:
            logger.error(f'[DIALER] Failed to dispatch {phone}: {e}')
            db.update_lead_status(lead['id'], 'failed')
            if broadcast_fn:
                try:
                    await broadcast_fn({
                        'type': 'campaign_progress',
                        'campaign_id': campaign_id,
                        'lead_id': lead['id'],
                        'phone': phone,
                        'status': 'failed',
                        'error': str(e),
                        'timestamp': datetime.utcnow().isoformat(),
                    })
                except Exception:
                    pass
            return False

    # Main dispatch loop
    leads = db.get_leads(campaign_id, status='pending')
    if retry_failed:
        failed = db.get_leads(campaign_id, status='failed')
        leads += [l for l in failed if int(l.get('retry_count', 0)) < max_retries]

    total   = len(leads)
    done    = 0
    delay_s = 60.0 / max(calls_per_min, 1)

    logger.info(f'[DIALER] Campaign {campaign_id}: {total} leads, {calls_per_min}/min, max_concurrent={max_concurrent}')

    semaphore = asyncio.Semaphore(max_concurrent)

    async def dispatch_with_sem(lead):
        nonlocal done
        async with semaphore:
            # Re-check campaign is still active before each call
            fresh = db.get_campaign_full(campaign_id)
            if fresh and fresh.get('status') not in ('active', 'running'):
                logger.info(f'[DIALER] Campaign {campaign_id} paused/stopped — halting')
                return
            await dispatch_call(lead)
            done += 1
            logger.info(f'[DIALER] Campaign {campaign_id}: {done}/{total} leads dispatched')
            await asyncio.sleep(delay_s)

    tasks = [asyncio.create_task(dispatch_with_sem(lead)) for lead in leads]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    db.update_campaign_status(campaign_id, 'completed')
    logger.info(f'[DIALER] Campaign {campaign_id} completed. {done}/{total} calls dispatched.')

    if broadcast_fn:
        try:
            await broadcast_fn({
                'type': 'campaign_complete',
                'campaign_id': campaign_id,
                'total': total,
                'dispatched': done,
                'timestamp': datetime.utcnow().isoformat(),
            })
        except Exception:
            pass
