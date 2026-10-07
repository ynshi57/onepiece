"""Public UI shell. All device evidence remains behind the paired API."""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

@router.get('/device-debug/ui', include_in_schema=False)
def device_debug_page():
    return FileResponse(Path(__file__).with_name('device_debug.html'), media_type='text/html',
                        headers={'Cache-Control': 'no-store'})
