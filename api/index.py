"""Vercel serverless entrypoint.

Kept to a single import on purpose. All routing, static serving and error
handling live in ``app.main``; this file exists only because Vercel needs a
callable named ``app`` at a predictable path.

The legacy ``builds`` block in vercel.json is the reason the sibling project
failed: it bundled this file alone, so `from app.main import app` raised
ModuleNotFoundError at import time. `includeFiles` in the build config ships
the whole ``app/`` package with it.
"""

from app.main import app

__all__ = ["app"]
