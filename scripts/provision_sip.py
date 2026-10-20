"""Create or update ASA's LiveKit SIP setup from .env — idempotent.

    python -m scripts.provision_sip --dry-run   # show what would change
    python -m scripts.provision_sip             # apply

Manages three LiveKit resources, found by name so re-running updates them in
place instead of duplicating:

  asa-inbound    inbound trunk: the DIDs patients dial (SIP_INBOUND_NUMBERS),
                 accepted from the provider's IPs and/or digest auth.
  asa-dispatch   individual dispatch rule: ONE room per caller (required for
                 concurrent calls), named <SIP_ROOM_PREFIX><caller>_<random>,
                 dispatching the voice agent (LIVEKIT_AGENT_NAME). Caller-ID is
                 kept visible — the premium gate matches on it.
  asa-outbound   outbound trunk (only if SIP_OUTBOUND_ADDRESS is set): used to
                 transfer calls to a hospital's reception and for outbound
                 reminder calls.

Which hospital a call belongs to is NOT configured here: the worker reads the
dialed number (sip.trunkPhoneNumber) and looks it up in `channels`
(main.py::_resolve_channel). Numbers with no mapping run the platform agent.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from config import settings

INBOUND, OUTBOUND, DISPATCH = "asa-inbound", "asa-outbound", "asa-dispatch"


def _csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def build_inbound(lk):
    if not settings.sip_inbound_numbers:
        raise SystemExit("SIP_INBOUND_NUMBERS is empty — nothing to provision.")
    if not (settings.sip_inbound_allowed_addresses or settings.sip_inbound_auth_username):
        raise SystemExit(
            "Refusing to create an OPEN inbound trunk: set SIP_INBOUND_ALLOWED_ADDRESSES "
            "(provider IPs) and/or SIP_INBOUND_AUTH_USERNAME/PASSWORD."
        )
    return lk.SIPInboundTrunkInfo(
        name=INBOUND,
        numbers=_csv(settings.sip_inbound_numbers),
        allowed_addresses=_csv(settings.sip_inbound_allowed_addresses),
        auth_username=settings.sip_inbound_auth_username,
        auth_password=settings.sip_inbound_auth_password,
        krisp_enabled=True,      # PSTN noise suppression on the caller's leg
    )


def build_outbound(lk):
    if not settings.sip_outbound_address:
        return None
    transport = {
        "udp": lk.SIPTransport.SIP_TRANSPORT_UDP,
        "tcp": lk.SIPTransport.SIP_TRANSPORT_TCP,
        "tls": lk.SIPTransport.SIP_TRANSPORT_TLS,
    }.get(settings.sip_outbound_transport.lower(), lk.SIPTransport.SIP_TRANSPORT_AUTO)
    return lk.SIPOutboundTrunkInfo(
        name=OUTBOUND,
        address=settings.sip_outbound_address,
        transport=transport,
        numbers=_csv(settings.sip_outbound_numbers) or _csv(settings.sip_inbound_numbers)[:1],
        auth_username=settings.sip_outbound_auth_username,
        auth_password=settings.sip_outbound_auth_password,
    )


def build_dispatch(lk, inbound_trunk_id: str):
    return lk.SIPDispatchRuleInfo(
        name=DISPATCH,
        trunk_ids=[inbound_trunk_id] if inbound_trunk_id else [],
        hide_phone_number=False,
        rule=lk.SIPDispatchRule(
            dispatch_rule_individual=lk.SIPDispatchRuleIndividual(room_prefix=settings.sip_room_prefix),
        ),
        room_config=lk.RoomConfiguration(
            agents=[lk.RoomAgentDispatch(agent_name=settings.livekit_agent_name)],
        ),
    )


def _redacted(info) -> str:
    from google.protobuf.json_format import MessageToDict

    d = MessageToDict(info, preserving_proto_field_name=True)
    for k in ("auth_password",):
        if d.get(k):
            d[k] = "***"
    return str(d)


async def provision(dry_run: bool) -> int:
    from livekit import api as lk

    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        raise SystemExit("Set LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET first.")

    inbound = build_inbound(lk)
    outbound = build_outbound(lk)
    client = lk.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
    try:
        sip = client.sip
        existing_in = {t.name: t for t in (await sip.list_inbound_trunk(lk.ListSIPInboundTrunkRequest())).items}
        existing_out = {t.name: t for t in (await sip.list_outbound_trunk(lk.ListSIPOutboundTrunkRequest())).items}
        existing_rules = {r.name: r for r in (await sip.list_dispatch_rule(lk.ListSIPDispatchRuleRequest())).items}

        def plan(kind: str, current, desired) -> str:
            action = "update" if current else "create"
            print(f"{action:>6} {kind}: {_redacted(desired)}")
            return action

        # Inbound trunk
        cur = existing_in.get(INBOUND)
        plan("inbound trunk", cur, inbound)
        inbound_id = cur.sip_trunk_id if cur else ""
        if not dry_run:
            if cur:
                await sip.update_inbound_trunk(cur.sip_trunk_id, inbound)
            else:
                inbound_id = (await sip.create_inbound_trunk(
                    lk.CreateSIPInboundTrunkRequest(trunk=inbound))).sip_trunk_id

        # Dispatch rule (needs the inbound trunk id)
        rule = build_dispatch(lk, inbound_id)
        cur_rule = existing_rules.get(DISPATCH)
        plan("dispatch rule", cur_rule, rule)
        if not dry_run:
            if cur_rule:
                await sip.update_dispatch_rule(cur_rule.sip_dispatch_rule_id, rule)
            else:
                await sip.create_dispatch_rule(lk.CreateSIPDispatchRuleRequest(
                    name=rule.name, trunk_ids=list(rule.trunk_ids), rule=rule.rule,
                    hide_phone_number=False, room_config=rule.room_config,
                ))

        # Outbound trunk (optional)
        if outbound is not None:
            cur_out = existing_out.get(OUTBOUND)
            plan("outbound trunk", cur_out, outbound)
            if not dry_run:
                if cur_out:
                    await sip.update_outbound_trunk(cur_out.sip_trunk_id, outbound)
                else:
                    await sip.create_outbound_trunk(lk.CreateSIPOutboundTrunkRequest(trunk=outbound))
        else:
            print("  skip outbound trunk: SIP_OUTBOUND_ADDRESS unset (no transfers / outbound calls)")
    finally:
        await client.aclose()

    print("dry run — nothing changed." if dry_run else "done. Check with: make verify")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the plan without changing LiveKit")
    sys.exit(asyncio.run(provision(ap.parse_args().dry_run)))
