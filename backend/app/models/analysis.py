from pydantic import BaseModel


class ProfilePayload(BaseModel):
    dataset_id: str
    points: list[list[float]]
    samples: int = 120
    corridor_width_m: float = 1.0


class VolumePayload(BaseModel):
    dataset_id: str
    points: list[list[float]] = []
    circle_center: list[float] = []
    circle_radius_m: float = 0.0
    base_elevation: float | None = None


class CrossSectionPayload(BaseModel):
    project_id: str = ""
    dataset_id: str = ""
    dtm_file_path: str = ""
    line: dict[str, object] | None = None
    coordinates: list[list[float]] = []
    samples: int = 180


class CompareVolumePayload(BaseModel):
    dataset_ids: list[str] = []
