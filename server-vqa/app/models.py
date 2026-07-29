from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class GpsPayload(BaseModel):
    lat: float
    lon: float


class VqaRequest(BaseModel):
    frame_id: str = Field(..., min_length=1)
    gps: Optional[GpsPayload] = None
    prompt: str = ""


class VqaResponse(BaseModel):
    frame_id: str
    objects: List[str]
    scene: str
    vision_location: str
    description: str
    summary: str
    spatial_description: str
    risk_level: str
    risk_message: str
    suggested_action: str
    spoken_text: str
    ocr_text: str
    change_significance: str = "major"
    changes: str = ""
    requested_model: str = ""
    resolved_model: str = ""
    model_routing_reason: str = ""
    gps_location: Optional[Dict]
    latency_ms: Optional[float]
    timestamp: str
