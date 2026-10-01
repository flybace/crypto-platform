"""ASGI application entry point for the standalone read-only runtime."""

from .http_api import create_app

app = create_app()
