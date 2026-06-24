from pydantic import BaseModel


class AdminManualBulkImportTask(BaseModel):
    source_folder: str
    kind: str  # las | ortho | dtm | dsm


class AdminManualBulkImportPayload(BaseModel):
    project_id: str = ""
    tasks: list[AdminManualBulkImportTask]
    max_parallel: int = 2


class AdminLocateFolderPayload(BaseModel):
    initial_path: str = ""
    kind: str = ""
    mode: str = "folder"


class AdminBulkDeleteItem(BaseModel):
    dataset_id: str = ""
    file_name: str = ""
    rel_path: str = ""


class AdminBulkDeletePayload(BaseModel):
    items: list[AdminBulkDeleteItem]


class AdminProjectPatchPayload(BaseModel):
    name: str | None = None
    location: str | None = None
    date: str | None = None
    status: str | None = None
    type: str | None = None


class AdminDatasetMetaPayload(BaseModel):
    dataset_id: str
    name: str | None = None
    date: str | None = None
    status: str | None = None
    dataset_type: str | None = None
    month: str | None = None
    height_offset: float | None = None


class AdminDatasetPathMetaPayload(BaseModel):
    name: str | None = None
    date: str | None = None
    status: str | None = None
    dataset_type: str | None = None
    month: str | None = None
    height_offset: float | None = None


class AdminDatasetRenamePayload(BaseModel):
    name: str


class AdminUserApprovalPayload(BaseModel):
    role: str = "user"


class AdminUserRolePayload(BaseModel):
    role: str


class AdminUserPasswordResetPayload(BaseModel):
    password: str


class AdminUserUploadAccessPayload(BaseModel):
    enabled: bool = False


class AdminUserLocationRequiredPayload(BaseModel):
    enabled: bool = True


class AdminUserHiddenTabsPayload(BaseModel):
    hidden_tabs: list[str] = []
