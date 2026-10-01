"""Delivery-facing views; transport wiring is kept outside the domain."""

from .http_api import create_app

__all__ = ["create_app"]
