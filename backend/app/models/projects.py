from pydantic import BaseModel


class ProjectCreatePayload(BaseModel):
    name: str
    location: str
    date: str
    status: str
    type: str


class ProjectUpdatePayload(BaseModel):
    name: str = ""


class ProjectOut(BaseModel):
    id: str
    name: str
    location: str
    date: str
    status: str
    type: str


class CameraViewPayload(BaseModel):
    name: str
    lat: float
    lng: float
    height: float
    heading: float
    pitch: float
    roll: float = 0.0
