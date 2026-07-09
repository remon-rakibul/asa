"""Guard against the LLM warmup silently breaking.

_warmup_llm imports from agent.nodes lazily inside a try/except, so a renamed
symbol doesn't fail loudly in prod — it just disables warmup (first request
pays the model cold-load). This test makes such a rename a CI failure instead.
"""

import inspect
import re

import agent.nodes
from api.app import _warmup_llm


def test_warmup_imports_resolve():
    src = inspect.getsource(_warmup_llm)
    imported = re.findall(r"from agent\.nodes import (\w+)", src)
    assert imported, "warmup no longer imports from agent.nodes — update this test"
    for name in imported:
        assert hasattr(agent.nodes, name), (
            f"api.app._warmup_llm imports agent.nodes.{name}, which no longer exists — "
            "warmup would silently fail at startup"
        )
