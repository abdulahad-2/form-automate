# Form Automate

A FastAPI-based form submission handler that uses Gmail API to send form submissions to an admin email and auto-responds to submitters.

## Features

- Accepts any JSON payload
- Uses Gmail API for reliable email delivery
- Sends form submissions to admin email
- Auto-responds to submitters with a confirmation email
- Production-ready configuration
- Easy deployment to Render

## Local Development

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up Gmail API:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project
   - Enable Gmail API
   - Create OAuth 2.0 credentials (OAuth client ID)
   - Download the credentials as `credentials.json` and place it in the project root

4. Generate a token:
   ```bash
   python -c "from gmail_service import GmailService; GmailService()"
   ```
   - This will open a browser window for authentication
   - After authenticating, a `token.pickle` file will be created

5. Copy `.env.example` to `.env` and update with your email settings
4. Run the development server:
   ```bash
   uvicorn main:app --reload
   ```
5. Access the test form at `frontend/index.html`

## Deployment to Render

### Method 1: Using render.yaml (Recommended)

1. Push your code to a GitHub repository
2. Go to [Render Dashboard](https://dashboard.render.com/)
3. Click "New +" and select "Blueprint"
4. Connect your repository
5. Select the repository and click "Apply"
6. Set the following environment variables in the Render dashboard:
   - `ADMIN_EMAIL`: Email to receive form submissions
   - `FROM_EMAIL`: Sender email (must be a Gmail address)
   - `GMAIL_CREDENTIALS`: The content of your `credentials.json` file
   - `GMAIL_TOKEN`: The content of your `token.pickle` file (base64 encoded)

   To get the base64 encoded token, run:
   ```bash
   python -c "import base64; print(base64.b64encode(open('token.pickle', 'rb').read()).decode('utf-8'))"
   ```

### Method 2: Manual Deployment

1. Create a new Web Service on Render
2. Connect your GitHub repository
3. Set the following:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Set the environment variables as listed above

## Frontend Hosting

The frontend can be hosted separately on services like Netlify, Vercel, or GitHub Pages. Just update the `backendUrl` in `frontend/index.html` to point to your Render backend URL.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SMTP_SERVER` | Yes | SMTP server address |
| `SMTP_PORT` | Yes | SMTP port (e.g., 587 for TLS) |
| `SMTP_USER` | Yes | SMTP username/email |
| `SMTP_PASSWORD` | Yes | SMTP password or app password |
| `ADMIN_EMAIL` | Yes | Admin email to receive submissions |
| `FROM_EMAIL` | Yes | Sender email (should match SMTP_USER) |
| `MAIL_STARTTLS` | No | Enable STARTTLS (default: true) |
| `MAIL_SSL_TLS` | No | Use SSL/TLS (default: false) |

## CORS Configuration

By default, CORS is enabled for all origins. To restrict access, modify the `allow_origins` list in `main.py`.

## License

MIT
=======
# form-automate
>>>>>>> 75650b2e91b07b3d309db32c452747daffe1179f
