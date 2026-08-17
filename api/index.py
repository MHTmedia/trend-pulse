"""
Vercel serverless entrypoint.

Vercel's Python runtime picks up the module-level `app` object and serves it
as a WSGI application. All routes are rewritten here by vercel.json.
"""

import sys
from pathlib import Path

# app.py lives at the project root, one level up from api/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402,F401
