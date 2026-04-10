"""Hermes memory plugin wrapper for Keep.

This module is the Hermes-side plugin shim. It lives in the keep-skill
package so that users can install the keep memory provider without
waiting for an upstream Hermes PR to merge.

Install into Hermes:
    keep-skill install-hermes-plugin   (copies to plugins/memory/keep/)

Or manually:
    cp -r $(python -c "import keep.hermes.plugin; print(keep.hermes.plugin.__path__[0])") \
        ~/.hermes/plugins/memory/keep

The wrapper inherits from the Hermes MemoryProvider ABC and delegates
all calls to the keep-side KeepMemoryProvider (duck-typed, no Hermes
imports on the keep side).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_KEEP_FLOW_CONTROL_KEYS = {"state", "params", "token_budget", "cursor", "state_doc_yaml", "budget"}
_KEEP_FLOW_PARAM_KEYS = {
    "id", "item_id", "prefix", "include_hidden", "query", "tags",
    "scope", "since", "until", "limit", "similar_limit", "parts_limit",
    "meta_limit", "edges_limit", "content", "uri",
}


def _coerce_params(value: Any) -> Dict[str, Any]:
    """Normalize ``keep_flow.params`` — accept JSON string or dict."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("keep_flow params must be a JSON object when passed as a string")
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("keep_flow params must decode to a JSON object")
    return {}


def _normalize_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Hoist known flat params into the nested ``params`` dict for keep_flow."""
    if not isinstance(args, dict):
        return {}
    normalized = dict(args)
    if tool_name != "keep_flow":
        return normalized
    params = _coerce_params(normalized.get("params"))
    for key, value in args.items():
        if key in _KEEP_FLOW_PARAM_KEYS and key not in _KEEP_FLOW_CONTROL_KEYS:
            params.setdefault(key, value)
    if params:
        normalized["params"] = params
    return normalized


def _load_impl():
    """Lazy-load the keep-side provider. Returns instance or None."""
    try:
        from keep.hermes import KeepMemoryProvider as _Impl
        return _Impl()
    except ImportError:
        return None


class KeepMemoryProvider(MemoryProvider):
    """Typed delegate that forwards every call to the keep-side provider."""

    def __init__(self):
        self._impl = _load_impl()

    def _ensure_impl(self):
        if self._impl is None:
            self._impl = _load_impl()
        return self._impl is not None

    @property
    def name(self) -> str:
        return "keep"

    def is_available(self) -> bool:
        if not self._ensure_impl():
            return False
        return self._impl.is_available()

    def get_config_schema(self) -> List[Dict[str, Any]]:
        if not self._ensure_impl():
            # Return a placeholder so the setup wizard shows "local"
            # instead of "no setup needed". keep-skill will be installed
            # by _install_dependencies before save_config runs.
            return [{"key": "_pending_install", "description": "Embedding provider", "choices": ["(install keep-skill to configure)"]}]
        return self._impl.get_config_schema()

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        if not self._ensure_impl():
            raise ValueError(
                "keep-skill is not installed. "
                "Run `pip install keep-skill` and rerun `hermes memory setup`."
            )
        self._impl.save_config(values, hermes_home)

    def initialize(self, session_id: str, **kwargs) -> None:
        agent_context = kwargs.get("agent_context", "")
        platform = kwargs.get("platform", "")
        if agent_context in ("cron", "flush") or platform == "cron":
            return
        if not self._ensure_impl():
            logger.warning("keep: impl not available, skipping initialize")
            return
        self._impl.initialize(session_id, **kwargs)

    def shutdown(self) -> None:
        if self._impl is not None:
            self._impl.shutdown()

    def system_prompt_block(self) -> str:
        if self._impl is None:
            return ""
        return self._impl.system_prompt_block()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._impl is None:
            return ""
        return self._impl.prefetch(query, session_id=session_id)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._impl is not None:
            self._impl.queue_prefetch(query, session_id=session_id)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self._impl is not None:
            self._impl.sync_turn(user_content, assistant_content, session_id=session_id)

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        if self._impl is not None:
            self._impl.on_turn_start(turn_number, message, **kwargs)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._impl is not None:
            self._impl.on_session_end(messages)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if self._impl is not None:
            self._impl.on_memory_write(action, target, content)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if self._impl is None:
            return ""
        return self._impl.on_pre_compress(messages)

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        if self._impl is not None:
            self._impl.on_delegation(task, result, child_session_id=child_session_id, **kwargs)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if self._impl is None:
            try:
                from keep.hermes.const import FLOW_SCHEMA, HELP_SCHEMA, PROMPT_SCHEMA
                return [FLOW_SCHEMA, HELP_SCHEMA, PROMPT_SCHEMA]
            except ImportError:
                return []
        return self._impl.get_tool_schemas()

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self._impl is None:
            return tool_error("keep-skill is not installed")
        try:
            normalized_args = _normalize_tool_args(tool_name, args)
        except ValueError as e:
            return tool_error(str(e))
        return self._impl.handle_tool_call(tool_name, normalized_args, **kwargs)


def register(ctx) -> None:
    """Plugin entry point — called by Hermes memory plugin discovery."""
    ctx.register_memory_provider(KeepMemoryProvider())
