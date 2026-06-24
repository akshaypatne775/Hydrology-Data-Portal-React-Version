from pydantic import BaseModel


class ClientErrorLogPayload(BaseModel):
    area: str = "frontend"
    message: str
    url: str = ""
    stack: str = ""
    project_id: str = ""
    dataset_id: str = ""
    extra: dict[str, object] | None = None
