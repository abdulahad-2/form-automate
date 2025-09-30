import os
import base64
import json
import pickle
import tempfile
from email.mime.text import MIMEText
from typing import Optional, Dict, Any
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import logging

# If modifying these scopes, delete the token.pickle file.
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

class GmailService:
    def __init__(self, credentials_path: str = 'credentials.json', token_path: str = 'token.pickle'):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        try:
            self.service = self._get_gmail_service()
        except Exception as e:
            logging.error(f"Failed to initialize GmailService: {str(e)}")
            raise

    def _get_gmail_service(self):
        """Get Gmail API service using OAuth 2.0 credentials."""
        creds = None
        
        # Check for token in environment variable (production)
        if 'GMAIL_TOKEN' in os.environ:
            try:
                logging.info("Loading Gmail token from environment variable")
                token_data = base64.b64decode(os.environ['GMAIL_TOKEN'])
                creds = pickle.loads(token_data)
                logging.info("Successfully loaded credentials from environment")
            except Exception as e:
                logging.error(f"Error loading token from environment: {str(e)}")
                raise Exception("Failed to load Gmail token from environment") from e
        # Check for token file (development)
        elif os.path.exists(self.token_path):
            try:
                logging.info(f"Loading Gmail token from {self.token_path}")
                with open(self.token_path, 'rb') as token:
                    creds = pickle.load(token)
                logging.info("Successfully loaded credentials from file")
            except Exception as e:
                logging.error(f"Error loading token file: {str(e)}")
                raise Exception(f"Failed to load token file: {str(e)}") from e

        # If there are no credentials available, raise an error in production
        if not creds:
            if 'GMAIL_TOKEN' in os.environ:
                raise Exception("Failed to load Gmail credentials from environment")
            else:
                raise Exception("No Gmail token found. Please generate a token first.")

        # Check if credentials are valid or can be refreshed
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    logging.info("Refreshing expired credentials")
                    creds.refresh(Request())
                    logging.info("Successfully refreshed credentials")
                except Exception as e:
                    logging.error(f"Error refreshing credentials: {str(e)}")
                    raise Exception("Failed to refresh Gmail credentials") from e
            else:
                raise Exception("Gmail credentials are invalid and cannot be refreshed")

        return build('gmail', 'v1', credentials=creds, static_discovery=False)

    def send_email(self, to_email: str, subject: str, body: str, from_email: Optional[str] = None) -> dict:
        """Send an email using the Gmail API.
        
        Args:
            to_email: Email address of the recipient
            subject: Email subject
            body: Email body (plain text)
            from_email: Optional sender email (must be the authenticated user)
            
        Returns:
            dict: The sent message
        """
        try:
            message = MIMEText(body)
            message['to'] = to_email
            message['subject'] = subject
            if from_email:
                message['from'] = from_email
                
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            message_body = {'raw': raw_message}
            
            sent_message = self.service.users().messages().send(
                userId='me',
                body=message_body
            ).execute()
            
            return {"status": "success", "message_id": sent_message['id']}
            
        except Exception as e:
            return {"status": "error", "message": str(e)}

# For testing
def test_send_email():
    gmail = GmailService()
    result = gmail.send_email(
        to_email="recipient@example.com",
        subject="Test Email from Gmail API",
        body="This is a test email sent via Gmail API.",
        from_email=None  # Will use the authenticated user's email
    )
    print(result)

if __name__ == "__main__":
    test_send_email()
