from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

from database import get_db, get_redis
from models.email import Campaign, EmailLog, CampaignStatus, EmailStatus
from models.upload import Upload
from schemas.email import CampaignCreate, CampaignUpdate, CampaignResponse, EmailLogResponse
from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service
from services.email_service import email_service, EmailType
from services.template_service import template_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
security = HTTPBearer()
limiter = Limiter(key_func=get_remote_address)

# Dependency to check admin authentication
async def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    token_result = await auth_service.verify_token(token)
    if not token_result['valid'] or token_result['type'] != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return token_result

@router.post("/", response_model=CampaignResponse)
async def create_campaign(
    campaign_data: CampaignCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Create a new email campaign and start sending immediately"""
    try:
        # Validate template exists
        from models.email import EmailTemplate
        template = await db.get(EmailTemplate, campaign_data.template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email template not found"
            )

        # Validate upload if provided
        upload_data = None
        if campaign_data.upload_id:
            upload = await db.get(Upload, campaign_data.upload_id)
            if not upload:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Upload not found"
                )
            upload_data = upload.processed_data

            if not upload_data or 'rows' not in upload_data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Upload data not processed yet"
                )

            # Count valid emails
            valid_rows = [row for row in upload_data['rows'] if row.get('valid', True)]
            total_emails = len(valid_rows)

            if total_emails == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No valid emails found in upload data"
                )
        else:
            total_emails = 0

        # Create campaign
        db_campaign = Campaign(
            name=campaign_data.name,
            template_id=campaign_data.template_id,
            upload_id=campaign_data.upload_id,
            status=CampaignStatus.SENDING if total_emails > 0 else CampaignStatus.DRAFT,
            total_emails=total_emails,
            sent_count=0,
            error_count=0,
            delivery_rate=0.0
        )

        db.add(db_campaign)
        await db.commit()
        await db.refresh(db_campaign)

        logger.info(f"Created new campaign: {db_campaign.name}")

        # Start sending emails immediately if there are recipients
        if total_emails > 0:
            background_tasks.add_task(
                send_campaign_emails,
                db_campaign.id,
                template,
                upload_data['rows']
            )

        return CampaignResponse.from_orm(db_campaign)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating campaign: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create campaign"
        )

@router.get("/", response_model=List[CampaignResponse])
async def list_campaigns(
    skip: int = 0,
    limit: int = 100,
    status: Optional[CampaignStatus] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """List all campaigns"""
    try:
        query = select(Campaign)

        if status:
            query = query.where(Campaign.status == status)

        query = query.order_by(desc(Campaign.created_at)).offset(skip).limit(limit)

        result = await db.execute(query)
        campaigns = result.scalars().all()

        return [CampaignResponse.from_orm(campaign) for campaign in campaigns]

    except Exception as e:
        logger.error(f"Error listing campaigns: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list campaigns"
        )

@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get campaign details"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        return CampaignResponse.from_orm(campaign)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting campaign {campaign_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get campaign"
        )

@router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: str,
    campaign_update: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Update campaign details"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        # Update fields
        update_data = campaign_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(campaign, field, value)

        await db.commit()
        await db.refresh(campaign)

        logger.info(f"Updated campaign: {campaign.name}")

        return CampaignResponse.from_orm(campaign)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating campaign {campaign_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update campaign"
        )

@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Delete a campaign"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        await db.delete(campaign)
        await db.commit()

        logger.info(f"Deleted campaign: {campaign.name}")

        return {"message": "Campaign deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting campaign {campaign_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete campaign"
        )

@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Pause a running campaign"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        if campaign.status != CampaignStatus.SENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only running campaigns can be paused"
            )

        campaign.status = CampaignStatus.PAUSED
        await db.commit()

        logger.info(f"Paused campaign: {campaign.name}")

        return {"message": "Campaign paused successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing campaign {campaign_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to pause campaign"
        )

@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Resume a paused campaign"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        if campaign.status != CampaignStatus.PAUSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only paused campaigns can be resumed"
            )

        campaign.status = CampaignStatus.SENDING
        await db.commit()

        # Get template and data for resuming
        from models.email import EmailTemplate
        template = await db.get(EmailTemplate, campaign.template_id)

        if campaign.upload_id:
            upload = await db.get(Upload, campaign.upload_id)
            upload_data = upload.processed_data if upload else None
            rows = upload_data['rows'] if upload_data and 'rows' in upload_data else []

            # Resume sending emails
            background_tasks.add_task(
                send_campaign_emails,
                campaign.id,
                template,
                rows
            )

        logger.info(f"Resumed campaign: {campaign.name}")

        return {"message": "Campaign resumed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming campaign {campaign_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resume campaign"
        )

@router.get("/{campaign_id}/progress")
async def get_campaign_progress(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get real-time progress of a campaign"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        # Get email logs for this campaign
        result = await db.execute(
            select(EmailLog)
            .where(EmailLog.campaign_id == campaign.id)
            .order_by(desc(EmailLog.created_at))
        )
        email_logs = result.scalars().all()

        # Calculate progress metrics
        total_processed = campaign.sent_count + campaign.error_count
        progress_percentage = (total_processed / campaign.total_emails * 100) if campaign.total_emails > 0 else 0

        # Get status breakdown
        status_breakdown = {}
        for log in email_logs:
            status = log.status.value
            status_breakdown[status] = status_breakdown.get(status, 0) + 1

        return {
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "status": campaign.status.value,
            "total_emails": campaign.total_emails,
            "sent_count": campaign.sent_count,
            "error_count": campaign.error_count,
            "pending_count": campaign.total_emails - total_processed,
            "progress_percentage": round(progress_percentage, 2),
            "delivery_rate": round(campaign.delivery_rate, 2),
            "status_breakdown": status_breakdown,
            "created_at": campaign.created_at,
            "updated_at": campaign.updated_at
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting campaign progress {campaign_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get campaign progress"
        )

@router.get("/{campaign_id}/logs", response_model=List[EmailLogResponse])
async def get_campaign_logs(
    campaign_id: str,
    skip: int = 0,
    limit: int = 100,
    status: Optional[EmailStatus] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get email logs for a campaign"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        query = select(EmailLog).where(EmailLog.campaign_id == campaign.id)

        if status:
            query = query.where(EmailLog.status == status)

        query = query.order_by(desc(EmailLog.created_at)).offset(skip).limit(limit)

        result = await db.execute(query)
        logs = result.scalars().all()

        return [EmailLogResponse.from_orm(log) for log in logs]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting campaign logs {campaign_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get campaign logs"
        )

async def send_campaign_emails(
    campaign_id: str,
    template,
    rows: List[Dict[str, Any]]
):
    """Send emails for a campaign"""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            campaign = await db.get(Campaign, campaign_id)
            if not campaign:
                logger.error(f"Campaign {campaign_id} not found")
                return

            # Filter valid rows
            valid_rows = [row for row in rows if row.get('valid', True)]

            for row in valid_rows:
                # Check if campaign is paused or cancelled
                campaign = await db.get(Campaign, campaign_id)
                if campaign.status in [CampaignStatus.PAUSED, CampaignStatus.CANCELLED, CampaignStatus.FAILED]:
                    logger.info(f"Campaign {campaign_id} stopped (status: {campaign.status.value})")
                    break

                data = row['data']
                email = data.get('email')

                if not email:
                    continue

                try:
                    # Create email from template
                    email_result = template_service.create_email_from_template(
                        subject_template=template.subject,
                        content_template=template.content,
                        data=data
                    )

                    if not email_result['success']:
                        await log_email_error(db, campaign_id, email, email_result['error'])
                        continue

                    # Send email
                    send_result = await email_service.send_email(
                        to_email=email,
                        subject=email_result['subject'],
                        content=email_result['text_content'],
                        html_content=email_result.get('html_content'),
                        email_type=EmailType.BULK
                    )

                    if send_result['status'] == 'success':
                        await log_email_success(db, campaign_id, email, send_result.get('message_id'))
                        campaign.sent_count += 1
                    else:
                        await log_email_error(db, campaign_id, email, send_result.get('message', 'Unknown error'))
                        campaign.error_count += 1

                except Exception as e:
                    logger.error(f"Error sending email to {email}: {str(e)}")
                    await log_email_error(db, campaign_id, email, str(e))
                    campaign.error_count += 1

                # Update campaign progress
                campaign.delivery_rate = (campaign.sent_count / campaign.total_emails * 100) if campaign.total_emails > 0 else 0
                await db.commit()

            # Mark campaign as completed
            campaign.status = CampaignStatus.COMPLETED
            await db.commit()

            logger.info(f"Campaign {campaign_id} completed: {campaign.sent_count}/{campaign.total_emails} emails sent")

        except Exception as e:
            logger.error(f"Error in send_campaign_emails for campaign {campaign_id}: {str(e)}")
            # Mark campaign as failed
            try:
                campaign = await db.get(Campaign, campaign_id)
                if campaign:
                    campaign.status = CampaignStatus.FAILED
                    await db.commit()
            except:
                pass

async def log_email_success(db, campaign_id: str, email: str, message_id: str):
    """Log successful email sending"""
    log = EmailLog(
        campaign_id=campaign_id,
        to_email=email,
        subject="Campaign Email",
        status=EmailStatus.SENT,
        external_id=message_id,
        sent_at=str(func.now())
    )
    db.add(log)

async def log_email_error(db, campaign_id: str, email: str, error_message: str):
    """Log email sending error"""
    log = EmailLog(
        campaign_id=campaign_id,
        to_email=email,
        subject="Campaign Email",
        status=EmailStatus.FAILED,
        error_message=error_message
    )
    db.add(log)