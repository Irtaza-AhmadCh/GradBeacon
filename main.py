"""Compatibility ASGI entry point for hosts configured with ``main:app``."""

from app.main import app

__all__ = ["app"]
