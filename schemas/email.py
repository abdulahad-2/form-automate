from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, EmailStr
from models.email import CampaignStatus, EmailStatus

# Email template schemas
class EmailTemplateBase(BaseModel):
    name: str
    subject: str
    content: str
    description: Optional[str] = None
    variables: Optional[List[str]] = []

class EmailTemplateCreate(EmailTemplateBase):
    pass

class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None
    description: Optional[str] = None
    variables: Optional[List[str]] = None

class EmailTemplateResponse(EmailTemplateBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Campaign schemas
class CampaignBase(BaseModel):
    name: str
    template_id: str

class CampaignCreate(CampaignBase):
    upload_id: Optional[str] = None

class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[CampaignStatus] = None

class CampaignResponse(CampaignBase):
    id: str
    status: CampaignStatus
    total_emails: int
    sent_count: int
    error_count: int
    delivery_rate: float
    upload_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Email log schemas
class EmailLogBase(BaseModel):
    to_email: EmailStr
    subject: str

class EmailLogResponse(EmailLogBase):
    id: str
    campaign_id: str
    status: EmailStatus
    error_message: Optional[str]
    sent_at: Optional[str]
    delivered_at: Optional[str]
    external_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Template preview schema
class TemplatePreviewRequest(BaseModel):
    template_id: str
    sample_data: Dict[str, Any]

class TemplatePreviewResponse(BaseModel):
    subject: str
    content: str
    variables_used: List[str]