from pydantic import BaseModel


class SpatialFeaturePayload(BaseModel):
    layer_id: str = ""
    layer_name: str = "Drawn Shapes"
    geojson: dict[str, object]
    plot_id: str = ""
    owner_name: str = ""
    structure_type: str = "Unassigned"
    source_type: str = "drawn"


class SpatialFeaturePatchPayload(BaseModel):
    geojson: dict[str, object] | None = None
    plot_id: str | None = None
    owner_name: str | None = None
    structure_type: str | None = None
