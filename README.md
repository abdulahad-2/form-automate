# Email Automation Platform

A comprehensive bulk email and form automation platform with templates, campaigns, and analytics. Transformed from a single-form FastAPI application into a full-featured email automation system.

## 🚀 Features

### 📧 **Email Management**
- **Hybrid Email Service**: Gmail API + Resend API for optimal delivery
- **Bulk Email Campaigns**: Upload CSV/Excel files and send personalized emails
- **Email Templates**: Dynamic templates with {{variable}} substitution
- **Email Verification**: Syntax validation, MX record checking, disposable detection
- **Delivery Tracking**: Real-time status monitoring and error handling

### 📝 **Form Management**
- **Multiple Form Endpoints**: Create unique form IDs (like Formspree)
- **Form Analytics**: Track submissions, sources, and field usage
- **Auto-responses**: Customizable email auto-responders
- **Embedding Code**: Generate HTML/JavaScript for form embedding

### 📊 **Analytics & Reporting**
- **Dashboard Overview**: Real-time statistics and activity monitoring
- **Campaign Analytics**: Delivery rates, domain performance, daily breakdowns
- **Form Analytics**: Submission trends, source tracking, field analysis
- **Email Performance**: Overall delivery metrics and success rates

### 🔒 **Security & Reliability**
- **Rate Limiting**: IP-based and user-based protection
- **Spam Prevention**: Disposable email detection, suspicious activity monitoring
- **Admin Authentication**: Secure password-protected admin area
- **Background Processing**: Non-blocking email sending

## 🏗️ Architecture

```
Frontend (Next.js) → FastAPI Backend → PostgreSQL Database
                                      ↓
                              Redis Cache & Rate Limiting
                                      ↓
                    Gmail API ← Email Service → Resend API
                                      ↓
                                   S3 Storage
```

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- PostgreSQL 12+
- Redis 6+
- Node.js 16+ (for frontend)

### Backend Setup

1. **Clone and install dependencies**
```bash
cd form-automate
pip install -r requirements.txt
```

2. **Set up environment variables**
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. **Set up database**
```bash
# Create PostgreSQL database
createdb email_automation

# Run migrations (when ready)
alembic upgrade head
```

4. **Configure Gmail API (if using Gmail)**
```bash
# Follow Gmail API setup instructions
# Generate credentials.json and token.pickle
```

5. **Start the server**
```bash
uvicorn main:app --reload
```

### 🔧 Environment Configuration

Key environment variables:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/email_automation
REDIS_URL=redis://localhost:6379/0

# Email Service
EMAIL_SERVICE=hybrid  # gmail, resend, hybrid
RESEND_API_KEY=your_resend_api_key
ADMIN_EMAIL=admin@example.com
FROM_EMAIL=noreply@example.com

# Gmail API
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GMAIL_TOKEN=base64_encoded_token

# File Storage (optional)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
S3_BUCKET_NAME=your-bucket-name

# Security
ADMIN_PASSWORD=your_secure_password
SECRET_KEY=your_jwt_secret_key
```

## 📚 API Documentation

Once running, visit:
- **Interactive Docs**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### Core Endpoints

#### Authentication
- `POST /api/auth/login` - Admin login
- `POST /api/auth/logout` - Logout
- `GET /api/auth/status` - Check authentication

#### Forms
- `POST /api/forms` - Create form endpoint
- `GET /api/forms` - List forms
- `GET /api/forms/{id}` - Get form details
- `GET /api/forms/{id}/embed-code` - Get embedding code

#### Templates
- `POST /api/templates` - Create email template
- `GET /api/templates` - List templates
- `POST /api/templates/{id}/preview` - Preview with data
- `POST /api/templates/{id}/test` - Send test email

#### Campaigns
- `POST /api/campaigns` - Create campaign (immediate sending)
- `GET /api/campaigns` - List campaigns
- `GET /api/campaigns/{id}/progress` - Real-time progress
- `POST /api/campaigns/{id}/pause` - Pause campaign

#### File Uploads
- `POST /api/upload/csv` - Upload CSV/Excel file
- `GET /api/upload/{id}/preview` - Preview uploaded data
- `POST /api/upload/{id}/validate-emails` - Verify emails

#### Analytics
- `GET /api/analytics/dashboard` - Dashboard statistics
- `GET /api/analytics/campaigns/{id}` - Campaign analytics
- `GET /api/analytics/forms/{id}` - Form analytics

## 💡 Usage Examples

### 1. Create an Email Template

```bash
curl -X POST "http://localhost:8000/api/templates" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Welcome Email",
    "subject": "Welcome {{name}}!",
    "content": "Hello {{name}},\n\nThank you for joining {{company}}.\n\nBest regards"
  }'
```

### 2. Upload CSV File

```bash
curl -X POST "http://localhost:8000/api/upload/csv" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contacts.csv"
```

### 3. Create Campaign

```bash
curl -X POST "http://localhost:8000/api/campaigns" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Newsletter Campaign",
    "template_id": "template-uuid",
    "upload_id": "upload-uuid"
  }'
```

### 4. Submit Form (Legacy Compatible)

```bash
curl -X POST "http://localhost:8000/submit-form" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "message": "Hello!"
  }'
```

### 5. Submit to Specific Form

```bash
curl -X POST "http://localhost:8000/api/submit/contact-form" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Jane Smith",
    "email": "jane@example.com",
    "company": "Acme Corp"
  }'
```

## 📄 CSV/Excel File Format

### Required Columns
- `name` (case-insensitive)
- `email` (case-insensitive)

### Optional Columns (become template variables)
- `company`
- `phone`
- `address`
- Any custom columns

### Example CSV
```csv
name,email,company,phone
John Doe,john@example.com,Acme Corp,555-0123
Jane Smith,jane@example.com,Tech Inc,555-0456
```

## 🎨 Template Variables

### Built-in Variables
- `{{name}}` - Recipient name
- `{{email}}` - Recipient email
- `{{date}}` - Current date
- `{{time}}` - Current time

### Custom Variables
From CSV columns or form submission data:
- `{{company}}` - From 'company' column
- `{{phone}}` - From 'phone' column
- Any column: `{{column_name}}`

### Template Example
```
Subject: Welcome to {{company}}!

Hello {{name}},

Thank you for your interest in {{company}}.
We've received your message from {{email}}.

Best regards,
{{company}} Team
```

## 🛠 Development

### Running Tests
```bash
pytest tests/
```

### Database Migrations
```bash
# Create new migration
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1
```

### Code Structure
```
form-automate/
├── main.py              # FastAPI application
├── database.py          # Database configuration
├── models/              # SQLAlchemy models
├── schemas/             # Pydantic schemas
├── services/            # Business logic
├── routes/              # API endpoints
├── alembic/             # Database migrations
└── requirements.txt     # Python dependencies
```

## 🚀 Production Deployment

### Environment Setup
1. Use PostgreSQL and Redis (managed services recommended)
2. Configure proper environment variables
3. Set up S3 bucket for file storage
4. Configure Gmail API and/or Resend API

### Security Considerations
- Change default admin password
- Use HTTPS in production
- Set up proper CORS origins
- Configure rate limiting
- Monitor for suspicious activity

### Performance
- Use connection pooling for database
- Configure Redis for caching
- Monitor email provider rate limits
- Use background tasks for email sending

## 📊 Monitoring

### Health Check
```bash
curl http://localhost:8000/health
```

### Application Logs
- Email delivery status
- Form submissions
- Error tracking
- Performance metrics

## 🆘 Support

### Common Issues

**Gmail API Authentication**
- Ensure OAuth2 credentials are properly set up
- Token must be base64 encoded
- Check Gmail API quota limits

**Database Connection**
- Verify PostgreSQL is running
- Check connection string format
- Ensure database exists

**Email Delivery**
- Verify sender email is configured
- Check recipient email validity
- Monitor rate limits

## 📄 License

This project transforms the original form automation system into a comprehensive email automation platform while maintaining backward compatibility.
