"""LiveKit Agent shell. All booking logic lives in the LangGraph graph
(wired via LangGraphLLM). This shell handles caller-ID recognition for
returning patient pre-fill on voice calls.
"""

from __future__ import annotations
import logging
import re

from livekit.agents import Agent, get_job_context

log = logging.getLogger(__name__)


def _normalize_mobile(raw: str) -> str:
    """Strip non-digits; return the BD local format (0XXXXXXXXXX, 11 digits).

    A "+880…"/international caller-ID already has 11+ digits after stripping,
    so keeping the last 11 yields "01XXXXXXXXX" correctly. But some SIP
    trunks deliver caller-ID with neither a country code nor the leading
    zero (bare 10 digits) — without prepending "0" the lookup below would
    never match the "0XXXXXXXXXX" format stored everywhere else in this app
    (agent prompts, portal signup), silently disabling caller-ID recognition.
    """
    digits = re.sub(r"\D", "", raw)
    digits = digits[-11:] if len(digits) >= 11 else digits
    if len(digits) == 10:
        digits = "0" + digits
    return digits


class DoctorAssistant(Agent):
    def __init__(
        self,
        hospital_id: int | None = None,
        langgraph_llm=None,
    ):
        super().__init__(
            instructions=(
                "You are a Bangla-speaking doctor's appointment-setting assistant."
            )
        )
        self._hospital_id = hospital_id
        self._langgraph_llm = langgraph_llm  # LangGraphLLM reference for patient injection

    async def on_enter(self) -> None:
        """Try to recognise a returning patient by SIP caller-ID."""
        if not self._hospital_id or not self._langgraph_llm:
            return
        try:
            from tools.database import get_patient_by_phone
            room = get_job_context().room
            participants = list(room.remote_participants.values()) if room else []
            for p in participants:
                raw_phone = (p.attributes or {}).get("sip.phoneNumber", "")
                if not raw_phone:
                    continue
                mobile = _normalize_mobile(raw_phone)
                if len(mobile) < 10:
                    continue
                patient = await get_patient_by_phone(self._hospital_id, mobile)
                if patient:
                    log.info(
                        "Recognised returning patient %s (MRN %s) from caller ID %s",
                        patient["name"], patient.get("mrn"), mobile,
                    )
                    # Inject patient identity into LangGraphLLM so the next turn's
                    # _turn_input pre-fills patient_name and patient_age in state.
                    self._langgraph_llm.known_patient = patient
                break
        except Exception:
            log.debug("Caller-ID patient lookup failed (non-fatal)", exc_info=True)
