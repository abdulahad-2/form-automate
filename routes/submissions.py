from fastapi import APIRouter, HTTPException, status, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any, Optional
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address
from email_validator import validate_email, EmailNotValidError

from database import get_db, get_redis
from models.form import Form, FormSubmission, FormStatus, SubmissionStatus
from schemas.form import ExternalFormSubmission, FormSubmissionResponse
from services.email_service import email_service, EmailType
from services.rate_limit_service import rate_limit_service
from services.template_service import template_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/submit", tags=["submissions"])
limiter = Limiter(key_func=get_remote_address)

async def process_form_submission(
    form_id: str,
    submission_data: Dict[str, Any],
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession
) -> Dict[str, Any]:
    """Process form submission and send emails"""
    try:
        client_ip = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")

        # Find form
        form = None
        if form_id != "default":
            result = await db.execute(
                select(Form).where(Form.form_id == form_id, Form.status == FormStatus.ACTIVE)
            )
            form = result.scalar_one_or_none()

        # For backward compatibility, use default form behavior if no specific form found
        if not form and form_id != "default":
            # Try to find the original default behavior
            logger.info(f"Form {form_id} not found, using default submission handler")
            return await handle_legacy_submission(submission_data, background_tasks)

        # Check rate limiting
        rate_limit_result = await rate_limit_service.is_rate_limited(
            key=client_ip,
            limit_type="form_submission"
        )

        if not rate_limit_result['allowed']:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=rate_limit_result['error']
            )

        # Check for suspicious activity
        suspicious_result = await rate_limit_service.check_suspicious_activity(
            ip_address=client_ip,
            email=submission_data.get("email"),
            action_type="form_submission"
        )

        if suspicious_result['blocked']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Submission blocked due to suspicious activity"
            )

        # Validate and extract email
        email_value = None
        email_fields = ["email", "Email", "user_email", "contact_email"]
        for field in email_fields:
            if field in submission_data and submission_data[field]:
                try:
                    validated = validate_email(submission_data[field])
                    email_value = validated.email
                    break
                except EmailNotValidError:
                    logger.warning(f"Invalid email format: {submission_data[field]}")

        # Create submission record
        db_submission = FormSubmission(
            form_id=form.id if form else None,
            email=email_value,
            data=submission_data,
            status=SubmissionStatus.PENDING,
            ip_address=client_ip,
            user_agent=user_agent
        )

        db.add(db_submission)
        await db.commit()
        await db.refresh(db_submission)

        # Send emails in background
        background_tasks.add_task(
            process_submission_emails,
            form_id,
            submission_data,
            email_value,
            db_submission.id
        )

        logger.info(f"Form submission processed: {db_submission.id}")

        return {
            "status": "success",
            "message": "Form submitted successfully",
            "submission_id": db_submission.id,
            "form_id": form_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing form submission: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process submission"
        )

async def handle_legacy_submission(
    submission_data: Dict[str, Any],
    background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """Handle legacy form submissions (backward compatibility)"""
    try:
        # Extract email for auto-reply
        email_value = None
        email_fields = ["email", "Email", "user_email", "contact_email"]
        for field in email_fields:
            if field in submission_data and submission_data[field]:
                try:
                    validated = validate_email(submission_data[field])
                    email_value = validated.email
                    break
                except EmailNotValidError:
                    logger.warning(f"Invalid email format: {submission_data[field]}")

        # Send admin email in background
        if email_value:
            background_tasks.add_task(send_legacy_admin_email, submission_data, email_value)
        else:
            background_tasks.add_task(send_legacy_admin_email, submission_data, None)

        # Send auto-reply if email is valid
        if email_value:
            background_tasks.add_task(send_legacy_auto_reply, email_value)

        return {
            "status": "success",
            "message": "Form submitted successfully"
        }

    except Exception as e:
        logger.error(f"Error in legacy submission handling: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process submission"
        )

async def process_submission_emails(
    form_id: str,
    submission_data: Dict[str, Any],
    email_value: Optional[str],
    submission_id: str
):
    """Process emails for a form submission"""
    try:
        from database import AsyncSessionLocal
        from os import getenv

        async with AsyncSessionLocal() as db:
            # Get form details if available
            form = None
            if form_id and form_id != "default":
                result = await db.execute(
                    select(Form).where(Form.id == form_id)
                )
                form = result.scalar_one_or_none()

            # Format submission data
            formatted_data = []
            for key, value in submission_data.items():
                if key.lower() not in ['password', 'secret', 'token']:  # Exclude sensitive fields
                    formatted_data.append(f"{key}: {value}")

            email_body = "New Form Submission\n\n" + "\n".join(formatted_data)

            # Get admin email from environment or form settings
            admin_email = getenv("ADMIN_EMAIL", "admin@example.com")
            if form and form.settings and form.settings.get("admin_email"):
                admin_email = form.settings["admin_email"]

            # Send admin notification
            admin_result = await email_service.send_email(
                to_email=admin_email,
                subject=f"New form submission - {form.name if form else 'Default Form'}",
                content=email_body,
                email_type=EmailType.TRANSACTIONAL
            )

            if admin_result['status'] != 'success':
                logger.error(f"Failed to send admin email: {admin_result.get('message', 'Unknown error')}")

            # Send auto-reply if email is provided
            if email_value:
                # Use custom auto-reply template if configured
                auto_reply_subject = "We've received your submission"
                auto_reply_content = (
                    "Thank you for contacting us. We have received your form submission "
                    "and will get back to you soon.\n\n— Team"
                )

                if form and form.settings:
                    settings = form.settings
                    if settings.get("auto_reply_enabled"):
                        auto_reply_subject = settings.get("auto_reply_subject", auto_reply_subject)
                        auto_reply_content = settings.get("auto_reply_content", auto_reply_content)

                        # Use template service if template is provided
                        if settings.get("auto_reply_template"):
                            template_result = template_service.render_template(
                                settings["auto_reply_template"],
                                submission_data
                            )
                            if template_result['success']:
                                auto_reply_content = template_result['rendered_content']

                auto_reply_result = await email_service.send_email(
                    to_email=email_value,
                    subject=auto_reply_subject,
                    content=auto_reply_content,
                    email_type=EmailType.AUTORESPONSE
                )

                if auto_reply_result['status'] != 'success':
                    logger.error(f"Failed to send auto-reply: {auto_reply_result.get('message', 'Unknown error')}")

            # Update submission status
            submission = await db.get(FormSubmission, submission_id)
            if submission:
                submission.status = SubmissionStatus.PROCESSED
                await db.commit()

    except Exception as e:
        logger.error(f"Error processing submission emails: {str(e)}")

async def send_legacy_admin_email(submission_data: Dict[str, Any], email_value: Optional[str]):
    """Send admin email for legacy submissions"""
    try:
        from os import getenv
        from gmail_service import GmailService
        import pydantic_settings

        # Get settings from environment
        ADMIN_EMAIL = getenv("ADMIN_EMAIL", "admin@example.com")
        FROM_EMAIL = getenv("FROM_EMAIL", "noreply@example.com")

        # Format payload
        lines = []
        for k, v in submission_data.items():
            lines.append(f"{k}: {v}")
        formatted = "\n".join(lines)
        subject = "New form submission"

        # Send using existing Gmail service
        gmail_service = GmailService()
        result = gmail_service.send_email(
            to_email=ADMIN_EMAIL,
            subject=subject,
            body=formatted,
            from_email=FROM_EMAIL
        )

        if result["status"] == "error":
            logger.error(f"Failed to send admin email: {result['message']}")

    except Exception as e:
        logger.error(f"Error in legacy admin email: {str(e)}")

async def send_legacy_auto_reply(email: str):
    """Send auto-reply for legacy submissions"""
    try:
        from os import getenv
        from gmail_service import GmailService

        FROM_EMAIL = getenv("FROM_EMAIL", "noreply@example.com")

        subject = "We've received your submission"
        body = (
            "Thank you for contacting us. We have received your form submission "
            "and will get back to you soon.\n\n— Team"
        )

        gmail_service = GmailService()
        result = gmail_service.send_email(
            to_email=email,
            subject=subject,
            body=body,
            from_email=FROM_EMAIL
        )

        if result["status"] == "error":
            logger.error(f"Failed to send auto-reply: {result['message']}")

    except Exception as e:
        logger.error(f"Error in legacy auto-reply: {str(e)}")

@router.post("/{form_id}")
async def submit_form(
    form_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Submit form data to a specific form endpoint
    This is the enhanced version of the original /submit-form endpoint
    """
    try:
        # Get JSON data
        submission_data = await request.json()

        if not isinstance(submission_data, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )

        # Process submission
        result = await process_form_submission(form_id, submission_data, request, background_tasks, db)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in form submission: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process submission"
        )

# Keep the original endpoint for backward compatibility
@router.post("/form")
async def submit_form_legacy(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Legacy form submission endpoint (original /submit-form behavior)
    """
    try:
        # Get JSON data
        submission_data = await request.json()

        if not isinstance(submission_data, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )

        # Process as default form
        result = await process_form_submission("default", submission_data, request, background_tasks, db)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in legacy form submission: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process submission"
        )