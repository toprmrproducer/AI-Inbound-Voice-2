"""
sip_manager.py
LiveKit SIP trunk CRUD + call dispatch helper.
"""
import os
import logging
import asyncio
from livekit import api
from livekit.protocol.sip import (
    CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo,
    CreateSIPInboundTrunkRequest,  SIPInboundTrunkInfo,
    ListSIPOutboundTrunkRequest,   ListSIPInboundTrunkRequest,
    DeleteSIPOutboundTrunkRequest, DeleteSIPInboundTrunkRequest,
)

logger = logging.getLogger("sip-manager")


def _lk() -> api.LiveKitAPI:
    return api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )


# ─── OUTBOUND ────────────────────────────────────────────────

async def create_outbound_trunk(name: str, address: str, numbers: list,
                                username: str, password: str) -> str:
    lkapi = _lk()
    try:
        trunk = SIPOutboundTrunkInfo(
            name=name, address=address, numbers=numbers,
            auth_username=username, auth_password=password,
        )
        result = await lkapi.sip.create_outbound_trunk(
            CreateSIPOutboundTrunkRequest(trunk=trunk)
        )
        logger.info(f"SIP outbound trunk created: {result.sip_trunk_id}")
        return result.sip_trunk_id
    finally:
        await lkapi.aclose()


async def list_outbound_trunks() -> list:
    lkapi = _lk()
    try:
        result = await lkapi.sip.list_outbound_trunk(ListSIPOutboundTrunkRequest())
        return [
            {"id": t.sip_trunk_id, "name": t.name,
             "address": t.address, "numbers": list(t.numbers)}
            for t in result.items
        ]
    finally:
        await lkapi.aclose()


async def delete_outbound_trunk(trunk_id: str):
    lkapi = _lk()
    try:
        await lkapi.sip.delete_outbound_trunk(
            DeleteSIPOutboundTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"SIP outbound trunk deleted: {trunk_id}")
    finally:
        await lkapi.aclose()


# ─── INBOUND ─────────────────────────────────────────────────

async def create_inbound_trunk(name: str, numbers: list,
                               allowed_addresses: list = None) -> str:
    lkapi = _lk()
    try:
        trunk = SIPInboundTrunkInfo(
            name=name, numbers=numbers,
            allowed_addresses=allowed_addresses or [],
        )
        result = await lkapi.sip.create_inbound_trunk(
            CreateSIPInboundTrunkRequest(trunk=trunk)
        )
        logger.info(f"SIP inbound trunk created: {result.sip_trunk_id}")
        return result.sip_trunk_id
    finally:
        await lkapi.aclose()


async def list_inbound_trunks() -> list:
    lkapi = _lk()
    try:
        result = await lkapi.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
        return [
            {"id": t.sip_trunk_id, "name": t.name, "numbers": list(t.numbers)}
            for t in result.items
        ]
    finally:
        await lkapi.aclose()


async def delete_inbound_trunk(trunk_id: str):
    lkapi = _lk()
    try:
        await lkapi.sip.delete_inbound_trunk(
            DeleteSIPInboundTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"SIP inbound trunk deleted: {trunk_id}")
    finally:
        await lkapi.aclose()


# ─── DISPATCH ────────────────────────────────────────────────

async def dispatch_outbound_call(phone: str, room_name: str,
                                 trunk_id: str, cli: str = None,
                                 participant_name: str = "Caller") -> dict:
    """
    Dials phone number into room_name using trunk_id.
    Returns {participant_id, room}.
    """
    lkapi = _lk()
    try:
        req = api.CreateSIPParticipantRequest(
            room_name=room_name,
            sip_trunk_id=trunk_id,
            sip_call_to=phone,
            participant_identity=f"sip-{phone.replace('+', '').replace(' ', '')}",
            participant_name=participant_name,
            wait_until_answered=True,
        )
        if cli:
            req.sip_number = cli
        result = await lkapi.sip.create_sip_participant(req)
        return {"participant_id": result.participant_identity, "room": room_name}
    finally:
        await lkapi.aclose()
