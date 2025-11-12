# main.py
# Enhanced FastAPI app for bulk email and form automation platform
# Install deps: pip install -r requirements.txt
# Development: uvicorn main:app --reload
# Production: uvicorn main:app --host 0.0.0.0 --port $PORT

import os
import logging
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Body, HTTPException, BackgroundTasks, Response, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic_settings import BaseSettings
from pydantic import EmailStr, ValidationError
from email_validator import validate_email, EmailNotValidError
from gmail_service import GmailService

# Import new modules
from database import init_db, close_db, redis_manager
from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service, limiter
from routes import auth, forms, submissions
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -----------------------------
# Settings using environment variables
# -----------------------------
class Settings(BaseSettings):
    # Legacy SMTP settings (for backward compatibility)
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    ADMIN_EMAIL: EmailStr = "admin@example.com"
    FROM_EMAIL: EmailStr = "noreply@example.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    # New service settings
    DATABASE_URL: str = "postgresql://username:password@localhost:5432/email_automation"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ADMIN_PASSWORD: str = "admin123"  # Should be changed in production

    # Email service settings
    EMAIL_SERVICE: str = "hybrid"  # gmail, resend, hybrid
    RESEND_API_KEY: Optional[str] = None

    # File upload settings
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    S3_BUCKET_NAME: Optional[str] = None
    AWS_DEFAULT_REGION: str = "us-east-1"

    # Rate limiting settings
    RATE_LIMIT_PER_MINUTE: int = 60
    BULK_EMAIL_RATE_LIMIT: int = 100

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()

# Initialize Gmail service for backward compatibility
gmail_service = GmailService()

# Initialize FastAPI app
app = FastAPI(
    title="Email Automation Platform",
    description="Comprehensive bulk email and form automation platform with templates, campaigns, and analytics",
    version="2.0.0",
    root_path="/",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add rate limiting exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize database and Redis connections"""
    try:
        # Initialize Redis
        await redis_manager.init_redis()

        # Initialize services that need Redis
        auth_service.redis_client = redis_manager.redis
        rate_limit_service.redis_client = redis_manager.redis

        # Initialize database (create tables if they don't exist)
        # Note: In production, you should use Alembic migrations
        # await init_db()

        logging.info("Application startup completed successfully")

    except Exception as e:
        logging.error(f"Error during startup: {str(e)}")
        # Continue startup even if database/Redis fails
        # The app will work with limited functionality

@app.on_event("shutdown")
async def shutdown_event():
    """Close database and Redis connections"""
    try:
        await close_db()
        logging.info("Application shutdown completed successfully")
    except Exception as e:
        logging.error(f"Error during shutdown: {str(e)}")

# Include new API routes
app.include_router(auth.router)
app.include_router(forms.router)
app.include_router(submissions.router)

# Legacy form submission endpoints for backward compatibility
def format_payload(payload: Dict[str, Any]) -> str:
    """
    Format dynamic payload into 'field: value' per line.
    Works even if nested dict/list values are present -> simple str().
    """
    lines = []
    for k, v in payload.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)

async def send_admin_email(subject: str, body: str) -> None:
    """
    Send the submission details to ADMIN_EMAIL using Gmail API.
    """
    try:
        result = gmail_service.send_email(
            to_email=settings.ADMIN_EMAIL,
            subject=subject,
            body=body,
            from_email=settings.FROM_EMAIL
        )
        if result["status"] == "error":
            logging.error(f"Failed to send admin email: {result['message']}")
    except Exception as e:
        logging.exception(f"Error in send_admin_email: {str(e)}")
        raise

async def send_autoreply(to_email: str) -> None:
    """Send auto-reply email using Gmail API."""
    try:
        subject = "We've received your submission"
        body = ("Thank you for contacting us. We have received your form submission "
                "and will get back to you soon.\n\n— Team")

        result = gmail_service.send_email(
            to_email=to_email,
            subject=subject,
            body=body,
            from_email=settings.FROM_EMAIL
        )

        if result["status"] == "error":
            logging.error(f"Failed to send auto-reply: {result['message']}")
    except Exception as e:
        logging.exception(f"Error in send_autoreply: {str(e)}")
        raise

@app.get("/submit-form")
async def get_submit_form():
    """Handle GET requests to /submit-form"""
    return {
        "status": "error",
        "message": "Please use POST method to submit the form"
    }

@app.post("/submit-form")
@app.options("/submit-form", include_in_schema=False)  # For CORS preflight
async def submit_form(
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = Body(..., media_type="application/json")
):
    """
    Legacy form submission endpoint for backward compatibility.
    Accept arbitrary JSON payload, email admin, and optionally auto-reply.
    """
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be a JSON object")

        logging.info("Received legacy submission: %s", payload)

        formatted = format_payload(payload)
        subject = "New form submission"

        # Send admin email in background (non-blocking)
        async def admin_send():
            try:
                await send_admin_email(subject=subject, body=formatted)
                logging.info("Admin email sent successfully.")
            except Exception as e:
                logging.exception("Failed to send admin email: %s", e)
                # If needed, here you can write to persistent logs or retry queue.

        background_tasks.add_task(admin_send)

        # Auto-reply if there's a valid 'email' field
        email_value = payload.get("email") or payload.get("Email") or payload.get("user_email")
        if email_value:
            try:
                # validate_email returns normalized email if valid
                valid = validate_email(email_value)
                valid_email = valid.email
                async def autoreply_send():
                    try:
                        await send_autoreply(valid_email)
                        logging.info("Auto-reply sent to %s", valid_email)
                    except Exception as e:
                        logging.exception("Failed to send auto-reply: %s", e)
                background_tasks.add_task(autoreply_send)
            except EmailNotValidError as e:
                logging.warning("Invalid email address provided: %s - %s", email_value, str(e))

        return {"status": "success", "message": "Form submitted successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        error_msg = str(e)
        logging.exception("Unexpected error in /submit-form: %s", error_msg)
        return {"status": "error", "message": error_msg}

# Health check and system endpoints
@app.get("/")
async def root():
    """
    Root endpoint to check if the API is running
    """
    return {
        "status": "ok",
        "message": "Email Automation Platform is running 🚀",
        "version": "2.0.0",
        "documentation": "/docs",
        "legacy_submit_endpoint": "/submit-form",
        "new_submit_endpoint": "/api/submit/{form_id}"
    }

@app.get("/health")
async def health_check():
    """
    Detailed health check endpoint
    """
    try:
        health_status = {
            "status": "healthy",
            "services": {
                "database": "unknown",
                "redis": "unknown",
                "gmail": "unknown",
                "resend": "unknown"
            },
            "configuration": {
                "email_service": settings.EMAIL_SERVICE,
                "admin_password_set": bool(settings.ADMIN_PASSWORD and settings.ADMIN_PASSWORD != "admin123"),
                "database_configured": settings.DATABASE_URL != "postgresql://username:password@localhost:5432/email_automation",
                "redis_configured": settings.REDIS_URL != "redis://localhost:6379/0"
            }
        }

        # Check services if available
        try:
            from services.email_service import email_service
            provider_health = await email_service.get_provider_health()
            health_status["services"]["gmail"] = "available" if provider_health["gmail"]["available"] else "unavailable"
            health_status["services"]["resend"] = "available" if provider_health["resend"]["available"] else "unavailable"
        except Exception as e:
            logging.warning(f"Email service health check failed: {str(e)}")

        # Check Redis
        if redis_manager.redis:
            try:
                await redis_manager.redis.ping()
                health_status["services"]["redis"] = "available"
            except Exception:
                health_status["services"]["redis"] = "unavailable"

        # Check database (basic connection test)
        try:
            from database import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                await session.execute("SELECT 1")
                health_status["services"]["database"] = "available"
        except Exception as e:
            logging.warning(f"Database health check failed: {str(e)}")
            health_status["services"]["database"] = "unavailable"

        return health_status

    except Exception as e:
        logging.error(f"Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Handle favicon.ico requests to prevent 404 errors
    """
    return Response(status_code=204)

# Add configuration endpoint for debugging
@app.get("/config")
async def get_config():
    """
    Get current configuration (for debugging)
    """
    return {
        "email_service": settings.EMAIL_SERVICE,
        "admin_email": settings.ADMIN_EMAIL,
        "from_email": settings.FROM_EMAIL,
        "database_url_configured": settings.DATABASE_URL != "postgresql://username:password@localhost:5432/email_automation",
        "redis_url_configured": settings.REDIS_URL != "redis://localhost:6379/0",
        "s3_configured": bool(settings.S3_BUCKET_NAME),
        "resend_configured": bool(settings.RESEND_API_KEY),
        "admin_password_default": settings.ADMIN_PASSWORD == "admin123"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True if os.getenv("ENVIRONMENT") == "development" else False
    )