from sqlalchemy import Column, String, Boolean, DateTime, Index
from enum import Enum
from .base import BaseModel

class VerificationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"

class EmailVerification(BaseModel):
    __tablename__ = "email_verifications"

    email = Column(String(255), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default=VerificationStatus.UNKNOWN)
    is_valid_syntax = Column(Boolean, default=False)
    has_mx_record = Column(Boolean, default=False)
    is_disposable = Column(Boolean, default=False)
    is_webmail = Column(Boolean, default=False)
    domain = Column(String(255), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # Add indexes for common queries
    __table_args__ = (
        Index('idx_verification_status', 'status'),
        Index('idx_verification_domain', 'domain'),
    )