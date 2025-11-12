from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

from database import get_db, get_redis
from models.email import EmailTemplate
from schemas.email import EmailTemplateCreate, EmailTemplateUpdate, EmailTemplateResponse, TemplatePreviewRequest, TemplatePreviewResponse
from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service
from services.template_service import template_service
from services.email_service import email_service, EmailType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])
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

@router.post("/", response_model=EmailTemplateResponse)
async def create_template(
    template_data: EmailTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Create a new email template"""
    try:
        # Validate template syntax and extract variables
        validation_result = template_service.validate_template(template_data.content)
        if not validation_result['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid template: {', '.join(validation_result['errors'])}"
            )

        # Extract variables from content
        content_variables = template_service.extract_variables(template_data.content)
        subject_variables = template_service.extract_variables(template_data.subject)
        all_variables = list(set(content_variables + subject_variables))

        # Create template
        db_template = EmailTemplate(
            name=template_data.name,
            subject=template_data.subject,
            content=template_data.content,
            description=template_data.description,
            variables=all_variables
        )

        db.add(db_template)
        await db.commit()
        await db.refresh(db_template)

        logger.info(f"Created new email template: {db_template.name}")

        return EmailTemplateResponse.from_orm(db_template)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating email template: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create email template"
        )

@router.get("/", response_model=List[EmailTemplateResponse])
async def list_templates(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """List all email templates"""
    try:
        query = select(EmailTemplate).order_by(desc(EmailTemplate.created_at)).offset(skip).limit(limit)

        result = await db.execute(query)
        templates = result.scalars().all()

        return [EmailTemplateResponse.from_orm(template) for template in templates]

    except Exception as e:
        logger.error(f"Error listing email templates: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list email templates"
        )

@router.get("/{template_id}", response_model=EmailTemplateResponse)
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get template details"""
    try:
        template = await db.get(EmailTemplate, template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )

        return EmailTemplateResponse.from_orm(template)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting template {template_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get email template"
        )

@router.put("/{template_id}", response_model=EmailTemplateResponse)
async def update_template(
    template_id: str,
    template_update: EmailTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Update email template"""
    try:
        template = await db.get(EmailTemplate, template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )

        # Update fields
        update_data = template_update.dict(exclude_unset=True)

        # Validate template if content is being updated
        if 'content' in update_data:
            validation_result = template_service.validate_template(update_data['content'])
            if not validation_result['valid']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid template: {', '.join(validation_result['errors'])}"
                )

        for field, value in update_data.items():
            setattr(template, field, value)

        # Re-extract variables if content or subject changed
        if 'content' in update_data or 'subject' in update_data:
            content_variables = template_service.extract_variables(template.content)
            subject_variables = template_service.extract_variables(template.subject)
            template.variables = list(set(content_variables + subject_variables))

        await db.commit()
        await db.refresh(template)

        logger.info(f"Updated email template: {template.name}")

        return EmailTemplateResponse.from_orm(template)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating template {template_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update email template"
        )

@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Delete an email template"""
    try:
        template = await db.get(EmailTemplate, template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )

        await db.delete(template)
        await db.commit()

        logger.info(f"Deleted email template: {template.name}")

        return {"message": "Email template deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting template {template_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete email template"
        )

@router.post("/{template_id}/preview")
async def preview_template(
    template_id: str,
    request: TemplatePreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Preview template with sample data"""
    try:
        template = await db.get(EmailTemplate, template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )

        # Generate preview using template service
        preview_result = template_service.preview_template(
            template.content,
            request.sample_data
        )

        # Also preview subject if it has variables
        subject_preview = None
        if template_service.extract_variables(template.subject):
            subject_result = template_service.render_template(
                template.subject,
                request.sample_data
            )
            if subject_result['success']:
                subject_preview = subject_result['rendered_content']
            else:
                subject_preview = template.subject

        return TemplatePreviewResponse(
            subject=subject_preview or template.subject,
            content=preview_result['rendered_content'],
            variables_used=preview_result['template_variables']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing template {template_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to preview email template"
        )

@router.post("/{template_id}/test")
async def test_template(
    template_id: str,
    test_email: str,
    sample_data: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Send test email using template"""
    try:
        template = await db.get(EmailTemplate, template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )

        # Create email from template
        email_result = template_service.create_email_from_template(
            subject_template=template.subject,
            content_template=template.content,
            data=sample_data
        )

        if not email_result['success']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Template rendering failed: {email_result['error']}"
            )

        # Send test email
        background_tasks.add_task(
            send_test_email,
            test_email,
            email_result['subject'],
            email_result['text_content'],
            email_result.get('html_content')
        )

        return {
            "success": True,
            "message": f"Test email sent to {test_email}",
            "subject": email_result['subject'],
            "used_variables": email_result['used_variables'],
            "warnings": email_result.get('warnings', [])
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing template {template_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to test email template"
        )

async def send_test_email(
    to_email: str,
    subject: str,
    text_content: str,
    html_content: Optional[str] = None
):
    """Send test email"""
    try:
        result = await email_service.send_email(
            to_email=to_email,
            subject=subject,
            content=text_content,
            html_content=html_content,
            email_type=EmailType.TRANSACTIONAL
        )

        if result['status'] != 'success':
            logger.error(f"Failed to send test email: {result.get('message', 'Unknown error')}")

    except Exception as e:
        logger.error(f"Error sending test email: {str(e)}")

@router.get("/{template_id}/stats")
async def get_template_stats(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get usage statistics for a template"""
    try:
        template = await db.get(EmailTemplate, template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )

        # Count campaigns using this template
        from models.email import Campaign
        campaign_count = await db.execute(
            select(func.count(Campaign.id))
            .where(Campaign.template_id == template.id)
        )
        campaigns_using = campaign_count.scalar() or 0

        # Get template complexity info
        validation_result = template_service.validate_template(template.content)

        return {
            "template_id": template.id,
            "template_name": template.name,
            "campaigns_using": campaigns_using,
            "variables_count": len(template.variables) if template.variables else 0,
            "variables": template.variables or [],
            "content_length": len(template.content),
            "subject_length": len(template.subject),
            "complexity": validation_result.get('estimated_complexity', 'unknown'),
            "has_html': validation_result.get('has_html', False),
            "created_at": template.created_at,
            "updated_at": template.updated_at
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting template stats {template_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get template statistics"
        )

@router.get("/suggestions/types")
async def get_template_suggestions():
    """Get template suggestions for different types"""
    try:
        suggestion_types = ['general', 'welcome', 'newsletter', 'notification']
        suggestions = {}

        for template_type in suggestion_types:
            suggestions[template_type] = template_service.get_template_suggestions(template_type)

        return {
            "success": True,
            "suggestions": suggestions
        }

    except Exception as e:
        logger.error(f"Error getting template suggestions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get template suggestions"
        )