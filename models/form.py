from sqlalchemy import Column, String, Text, JSON, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from enum import Enum
from .base import BaseModel

class FormStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"

class SubmissionStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    SPAM = "spam"

class Form(BaseModel):
    __tablename__ = "forms"

    name = Column(String(255), nullable=False, index=True)
    form_id = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    settings = Column(JSON, nullable=True, default={})
    status = Column(SQLEnum(FormStatus), default=FormStatus.ACTIVE, nullable=False)

    # Relationships
    submissions = relationship("FormSubmission", back_populates="form", cascade="all, delete-orphan")

class FormSubmission(BaseModel):
    __tablename__ = "form_submissions"

    form_id = Column(String, ForeignKey("forms.id"), nullable=False, index=True)
    email = Column(String(255), nullable=True, index=True)
    data = Column(JSON, nullable=False)
    status = Column(SQLEnum(SubmissionStatus), default=SubmissionStatus.PENDING, nullable=False)
    ip_address = Column(String(45), nullable=True)  # IPv6 compatible
    user_agent = Column(Text, nullable=True)

    # Relationships
    form = relationship("Form", back_populates="submissions")