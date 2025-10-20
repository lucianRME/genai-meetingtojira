# agents/session_logger.py
import functools
from app.session_manager import _log_action, _append_rolling_summary, _db, _now

def session_step(action_name: str, summary_line: str | None = None):
    """Decorator to log agent steps and update session summary."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # detect session_id (args or kwargs)
            sid = kwargs.get("session_id") or (args[0] if args else None)
            if not sid:
                return func(*args, **kwargs)  # fallback: no session context

            # log start
            _log_action(sid, f"{action_name}_start", actor="agentic")

            # execute
            result = func(*args, **kwargs)

            # log end
            _log_action(sid, f"{action_name}_done", actor="agentic")
            if summary_line:
                _append_rolling_summary(sid, summary_line)

            return result
        return wrapper
    return decorator