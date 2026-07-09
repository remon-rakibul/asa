"""Routing for the appointment ReAct graph."""

from langchain_core.messages import ToolMessage
from langgraph.graph import END

from .state import AppointmentState


def should_continue(state: AppointmentState) -> str:
    """Route to the tools node if the LLM made tool calls; otherwise end the turn."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


def route_after_tools(state: AppointmentState) -> str:
    """After tools run: deterministic farewell only when a booking JUST succeeded.

    Keyed on the BOOKED tool result, not on appointment_id in state — a thread
    keeps its appointment_id after booking, and later tool calls in the same
    session (list/cancel/reschedule, RAG) must not re-trigger the farewell.
    """
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and str(last.content).startswith("BOOKED:"):
        return "post_booking"
    return "call_model"
