# agents/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

# Try to import session helpers; fall back to no-ops in CI or minimal runs
try:
    from app.session_manager import (
        log_action as _sm_log_action,
        update_summary as _sm_update_summary,
        get_summary as _sm_get_summary,
        set_state as _sm_set_state,
        get_state as _sm_get_state,
    )
except Exception:
    def _sm_log_action(session_id: str, action_type: str, payload: Dict[str, Any] | None = None):
        return None
    def _sm_update_summary(session_id: str, bullet: str):
        return None
    def _sm_get_summary(session_id: str) -> str:
        return ""
    def _sm_set_state(session_id: str, key: str, value: Any):
        return None
    def _sm_get_state(session_id: str, key: str, default: Any = None) -> Any:
        return default


class Agent(ABC):
    """
    Base class for all agents in the Synapse pipeline.

    Adds session-aware convenience methods so concrete agents can:
      - log actions to memory_action
      - append compact bullets to the rolling summary
      - fetch/set small session-scoped state (e.g., project_id, last paths)
      - build prompts that include the compact rolling summary
    """
    name: str = "agent"

    # ---------- abstract API ----------
    @abstractmethod
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def on_error(self, err: Exception, state: Dict[str, Any]) -> Dict[str, Any]:
        raise err

    # ---------- session helpers ----------
    @staticmethod
    def _sid(state: Dict[str, Any]) -> Optional[str]:
        return state.get("session_id")

    def log(self, state: Dict[str, Any], action: str, **payload) -> None:
        """Write an action row for this session (no-op if no session_id)."""
        sid = self._sid(state)
        if not sid:
            return
        _sm_log_action(sid, action, payload or None)

    def append_summary(self, state: Dict[str, Any], bullet: str) -> None:
        """Prepend a compact bullet to the rolling_session summary."""
        sid = self._sid(state)
        if not sid or not bullet:
            return
        _sm_update_summary(sid, bullet)

    def get_summary(self, state: Dict[str, Any]) -> str:
        """Return the compact rolling summary for this session ('' if none)."""
        sid = self._sid(state)
        return _sm_get_summary(sid) if sid else ""

    # ---------- small session key/value ----------
    def get_kv(self, state: Dict[str, Any], key: str, default: Any = None) -> Any:
        sid = self._sid(state)
        if not sid:
            return state.get(key, default)
        return _sm_get_state(sid, key, default)

    def set_kv(self, state: Dict[str, Any], key: str, value: Any) -> None:
        sid = self._sid(state)
        if sid:
            _sm_set_state(sid, key, value)
        # mirror into in-memory state so downstream steps see it immediately
        state[key] = value

    # ---------- prompt builder ----------
    def build_prompt(self, state: Dict[str, Any], base_prompt: str) -> str:
        """
        Prepend the compact rolling summary to an LLM prompt so each agent
        inherits prior context without re-sending long transcripts.
        """
        compact = self.get_summary(state)
        if not compact:
            return base_prompt
        return (
            "You are continuing an ongoing session. Use the compact context below.\n"
            "Context (bulleted, newest first):\n"
            f"{compact}\n\n"
            "Task:\n"
            f"{base_prompt}"
        )
