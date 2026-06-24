from pydantic import BaseModel


class PointCloudProcessPayload(BaseModel):
    filename: str
    project_id: str = "default-project"


class PointCloudSliceBoxPayload(BaseModel):
    center: list[float]
    rotation: list[float] = []
    dimensions: list[float]


class PointCloudSliceExportPayload(BaseModel):
    name: str = "slice-export"
    box: PointCloudSliceBoxPayload
    source_asset: str = ""
    viewer_type: str = ""
