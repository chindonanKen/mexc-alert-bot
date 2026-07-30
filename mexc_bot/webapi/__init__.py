"""V2 Desk — FastAPI web UI for the trading assistant platform."""

__all__ = ["create_app"]


def create_app():
    from .app import create_app as _create

    return _create()
