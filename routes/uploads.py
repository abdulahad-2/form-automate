from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import logging
import uuid
from slowapi import Limiter
from slowapi.util import get_remote_address

from database import get_db, get_redis
from models.upload import Upload, UploadStatus
from schemas.upload import UploadResponse, UploadPreviewRequest, UploadPreviewResponse
from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service
from services.file_service import file_upload_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upload", tags=["uploads"])
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

@router.post("/csv", response_model=UploadResponse)
async def upload_csv(
    request,
    background_tasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Upload and process CSV/Excel file for email campaigns"""
    try:
        # Validate file type
        allowed_types = [
            'text/csv',
            'application/csv',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ]

        if file.content_type not in allowed_types:
            # Check file extension as fallback
            if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Only CSV and Excel files are allowed"
                )

        # Read file content
        file_content = await file.read()
        if not file_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is empty"
            )

        # Process file
        process_result = await file_upload_service.process_file_upload(
            file_content=file_content,
            filename=file.filename,
            content_type=file.content_type
        )

        if not process_result['success']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=process_result['error']
            )

        # Save to database
        db_upload = Upload(
            id=process_result['file_id'],
            filename=process_result['filename'],
            original_filename=process_result['original_filename'],
            file_size=process_result['file_size'],
            file_type=process_result['file_type'],
            s3_key=process_result.get('s3_key'),
            status=UploadStatus.COMPLETED,
            processed_data=process_result['processed_data'],
            validation_errors=process_result.get('validation_errors'),
            total_rows=process_result['total_rows'],
            valid_rows=process_result['valid_rows'],
            invalid_rows=process_result['invalid_rows']
        )

        db.add(db_upload)
        await db.commit()
        await db.refresh(db_upload)

        logger.info(f"File uploaded successfully: {db_upload.original_filename}")

        return UploadResponse.from_orm(db_upload)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file"
        )

@router.get("/", response_model=List[UploadResponse])
async def list_uploads(
    skip: int = 0,
    limit: int = 100,
    status: Optional[UploadStatus] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """List all file uploads"""
    try:
        query = select(Upload)

        if status:
            query = query.where(Upload.status == status)

        query = query.order_by(desc(Upload.created_at)).offset(skip).limit(limit)

        result = await db.execute(query)
        uploads = result.scalars().all()

        return [UploadResponse.from_orm(upload) for upload in uploads]

    except Exception as e:
        logger.error(f"Error listing uploads: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list uploads"
        )

@router.get("/{upload_id}", response_model=UploadResponse)
async def get_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get upload details"""
    try:
        upload = await db.get(Upload, upload_id)
        if not upload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload not found"
            )

        return UploadResponse.from_orm(upload)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting upload {upload_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get upload details"
        )

@router.post("/{upload_id}/preview")
async def preview_upload(
    upload_id: str,
    request: UploadPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Preview uploaded data"""
    try:
        upload = await db.get(Upload, upload_id)
        if not upload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload not found"
            )

        if not upload.processed_data or 'rows' not in upload.processed_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No data available for preview"
            )

        # Get sample data
        rows = upload.processed_data['rows']
        sample_data = rows[:request.limit] if request.limit > 0 else rows[:10]

        # Filter for valid rows only if requested
        if sample_data and all('valid' in row for row in sample_data):
            sample_data = [row for row in sample_data if row.get('valid', True)]

        return UploadPreviewResponse(
            upload_id=upload.id,
            total_rows=upload.total_rows,
            valid_rows=upload.valid_rows,
            invalid_rows=upload.invalid_rows,
            validation_errors=upload.validation_errors,
            sample_data=sample_data,
            detected_columns=upload.processed_data.get('column_info', {}).get('all', [])
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing upload {upload_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to preview upload data"
        )

@router.delete("/{upload_id}")
async def delete_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Delete upload and associated file"""
    try:
        upload = await db.get(Upload, upload_id)
        if not upload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload not found"
            )

        # Delete from S3 if S3 key exists
        if upload.s3_key:
            await file_upload_service.delete_s3_file(upload.s3_key)

        # Delete from database
        await db.delete(upload)
        await db.commit()

        logger.info(f"Deleted upload: {upload.original_filename}")

        return {"message": "Upload deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting upload {upload_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete upload"
        )

@router.get("/{upload_id}/download-url")
async def get_download_url(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get download URL for uploaded file"""
    try:
        upload = await db.get(Upload, upload_id)
        if not upload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload not found"
            )

        if not upload.s3_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File not available for download"
            )

        # Generate presigned URL
        download_url = await file_upload_service.get_s3_file_url(upload.s3_key, expiration=3600)

        if not download_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate download URL"
            )

        return {
            "download_url": download_url,
            "filename": upload.original_filename,
            "expires_in": 3600
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting download URL for upload {upload_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate download URL"
        )

@router.post("/{upload_id}/validate-emails")
async def validate_upload_emails(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Validate emails in uploaded data"""
    try:
        upload = await db.get(Upload, upload_id)
        if not upload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload not found"
            )

        if not upload.processed_data or 'rows' not in upload.processed_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No data available for validation"
            )

        # Extract emails from processed data
        rows = upload.processed_data['rows']
        emails = []

        for row in rows:
            if row.get('data', {}).get('email'):
                emails.append(row['data']['email'])

        if not emails:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No emails found in uploaded data"
            )

        # Initialize verification service
        from services.verification_service import EmailVerificationService
        verification_service = EmailVerificationService(redis_manager.redis)

        # Validate emails
        validation_result = await verification_service.verify_bulk_emails(emails)

        # Update upload with validation results
        upload.processed_data['email_validation'] = validation_result
        await db.commit()

        logger.info(f"Validated {len(emails)} emails for upload {upload_id}")

        return {
            "success": True,
            "total_emails": len(emails),
            "valid_emails": validation_result['valid'],
            "invalid_emails": validation_result['invalid'],
            "risky_emails": validation_result['risky'],
            "results": validation_result['results'][:10]  # Return first 10 results as sample
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating emails for upload {upload_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate emails"
        )

@router.get("/{upload_id}/stats")
async def get_upload_stats(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_admin)
):
    """Get statistics for uploaded data"""
    try:
        upload = await db.get(Upload, upload_id)
        if not upload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload not found"
            )

        stats = {
            "upload_id": upload.id,
            "filename": upload.original_filename,
            "file_size": upload.file_size,
            "file_type": upload.file_type,
            "status": upload.status.value,
            "total_rows": upload.total_rows,
            "valid_rows": upload.valid_rows,
            "invalid_rows": upload.invalid_rows,
            "validation_rate": (upload.valid_rows / upload.total_rows * 100) if upload.total_rows > 0 else 0,
            "created_at": upload.created_at,
            "updated_at": upload.updated_at
        }

        # Add column information if available
        if upload.processed_data and 'column_info' in upload.processed_data:
            column_info = upload.processed_data['column_info']
            stats['columns'] = {
                'total': len(column_info.get('all', [])),
                'required': len(column_info.get('required', [])),
                'optional': len(column_info.get('optional', [])),
                'all_columns': column_info.get('all', [])
            }

        # Add validation errors if any
        if upload.validation_errors:
            stats['has_validation_errors'] = True
            stats['validation_error_count'] = len(upload.validation_errors.get('errors', []))
        else:
            stats['has_validation_errors'] = False
            stats['validation_error_count'] = 0

        # Add email validation stats if available
        if upload.processed_data and 'email_validation' in upload.processed_data:
            email_validation = upload.processed_data['email_validation']
            stats['email_validation'] = {
                'total': email_validation.get('total', 0),
                'valid': email_validation.get('valid', 0),
                'invalid': email_validation.get('invalid', 0),
                'risky': email_validation.get('risky', 0),
                'validation_rate': (email_validation.get('valid', 0) / email_validation.get('total', 1)) * 100
            }

        return stats

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting upload stats {upload_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get upload statistics"
        )