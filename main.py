# main.py
# FastAPI app to receive ANY form JSON, email admin, and auto-reply the submitter if 'email' exists.
# Install deps: pip install -r requirements.txt
# Development: uvicorn main:app --reload
# Production: uvicorn main:app --host 0.0.0.0 --port $PORT

import os
import logging
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Body, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
from pydantic import EmailStr, ValidationError
from email_validator import validate_email, EmailNotValidError
from gmail_service import GmailService

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -----------------------------
# Settings using environment variables
# -----------------------------
class Settings(BaseSettings):
    SMTP_SERVER: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASSWORD: str
    ADMIN_EMAIL: EmailStr
    FROM_EMAIL: EmailStr
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()  # will raise if required env vars missing

# Initialize Gmail service
gmail_service = GmailService()

app = FastAPI(
    title="Universal Form Collector",
    description="A production-ready form submission handler with email notifications",
    version="1.0.0",
    root_path="/"  # Add this line to handle root path properly
)

# CORS configuration
# In production, you might want to restrict this to specific origins
# by setting the ALLOWED_ORIGINS environment variable
# e.g., ALLOWED_ORIGINS=https://yourdomain.com,https://yourotherdomain.com
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

fm = FastMail(conf)

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
    Accept arbitrary JSON payload, email admin, and optionally auto-reply.
    """
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be a JSON object")

        logging.info("Received submission: %s", payload)

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


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
async def root():
    """
    Root endpoint to check if the API is running
    """
    return {
        "status": "ok",
        "message": "Form Automate API is running 🚀",
        "documentation": "/docs",
        "submit_endpoint": "/submit-form"
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Handle favicon.ico requests to prevent 404 errors
    """
    return Response(status_code=204)
