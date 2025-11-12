from sqlalchemy import Column, String, Text, JSON, ForeignKey, Integer, Enum as SQLEnum, Float
from sqlalchemy.orm import relationship
from enum import Enum
from .base import BaseModel

class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SENDING = "sending"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class EmailStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    FAILED = "failed"

class EmailTemplate(BaseModel):
    __tablename__ = "email_templates"

    name = Column(String(255), nullable=False, index=True)
    subject = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    variables = Column(JSON, nullable=True, default=[])
    description = Column(Text, nullable=True)

    # Relationships
    campaigns = relationship("Campaign", back_populates="template")

class Campaign(BaseModel):
    __tablename__ = "campaigns"

    name = Column(String(255), nullable=False, index=True)
    template_id = Column(String, ForeignKey("email_templates.id"), nullable=False)
    status = Column(SQLEnum(CampaignStatus), default=CampaignStatus.DRAFT, nullable=False)
    total_emails = Column(Integer, default=0, nullable=False)
    sent_count = Column(Integer, default=0, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
    delivery_rate = Column(Float, default=0.0, nullable=False)
    upload_id = Column(String, ForeignKey("uploads.id"), nullable=True)

    # Relationships
    template = relationship("EmailTemplate", back_populates="campaigns")
    email_logs = relationship("EmailLog", back_populates="campaign", cascade="all, delete-orphan")
    upload = relationship("Upload", back_populates="campaigns")

class EmailLog(BaseModel):
    __tablename__ = "email_logs"

    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False, index=True)
    to_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(500), nullable=False)
    status = Column(SQLEnum(EmailStatus), default=EmailStatus.PENDING, nullable=False)
    error_message = Column(Text, nullable=True)
    sent_at = Column(String(255), nullable=True)  # Storing as string to handle different timestamp formats
    delivered_at = Column(String(255), nullable=True)
    external_id = Column(String(255), nullable=True, index=True)  # External email service ID

    # Relationships
    campaign = relationship("Campaign", back_populates="email_logs")