from sqlalchemy import Column, String, Text, JSON, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.orm import relationship
from enum import Enum
from .base import BaseModel

class UploadStatus(str, Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"

class Upload(BaseModel):
    __tablename__ = "uploads"

    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(50), nullable=False)  # csv, xlsx, xls
    s3_key = Column(String(500), nullable=True)  # S3 object key
    status = Column(SQLEnum(UploadStatus), default=UploadStatus.UPLOADING, nullable=False)
    processed_data = Column(JSON, nullable=True)  # Processed CSV/Excel data
    validation_errors = Column(JSON, nullable=True)  # Validation errors
    total_rows = Column(Integer, default=0, nullable=False)
    valid_rows = Column(Integer, default=0, nullable=False)
    invalid_rows = Column(Integer, default=0, nullable=False)

    # Relationships
    campaigns = relationship("Campaign", back_populates="upload")