"""LAN listener exposes only authenticated debug APIs, not legacy model/admin APIs."""
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.device_debug_api import router
from app.device_debug_page import router as page_router
from app.device_debug_pairing import router as pairing_router
from app.device_debug_discovery import DeviceDebugAdvertiser

@asynccontextmanager
async def lifespan(app):
    advertiser = DeviceDebugAdvertiser()
    await asyncio.to_thread(advertiser.start, int(os.environ.get('VQASEE_DEVICE_DEBUG_PORT','9001')))
    try: yield
    finally: await asyncio.to_thread(advertiser.stop)

app = FastAPI(title='VQASee Device Debug', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(router)
app.include_router(page_router)
app.include_router(pairing_router)

@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'device-debug'}
