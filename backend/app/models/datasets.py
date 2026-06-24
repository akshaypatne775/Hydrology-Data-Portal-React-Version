from pydantic import BaseModel


class ProcessDatasetOut(BaseModel):
    status: str
    message: str
    project_id: str
    dataset_id: str
    dataset_name: str
    cog_path: str
    cog_tile_url_template: str


class CompleteUploadPayload(BaseModel):
    filename: str
    totalChunks: int
    project_id: str = "default-project"


class CompleteDatasetUploadPayload(BaseModel):
    filename: str
    totalChunks: int
    project_id: str = "default-project"
    dataset_type: str = ""
    month: str = ""
    created_at: str = ""
    epsg: str = ""


class DatasetMetaPayload(BaseModel):
    dataset_id: str
    month: str = ""
    dataset_type: str = ""


class DatasetOwnerPathMetaPayload(BaseModel):
    height_offset: float | None = None


class CropMaskPayload(BaseModel):
    points: list[list[float]]


class ContourGeneratePayload(BaseModel):
    dataset_id: str = ""
    source_tif: str = ""
    interval: float = 5.0


class FileDeletePayload(BaseModel):
    rel_path: str
