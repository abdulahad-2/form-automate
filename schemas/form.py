from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, EmailStr
from models.form import FormStatus, SubmissionStatus

# Form schemas
class FormBase(BaseModel):
    name: str
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = {}

class FormCreate(FormBase):
    form_id: str

class FormUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    status: Optional[FormStatus] = None

class FormResponse(FormBase):
    id: str
    form_id: str
    status: FormStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Form submission schemas
class FormSubmissionBase(BaseModel):
    email: Optional[EmailStr] = None
    data: Dict[str, Any]

class FormSubmissionCreate(FormSubmissionBase):
    form_id: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

class FormSubmissionResponse(FormSubmissionBase):
    id: str
    form_id: str
    status: SubmissionStatus
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Enhanced form submission for external API
class ExternalFormSubmission(BaseModel):
    """Schema for external form submissions (existing /submit-form endpoint)"""
    form_id: Optional[str] = None
    data: Dict[str, Any]

    class Config:
        extra = "allow"  # Allow any additional fields