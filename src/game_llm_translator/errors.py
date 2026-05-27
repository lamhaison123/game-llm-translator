from __future__ import annotations

STOPPED = "Stopped by user"


class StoppedByUser(RuntimeError):
    """Raised when stop_event has been set. Inherits from RuntimeError so legacy
    callers that match by `isinstance(exc, RuntimeError) and str(exc) == "Stopped by user"`
    continue to work; internal code should prefer `except StoppedByUser`."""

    def __init__(self, message: str = STOPPED) -> None:
        super().__init__(message)
