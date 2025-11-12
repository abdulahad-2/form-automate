from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import logging
import uuid
from slowapi import Limiter
from slowapi.util import get_remote_address

from database import get_db, get_redis
from models.form import Form, FormSubmission, FormStatus, SubmissionStatus
from schemas.form import FormCreate, FormUpdate, FormResponse, FormSubmissionResponse
from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service
from services.email_service import email_service, EmailType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forms", tags=["forms"])
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

@router.post("/", response_model=FormResponse)
async def create_form(
    form_data: FormCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Create a new form endpoint"""
    try:
        # Check if form_id already exists
        existing_form = await db.execute(
            select(Form).where(Form.form_id == form_data.form_id)
        )
        if existing_form.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Form ID '{form_data.form_id}' already exists"
            )

        # Create new form
        db_form = Form(
            name=form_data.name,
            form_id=form_data.form_id,
            description=form_data.description,
            settings=form_data.settings or {},
            status=FormStatus.ACTIVE
        )

        db.add(db_form)
        await db.commit()
        await db.refresh(db_form)

        logger.info(f"Created new form: {db_form.form_id}")

        return FormResponse.from_orm(db_form)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating form: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create form"
        )

@router.get("/", response_model=List[FormResponse])
async def list_forms(
    skip: int = 0,
    limit: int = 100,
    status: Optional[FormStatus] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """List all forms"""
    try:
        query = select(Form)

        if status:
            query = query.where(Form.status == status)

        query = query.order_by(desc(Form.created_at)).offset(skip).limit(limit)

        result = await db.execute(query)
        forms = result.scalars().all()

        return [FormResponse.from_orm(form) for form in forms]

    except Exception as e:
        logger.error(f"Error listing forms: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list forms"
        )

@router.get("/{form_id}", response_model=FormResponse)
async def get_form(
    form_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get form details"""
    try:
        # Try to find by ID first, then by form_id
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

        return FormResponse.from_orm(form)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting form {form_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get form"
        )

@router.put("/{form_id}", response_model=FormResponse)
async def update_form(
    form_id: str,
    form_update: FormUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Update form details"""
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

        # Update fields
        update_data = form_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(form, field, value)

        await db.commit()
        await db.refresh(form)

        logger.info(f"Updated form: {form.form_id}")

        return FormResponse.from_orm(form)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating form {form_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update form"
        )

@router.delete("/{form_id}")
async def delete_form(
    form_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Delete a form"""
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

        await db.delete(form)
        await db.commit()

        logger.info(f"Deleted form: {form.form_id}")

        return {"message": "Form deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting form {form_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete form"
        )

@router.get("/{form_id}/submissions", response_model=List[FormSubmissionResponse])
async def get_form_submissions(
    form_id: str,
    skip: int = 0,
    limit: int = 100,
    status: Optional[SubmissionStatus] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get submissions for a specific form"""
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

        # Query submissions
        query = select(FormSubmission).where(FormSubmission.form_id == form.id)

        if status:
            query = query.where(FormSubmission.status == status)

        query = query.order_by(desc(FormSubmission.created_at)).offset(skip).limit(limit)

        result = await db.execute(query)
        submissions = result.scalars().all()

        return [FormSubmissionResponse.from_orm(submission) for submission in submissions]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting submissions for form {form_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get form submissions"
        )

@router.post("/{form_id}/embed-code")
async def get_embed_code(
    form_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Generate embed code for a form"""
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

        # Generate embed code
        base_url = "https://your-domain.com"  # This should come from environment
        form_url = f"{base_url}/api/submit/{form.form_id}"

        embed_codes = {
            "html_form": f'''
<form action="{form_url}" method="POST" id="form-{form.form_id}">
    <!-- Add your form fields here -->
    <input type="text" name="name" required placeholder="Your Name">
    <input type="email" name="email" required placeholder="Your Email">
    <textarea name="message" placeholder="Your Message"></textarea>
    <button type="submit">Submit</button>
</form>
            '''.strip(),
            "javascript": f'''
<script>
(function() {{
    var form = document.createElement('form');
    form.action = '{form_url}';
    form.method = 'POST';
    form.id = 'form-{form.form_id}';

    // Add fields
    var nameField = document.createElement('input');
    nameField.type = 'text';
    nameField.name = 'name';
    nameField.required = true;
    nameField.placeholder = 'Your Name';
    form.appendChild(nameField);

    var emailField = document.createElement('input');
    emailField.type = 'email';
    emailField.name = 'email';
    emailField.required = true;
    emailField.placeholder = 'Your Email';
    form.appendChild(emailField);

    var messageField = document.createElement('textarea');
    messageField.name = 'message';
    messageField.placeholder = 'Your Message';
    form.appendChild(messageField);

    var submitBtn = document.createElement('button');
    submitBtn.type = 'submit';
    submitBtn.textContent = 'Submit';
    form.appendChild(submitBtn);

    // Add to page
    document.body.appendChild(form);
}})();
</script>
            '''.strip(),
            "iframe": f'<iframe src="{form_url}?embed=true" width="100%" height="500" frameborder="0"></iframe>',
            "direct_url": form_url
        }

        return {
            "form_id": form.form_id,
            "form_name": form.name,
            "embed_codes": embed_codes
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating embed code for form {form_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate embed code"
        )

@router.get("/{form_id}/stats")
async def get_form_stats(
    form_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get statistics for a specific form"""
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

        # Get submission statistics
        total_submissions = await db.execute(
            select(func.count(FormSubmission.id))
            .where(FormSubmission.form_id == form.id)
        )
        total_count = total_submissions.scalar() or 0

        # Get submissions by status
        status_stats = await db.execute(
            select(FormSubmission.status, func.count(FormSubmission.id))
            .where(FormSubmission.form_id == form.id)
            .group_by(FormSubmission.status)
        )
        status_counts = dict(status_stats.all())

        # Get recent submissions count (last 7 days)
        from datetime import datetime, timedelta
        week_ago = datetime.utcnow() - timedelta(days=7)

        recent_submissions = await db.execute(
            select(func.count(FormSubmission.id))
            .where(FormSubmission.form_id == form.id)
            .where(FormSubmission.created_at >= week_ago)
        )
        recent_count = recent_submissions.scalar() or 0

        return {
            "form_id": form.form_id,
            "form_name": form.name,
            "total_submissions": total_count,
            "recent_submissions": recent_count,
            "status_breakdown": {
                status.value: count for status, count in status_counts.items()
            },
            "form_status": form.status.value,
            "created_at": form.created_at,
            "updated_at": form.updated_at
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stats for form {form_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get form stats"
        )