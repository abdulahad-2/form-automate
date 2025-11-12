from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel
from models.upload import UploadStatus

class UploadBase(BaseModel):
    filename: str
    original_filename: str
    file_size: int
    file_type: str

class UploadResponse(UploadBase):
    id: str
    s3_key: Optional[str]
    status: UploadStatus
    processed_data: Optional[Dict[str, Any]]
    validation_errors: Optional[Dict[str, Any]]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class UploadPreviewRequest(BaseModel):
    upload_id: str
    limit: int = 10

class UploadPreviewResponse(BaseModel):
    upload_id: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    validation_errors: Optional[Dict[str, Any]]
    sample_data: List[Dict[str, Any]]
    detected_columns: List[str]