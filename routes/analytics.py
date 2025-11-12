from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, or_
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

from database import get_db, get_redis
from models.form import Form, FormSubmission, FormStatus, SubmissionStatus
from models.email import Campaign, EmailLog, CampaignStatus, EmailStatus
from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
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

@router.get("/dashboard")
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get overall dashboard statistics"""
    try:
        # Get date ranges for different periods
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        month_start = today_start - timedelta(days=30)

        # Form statistics
        total_forms = await db.execute(select(func.count(Form.id)))
        active_forms = await db.execute(select(func.count(Form.id)).where(Form.status == FormStatus.ACTIVE))

        # Form submissions
        total_submissions = await db.execute(select(func.count(FormSubmission.id)))
        today_submissions = await db.execute(
            select(func.count(FormSubmission.id))
            .where(FormSubmission.created_at >= today_start)
        )
        week_submissions = await db.execute(
            select(func.count(FormSubmission.id))
            .where(FormSubmission.created_at >= week_start)
        )

        # Campaign statistics
        total_campaigns = await db.execute(select(func.count(Campaign.id)))
        active_campaigns = await db.execute(
            select(func.count(Campaign.id))
            .where(Campaign.status == CampaignStatus.SENDING)
        )

        # Email statistics
        total_emails = await db.execute(select(func.count(EmailLog.id)))
        sent_emails = await db.execute(
            select(func.count(EmailLog.id))
            .where(EmailLog.status == EmailStatus.SENT)
        )
        failed_emails = await db.execute(
            select(func.count(EmailLog.id))
            .where(EmailLog.status == EmailStatus.FAILED)
        )

        # Calculate delivery rate
        total_sent_count = sent_emails.scalar() or 0
        total_failed_count = failed_emails.scalar() or 0
        total_processed = total_sent_count + total_failed_count
        delivery_rate = (total_sent_count / total_processed * 100) if total_processed > 0 else 0

        # Recent activity
        recent_forms = await db.execute(
            select(Form)
            .order_by(desc(Form.created_at))
            .limit(5)
        )
        recent_campaigns = await db.execute(
            select(Campaign)
            .order_by(desc(Campaign.created_at))
            .limit(5)
        )

        return {
            "forms": {
                "total": total_forms.scalar() or 0,
                "active": active_forms.scalar() or 0,
                "inactive": (total_forms.scalar() or 0) - (active_forms.scalar() or 0)
            },
            "submissions": {
                "total": total_submissions.scalar() or 0,
                "today": today_submissions.scalar() or 0,
                "this_week": week_submissions.scalar() or 0,
                "growth_rate": calculate_growth_rate(
                    await get_submissions_by_period(db, week_start, today_start),
                    await get_submissions_by_period(db, week_start - timedelta(days=7), week_start)
                )
            },
            "campaigns": {
                "total": total_campaigns.scalar() or 0,
                "active": active_campaigns.scalar() or 0,
                "completed": await get_campaigns_by_status(db, CampaignStatus.COMPLETED),
                "failed": await get_campaigns_by_status(db, CampaignStatus.FAILED)
            },
            "emails": {
                "total": total_emails.scalar() or 0,
                "sent": total_sent_count,
                "failed": total_failed_count,
                "delivery_rate": round(delivery_rate, 2),
                "pending": (total_emails.scalar() or 0) - total_processed
            },
            "recent_activity": {
                "forms": [
                    {
                        "id": form.id,
                        "name": form.name,
                        "form_id": form.form_id,
                        "status": form.status.value,
                        "created_at": form.created_at
                    }
                    for form in recent_forms.scalars().all()
                ],
                "campaigns": [
                    {
                        "id": campaign.id,
                        "name": campaign.name,
                        "status": campaign.status.value,
                        "total_emails": campaign.total_emails,
                        "sent_count": campaign.sent_count,
                        "created_at": campaign.created_at
                    }
                    for campaign in recent_campaigns.scalars().all()
                ]
            }
        }

    except Exception as e:
        logger.error(f"Error getting dashboard stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get dashboard statistics"
        )

@router.get("/campaigns/{campaign_id}")
async def get_campaign_analytics(
    campaign_id: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get detailed analytics for a specific campaign"""
    try:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found"
            )

        # Date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        # Get email logs for the campaign
        result = await db.execute(
            select(EmailLog)
            .where(
                and_(
                    EmailLog.campaign_id == campaign.id,
                    EmailLog.created_at >= start_date
                )
            )
            .order_by(desc(EmailLog.created_at))
        )
        email_logs = result.scalars().all()

        # Calculate statistics
        total_logs = len(email_logs)
        status_counts = {}
        daily_stats = {}
        domains = {}

        for log in email_logs:
            # Status counts
            status = log.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

            # Daily statistics
            date_key = log.created_at.strftime('%Y-%m-%d')
            if date_key not in daily_stats:
                daily_stats[date_key] = {
                    'sent': 0,
                    'failed': 0,
                    'delivered': 0,
                    'bounced': 0,
                    'total': 0
                }

            daily_stats[date_key][status] = daily_stats[date_key].get(status, 0) + 1
            daily_stats[date_key]['total'] += 1

            # Domain analysis
            if log.to_email:
                domain = log.to_email.split('@')[-1].lower()
                if domain not in domains:
                    domains[domain] = {'sent': 0, 'failed': 0, 'total': 0}

                domains[domain]['total'] += 1
                if status == EmailStatus.SENT.value:
                    domains[domain]['sent'] += 1
                elif status == EmailStatus.FAILED.value:
                    domains[domain]['failed'] += 1

        # Sort daily stats by date
        sorted_daily_stats = dict(sorted(daily_stats.items()))

        # Calculate domain success rates
        domain_analysis = {}
        for domain, stats in domains.items():
            success_rate = (stats['sent'] / stats['total'] * 100) if stats['total'] > 0 else 0
            domain_analysis[domain] = {
                'total': stats['total'],
                'sent': stats['sent'],
                'failed': stats['failed'],
                'success_rate': round(success_rate, 2)
            }

        # Top domains by volume
        top_domains = sorted(
            domain_analysis.items(),
            key=lambda x: x[1]['total'],
            reverse=True
        )[:10]

        return {
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "period_days": days,
            "summary": {
                "total_emails": campaign.total_emails,
                "sent_count": campaign.sent_count,
                "error_count": campaign.error_count,
                "delivery_rate": round(campaign.delivery_rate, 2),
                "status": campaign.status.value,
                "logs_analyzed": total_logs
            },
            "status_breakdown": status_counts,
            "daily_stats": sorted_daily_stats,
            "domain_analysis": {
                "top_domains": dict(top_domains),
                "unique_domains": len(domain_analysis)
            },
            "timeline": {
                "created_at": campaign.created_at,
                "updated_at": campaign.updated_at
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting campaign analytics {campaign_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get campaign analytics"
        )

@router.get("/forms/{form_id}")
async def get_form_analytics(
    form_id: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get detailed analytics for a specific form"""
    try:
        # Find form
        form = await db.get(Form, form_id)
        if not form:
            result = await db.execute(
                select(Form).where(Form.form_id == form_id)
            )
            form = result.scalar_one_or_none()

        if not form:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        # Date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        # Get submissions for the form
        result = await db.execute(
            select(FormSubmission)
            .where(
                and_(
                    FormSubmission.form_id == form.id,
                    FormSubmission.created_at >= start_date
                )
            )
            .order_by(desc(FormSubmission.created_at))
        )
        submissions = result.scalars().all()

        # Calculate statistics
        total_submissions = len(submissions)
        status_counts = {}
        daily_stats = {}
        submission_sources = {}  # By IP address
        submission_fields = {}  # Field usage analysis

        for submission in submissions:
            # Status counts
            status = submission.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

            # Daily statistics
            date_key = submission.created_at.strftime('%Y-%m-%d')
            if date_key not in daily_stats:
                daily_stats[date_key] = {
                    'pending': 0,
                    'processed': 0,
                    'failed': 0,
                    'spam': 0,
                    'total': 0
                }

            daily_stats[date_key][status] = daily_stats[date_key].get(status, 0) + 1
            daily_stats[date_key]['total'] += 1

            # Submission sources
            if submission.ip_address:
                source = submission.ip_address
                submission_sources[source] = submission_sources.get(source, 0) + 1

            # Field analysis
            if submission.data:
                for field in submission.data.keys():
                    if field.lower() not in ['password', 'secret', 'token']:  # Exclude sensitive fields
                        submission_fields[field] = submission_fields.get(field, 0) + 1

        # Sort daily stats by date
        sorted_daily_stats = dict(sorted(daily_stats.items()))

        # Top submission sources
        top_sources = sorted(
            submission_sources.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]

        # Field usage analysis
        field_analysis = {
            field: {
                'usage_count': count,
                'usage_percentage': round((count / total_submissions * 100), 2) if total_submissions > 0 else 0
            }
            for field, count in submission_fields.items()
        }

        return {
            "form_id": form.id,
            "form_name": form.name,
            "form_identifier": form.form_id,
            "period_days": days,
            "summary": {
                "total_submissions": total_submissions,
                "status": form.status.value,
                "submissions_analyzed": len(submissions)
            },
            "status_breakdown": status_counts,
            "daily_stats": sorted_daily_stats,
            "submission_analysis": {
                "unique_sources": len(submission_sources),
                "top_sources": dict(top_sources)
            },
            "field_analysis": field_analysis,
            "timeline": {
                "created_at": form.created_at,
                "updated_at": form.updated_at
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting form analytics {form_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get form analytics"
        )

@router.get("/email-performance")
async def get_email_performance(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get overall email performance analytics"""
    try:
        # Date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        # Get email logs
        result = await db.execute(
            select(EmailLog)
            .where(EmailLog.created_at >= start_date)
            .order_by(desc(EmailLog.created_at))
        )
        email_logs = result.scalars().all()

        if not email_logs:
            return {
                "period_days": days,
                "total_emails": 0,
                "performance_metrics": {},
                "daily_performance": {},
                "domain_performance": {},
                "campaign_performance": {}
            }

        # Calculate performance metrics
        total_emails = len(email_logs)
        status_counts = {}
        daily_performance = {}
        domain_performance = {}
        campaign_performance = {}

        for log in email_logs:
            # Status counts
            status = log.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

            # Daily performance
            date_key = log.created_at.strftime('%Y-%m-%d')
            if date_key not in daily_performance:
                daily_performance[date_key] = {
                    'sent': 0,
                    'failed': 0,
                    'delivered': 0,
                    'bounced': 0,
                    'total': 0
                }

            daily_performance[date_key][status] = daily_performance[date_key].get(status, 0) + 1
            daily_performance[date_key]['total'] += 1

            # Domain performance
            if log.to_email:
                domain = log.to_email.split('@')[-1].lower()
                if domain not in domain_performance:
                    domain_performance[domain] = {'sent': 0, 'failed': 0, 'total': 0}

                domain_performance[domain]['total'] += 1
                if status == EmailStatus.SENT.value:
                    domain_performance[domain]['sent'] += 1
                elif status == EmailStatus.FAILED.value:
                    domain_performance[domain]['failed'] += 1

            # Campaign performance
            if log.campaign_id:
                if log.campaign_id not in campaign_performance:
                    campaign_performance[log.campaign_id] = {
                        'sent': 0,
                        'failed': 0,
                        'total': 0
                    }

                campaign_performance[log.campaign_id]['total'] += 1
                if status == EmailStatus.SENT.value:
                    campaign_performance[log.campaign_id]['sent'] += 1
                elif status == EmailStatus.FAILED.value:
                    campaign_performance[log.campaign_id]['failed'] += 1

        # Calculate success rates
        for domain in domain_performance:
            stats = domain_performance[domain]
            success_rate = (stats['sent'] / stats['total'] * 100) if stats['total'] > 0 else 0
            domain_performance[domain]['success_rate'] = round(success_rate, 2)

        for campaign_id in campaign_performance:
            stats = campaign_performance[campaign_id]
            success_rate = (stats['sent'] / stats['total'] * 100) if stats['total'] > 0 else 0
            campaign_performance[campaign_id]['success_rate'] = round(success_rate, 2)

        # Overall performance metrics
        sent_count = status_counts.get(EmailStatus.SENT.value, 0)
        failed_count = status_counts.get(EmailStatus.FAILED.value, 0)
        total_processed = sent_count + failed_count
        overall_delivery_rate = (sent_count / total_processed * 100) if total_processed > 0 else 0

        return {
            "period_days": days,
            "summary": {
                "total_emails": total_emails,
                "overall_delivery_rate": round(overall_delivery_rate, 2),
                "status_breakdown": status_counts
            },
            "daily_performance": dict(sorted(daily_performance.items())),
            "domain_performance": {
                "domains": domain_performance,
                "top_performing": sorted(
                    [(d, s) for d, s in domain_performance.items() if s.get('success_rate', 0) > 0],
                    key=lambda x: x[1]['success_rate'],
                    reverse=True
                )[:10]
            },
            "campaign_performance": campaign_performance
        }

    except Exception as e:
        logger.error(f"Error getting email performance analytics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get email performance analytics"
        )

# Helper functions
async def get_submissions_by_period(db: AsyncSession, start_date: datetime, end_date: datetime) -> int:
    """Get count of submissions in a time period"""
    result = await db.execute(
        select(func.count(FormSubmission.id))
        .where(
            and_(
                FormSubmission.created_at >= start_date,
                FormSubmission.created_at < end_date
            )
        )
    )
    return result.scalar() or 0

async def get_campaigns_by_status(db: AsyncSession, status: CampaignStatus) -> int:
    """Get count of campaigns by status"""
    result = await db.execute(
        select(func.count(Campaign.id)).where(Campaign.status == status)
    )
    return result.scalar() or 0

def calculate_growth_rate(current: int, previous: int) -> float:
    """Calculate growth rate percentage"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)