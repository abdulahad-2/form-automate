import os
import logging
from typing import Dict, Any, Optional, List
from enum import Enum
import resend
from gmail_service import GmailService
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Template

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailProvider(str, Enum):
    GMAIL = "gmail"
    RESEND = "resend"
    HYBRID = "hybrid"

class EmailType(str, Enum):
    TRANSACTIONAL = "transactional"
    BULK = "bulk"
    AUTORESPONSE = "autoresponse"

class EmailService:
    def __init__(self):
        self.gmail_service = None
        self.resend_api_key = os.getenv("RESEND_API_KEY")
        self.email_service = os.getenv("EMAIL_SERVICE", "hybrid")

        # Initialize Gmail service if needed
        if self.email_service in [EmailProvider.GMAIL, EmailProvider.HYBRID]:
            try:
                self.gmail_service = GmailService()
                logger.info("Gmail service initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Gmail service: {e}")
                if self.email_service == EmailProvider.GMAIL:
                    raise
                else:
                    logger.warning("Gmail service failed, continuing with Resend only")

        # Initialize Resend if needed
        if self.email_service in [EmailProvider.RESEND, EmailProvider.HYBRID]:
            if not self.resend_api_key:
                logger.warning("RESEND_API_KEY not found in environment variables")
                if self.email_service == EmailProvider.RESEND:
                    raise ValueError("RESEND_API_KEY is required for Resend service")
            else:
                resend.api_key = self.resend_api_key
                logger.info("Resend service initialized successfully")

    def _choose_provider(self, email_type: EmailType, recipient_count: int = 1) -> EmailProvider:
        """Choose the appropriate email provider based on type and configuration"""
        if self.email_service == EmailProvider.GMAIL:
            return EmailProvider.GMAIL
        elif self.email_service == EmailProvider.RESEND:
            return EmailProvider.RESEND
        elif self.email_service == EmailProvider.HYBRID:
            # Use Gmail for transactional/autoresponse, Resend for bulk
            if email_type in [EmailType.TRANSACTIONAL, EmailType.AUTORESPONSE]:
                return EmailProvider.GMAIL if self.gmail_service else EmailProvider.RESEND
            elif email_type == EmailType.BULK or recipient_count > 10:
                return EmailProvider.RESEND
            else:
                return EmailProvider.GMAIL if self.gmail_service else EmailProvider.RESEND
        else:
            raise ValueError(f"Invalid email service configuration: {self.email_service}")

    async def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        from_email: Optional[str] = None,
        email_type: EmailType = EmailType.TRANSACTIONAL,
        html_content: Optional[str] = None,
        template_data: Optional[Dict[str, Any]] = None,
        reply_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send an email using the appropriate provider

        Args:
            to_email: Recipient email address
            subject: Email subject
            content: Plain text content
            from_email: Sender email (optional)
            email_type: Type of email (transactional, bulk, autoresponse)
            html_content: HTML content (optional)
            template_data: Data for template rendering
            reply_to: Reply-to email address

        Returns:
            Dict with status and message/response data
        """
        try:
            # Process template if provided
            if template_data:
                content = self._render_template(content, template_data)
                if html_content:
                    html_content = self._render_template(html_content, template_data)

            # Choose provider
            provider = self._choose_provider(email_type)
            logger.info(f"Using {provider} to send email to {to_email}")

            # Send via chosen provider
            if provider == EmailProvider.GMAIL:
                return await self._send_via_gmail(
                    to_email, subject, content, from_email, html_content, reply_to
                )
            elif provider == EmailProvider.RESEND:
                return await self._send_via_resend(
                    to_email, subject, content, from_email, html_content, reply_to
                )

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return {
                "status": "error",
                "message": f"Failed to send email: {str(e)}",
                "provider": provider if 'provider' in locals() else "unknown"
            }

    async def _send_via_gmail(
        self,
        to_email: str,
        subject: str,
        content: str,
        from_email: Optional[str] = None,
        html_content: Optional[str] = None,
        reply_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send email via Gmail API"""
        if not self.gmail_service:
            raise RuntimeError("Gmail service not available")

        # For Gmail, we'll use plain text for now
        # HTML support would require additional MIME handling
        result = self.gmail_service.send_email(
            to_email=to_email,
            subject=subject,
            body=content,
            from_email=from_email
        )

        if result["status"] == "success":
            return {
                "status": "success",
                "message_id": result["message_id"],
                "provider": "gmail"
            }
        else:
            return {
                "status": "error",
                "message": result["message"],
                "provider": "gmail"
            }

    async def _send_via_resend(
        self,
        to_email: str,
        subject: str,
        content: str,
        from_email: Optional[str] = None,
        html_content: Optional[str] = None,
        reply_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send email via Resend API"""
        params = {
            "to": [to_email],
            "subject": subject,
        }

        # Set sender email
        default_from = os.getenv("FROM_EMAIL", "noreply@example.com")
        params["from"] = from_email or default_from

        # Add content
        if html_content:
            params["html"] = html_content
            # Also include text version
            params["text"] = content
        else:
            params["text"] = content

        # Add reply-to if provided
        if reply_to:
            params["reply_to"] = reply_to

        try:
            result = resend.Emails.send(params)
            return {
                "status": "success",
                "message_id": result["id"],
                "provider": "resend",
                "response": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "provider": "resend"
            }

    async def send_bulk_emails(
        self,
        recipients: List[Dict[str, Any]],
        subject: str,
        content: str,
        from_email: Optional[str] = None,
        html_content: Optional[str] = None,
        template_data: Optional[Dict[str, Any]] = None,
        batch_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Send bulk emails to multiple recipients

        Args:
            recipients: List of recipient dicts with 'email' and optional template variables
            subject: Email subject
            content: Email content (can include template variables)
            from_email: Sender email
            html_content: HTML content
            template_data: Base template data
            batch_size: Number of emails to send in parallel

        Returns:
            List of results for each recipient
        """
        results = []

        # Process in batches to avoid rate limits
        for i in range(0, len(recipients), batch_size):
            batch = recipients[i:i + batch_size]

            for recipient in batch:
                try:
                    # Merge base template data with recipient-specific data
                    recipient_data = template_data.copy() if template_data else {}
                    recipient_data.update(recipient.get('data', {}))

                    result = await self.send_email(
                        to_email=recipient['email'],
                        subject=subject,
                        content=content,
                        from_email=from_email,
                        html_content=html_content,
                        email_type=EmailType.BULK,
                        template_data=recipient_data
                    )

                    result['recipient'] = recipient['email']
                    results.append(result)

                except Exception as e:
                    logger.error(f"Failed to send bulk email to {recipient.get('email', 'unknown')}: {str(e)}")
                    results.append({
                        "status": "error",
                        "message": str(e),
                        "recipient": recipient.get('email', 'unknown'),
                        "provider": "bulk"
                    })

        return results

    def _render_template(self, template_str: str, data: Dict[str, Any]) -> str:
        """Render template string with provided data using Jinja2"""
        try:
            template = Template(template_str)
            return template.render(**data)
        except Exception as e:
            logger.error(f"Template rendering error: {str(e)}")
            # Return original string if template fails
            return template_str

    async def get_provider_health(self) -> Dict[str, Any]:
        """Check the health of all configured email providers"""
        health_status = {
            "gmail": {
                "available": False,
                "error": None
            },
            "resend": {
                "available": False,
                "error": None
            },
            "primary_service": self.email_service
        }

        # Check Gmail
        if self.gmail_service:
            try:
                # Simple test - try to initialize the service again
                gmail_test = GmailService()
                health_status["gmail"]["available"] = True
            except Exception as e:
                health_status["gmail"]["error"] = str(e)

        # Check Resend
        if self.resend_api_key:
            try:
                # Simple API call to test connectivity
                # This is a basic check - you might want to implement a more sophisticated health check
                health_status["resend"]["available"] = True
            except Exception as e:
                health_status["resend"]["error"] = str(e)

        return health_status

# Global email service instance
email_service = EmailService()