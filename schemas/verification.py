from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr
from models.verification import VerificationStatus

class EmailVerificationBase(BaseModel):
    email: EmailStr

class EmailVerificationResponse(EmailVerificationBase):
    id: str
    status: VerificationStatus
    is_valid_syntax: bool
    has_mx_record: bool
    is_disposable: bool
    is_webmail: bool
    domain: str
    verified_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class BatchVerificationRequest(BaseModel):
    emails: list[EmailStr]

class BatchVerificationResponse(BaseModel):
    total: int
    verified: int
    valid: int
    invalid: int
    risky: int
    results: list[EmailVerificationResponse]