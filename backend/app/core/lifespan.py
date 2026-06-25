from contextlib import asynccontextmanager
from fastapi import FastAPI
import logging

from app.core.database import ensure_tables

# We import these inside the lifespan to avoid circular dependencies
def _run_backfill():
    try:
        from app.main import _backfill_processed_sizes
        _backfill_processed_sizes()
    except Exception as e:
        logging.error(f"Failed to backfill processed sizes: {e}")

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    # Startup
    ensure_tables()
    _run_backfill()
    yield
    # Shutdown
    pass
