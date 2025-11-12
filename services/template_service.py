import os
import logging
import re
from typing import Dict, Any, List, Optional, Tuple, Set
from jinja2 import Template, TemplateSyntaxError, meta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
import html

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TemplateService:
    def __init__(self):
        self.built_in_variables = {
            'name': 'Recipient name',
            'email': 'Recipient email address',
            'date': 'Current date',
            'time': 'Current time',
            'company': 'Company name (if provided)',
            'phone': 'Phone number (if provided)',
            'message': 'Custom message (if provided)'
        }

    def extract_variables(self, template_str: str) -> List[str]:
        """
        Extract all template variables from a template string

        Args:
            template_str: Template string with {{variable}} placeholders

        Returns:
            List of variable names found in the template
        """
        variables = set()

        # Find {{variable}} patterns using regex
        pattern = r'\{\{\s*([^}]+)\s*\}\}'
        matches = re.findall(pattern, template_str)

        for match in matches:
            # Clean up the variable name (remove filters, whitespace)
            var_name = match.split('|')[0].strip()
            variables.add(var_name)

        # Also try Jinja2 parsing for more complex templates
        try:
            jinja_template = Template(template_str)
            if hasattr(jinja_template, 'environment'):
                jinja_vars = meta.find_undeclared_variables(
                    jinja_template.environment.parse(template_str)
                )
                variables.update(jinja_vars)
        except Exception as e:
            logger.warning(f"Jinja2 parsing failed, using regex only: {str(e)}")

        return sorted(list(variables))

    def render_template(
        self,
        template_str: str,
        data: Dict[str, Any],
        strict: bool = False,
        autoescape: bool = True
    ) -> Dict[str, Any]:
        """
        Render a template with provided data

        Args:
            template_str: Template string with Jinja2 syntax
            data: Dictionary of variables to substitute
            strict: If True, raise error for missing variables
            autoescape: Enable HTML autoescaping

        Returns:
            Dict with rendered content and metadata
        """
        try:
            # Create Jinja2 template
            template = Template(template_str, autoescape=autoescape)

            # Add built-in variables if not provided
            enriched_data = self._enrich_data_with_builtins(data)

            # Render template
            if strict:
                rendered_content = template.render(**enriched_data)
            else:
                # Use undefined handling for missing variables
                rendered_content = template.render(**enriched_data)

            # Extract used variables
            used_variables = self.extract_variables(template_str)

            return {
                'success': True,
                'rendered_content': rendered_content,
                'used_variables': used_variables,
                'provided_variables': list(enriched_data.keys()),
                'missing_variables': list(set(used_variables) - set(enriched_data.keys())),
                'warnings': []
            }

        except TemplateSyntaxError as e:
            return {
                'success': False,
                'error': f'Template syntax error: {str(e)}',
                'line': e.lineno,
                'rendered_content': template_str,
                'used_variables': [],
                'provided_variables': list(data.keys()),
                'missing_variables': [],
                'warnings': []
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'Template rendering error: {str(e)}',
                'rendered_content': template_str,
                'used_variables': self.extract_variables(template_str),
                'provided_variables': list(data.keys()),
                'missing_variables': [],
                'warnings': []
            }

    def preview_template(
        self,
        template_str: str,
        sample_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Preview template with sample data

        Args:
            template_str: Template string to preview
            sample_data: Optional sample data for rendering

        Returns:
            Dict with preview information
        """
        # Extract variables
        variables = self.extract_variables(template_str)

        # Generate sample data if not provided
        if not sample_data:
            sample_data = self._generate_sample_data(variables)

        # Render template with sample data
        render_result = self.render_template(template_str, sample_data)

        # Add preview-specific information
        render_result.update({
            'template_variables': variables,
            'sample_data': sample_data,
            'variable_descriptions': {var: self.built_in_variables.get(var, 'Custom variable')
                                    for var in variables}
        })

        return render_result

    def validate_template(self, template_str: str) -> Dict[str, Any]:
        """
        Validate template syntax and structure

        Args:
            template_str: Template string to validate

        Returns:
            Dict with validation results
        """
        result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'variables': [],
            'has_html': False,
            'estimated_complexity': 'low'
        }

        try:
            # Check syntax
            template = Template(template_str)
            result['variables'] = self.extract_variables(template_str)

            # Check for HTML content
            if '<' in template_str and '>' in template_str:
                result['has_html'] = True

                # Basic HTML validation
                try:
                    soup = BeautifulSoup(template_str, 'html.parser')
                    if soup.find() is None:
                        result['warnings'].append('HTML appears to be malformed')
                except Exception as e:
                    result['warnings'].append(f'HTML parsing warning: {str(e)}')

            # Estimate complexity
            complexity_indicators = [
                len(result['variables']) > 5,  # Many variables
                '{%' in template_str,         # Control statements
                '{#' in template_str,         # Comments
                template_str.count('{') > 10, # Many substitutions
                result['has_html']           # HTML content
            ]

            complexity_score = sum(complexity_indicators)

            if complexity_score <= 1:
                result['estimated_complexity'] = 'low'
            elif complexity_score <= 3:
                result['estimated_complexity'] = 'medium'
            else:
                result['estimated_complexity'] = 'high'

            # Additional checks
            if not result['variables']:
                result['warnings'].append('No template variables found - template is static')

            if 'date' not in result['variables'] and '{{date}}' not in template_str.lower():
                result['warnings'].append('Consider adding date variable for personalization')

            # Check for potential issues
            if 'http://' in template_str or 'https://' in template_str:
                result['warnings'].append('Template contains URLs - ensure they are correct')

        except TemplateSyntaxError as e:
            result['valid'] = False
            result['errors'].append(f'Syntax error at line {e.lineno}: {str(e)}')

        except Exception as e:
            result['valid'] = False
            result['errors'].append(f'Validation error: {str(e)}')

        return result

    def create_email_from_template(
        self,
        subject_template: str,
        content_template: str,
        data: Dict[str, Any],
        from_email: Optional[str] = None,
        html_template: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a complete email message from templates

        Args:
            subject_template: Email subject template
            content_template: Plain text content template
            data: Data for template rendering
            from_email: Sender email address
            html_template: Optional HTML content template

        Returns:
            Dict with rendered email components and MIME message
        """
        try:
            # Render subject
            subject_result = self.render_template(subject_template, data)
            if not subject_result['success']:
                return {
                    'success': False,
                    'error': f'Subject template error: {subject_result["error"]}'
                }

            # Render plain text content
            content_result = self.render_template(content_template, data)
            if not content_result['success']:
                return {
                    'success': False,
                    'error': f'Content template error: {content_result["error"]}'
                }

            # Create email message
            if html_template:
                # Render HTML content
                html_result = self.render_template(html_template, data)
                if not html_result['success']:
                    return {
                        'success': False,
                        'error': f'HTML template error: {html_result["error"]}'
                    }

                # Create multipart message
                msg = MIMEMultipart('alternative')
                msg.attach(MIMEText(content_result['rendered_content'], 'plain'))
                msg.attach(MIMEText(html_result['rendered_content'], 'html'))
            else:
                # Create plain text message
                msg = MIMEText(content_result['rendered_content'], 'plain')

            # Set headers
            msg['Subject'] = subject_result['rendered_content']
            if from_email:
                msg['From'] = from_email

            return {
                'success': True,
                'subject': subject_result['rendered_content'],
                'text_content': content_result['rendered_content'],
                'html_content': html_result['rendered_content'] if html_template else None,
                'mime_message': msg,
                'used_variables': list(set(
                    subject_result['used_variables'] +
                    content_result['used_variables'] +
                    (html_result['used_variables'] if html_template else [])
                )),
                'warnings': (
                    subject_result['warnings'] +
                    content_result['warnings'] +
                    (html_result['warnings'] if html_template else [])
                )
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'Email creation error: {str(e)}'
            }

    def _enrich_data_with_builtins(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Add built-in variables to the data dictionary"""
        from datetime import datetime

        enriched_data = data.copy()

        # Add current date and time if not present
        if 'date' not in enriched_data:
            enriched_data['date'] = datetime.now().strftime('%Y-%m-%d')
        if 'time' not in enriched_data:
            enriched_data['time'] = datetime.now().strftime('%H:%M:%S')

        return enriched_data

    def _generate_sample_data(self, variables: List[str]) -> Dict[str, Any]:
        """Generate sample data for template variables"""
        sample_data = {}
        from datetime import datetime

        for var in variables:
            if var == 'name':
                sample_data[var] = 'John Doe'
            elif var == 'email':
                sample_data[var] = 'john.doe@example.com'
            elif var == 'date':
                sample_data[var] = datetime.now().strftime('%Y-%m-%d')
            elif var == 'time':
                sample_data[var] = datetime.now().strftime('%H:%M:%S')
            elif var == 'company':
                sample_data[var] = 'Acme Corporation'
            elif var == 'phone':
                sample_data[var] = '+1-555-0123'
            elif var == 'message':
                sample_data[var] = 'This is a sample message for template testing.'
            elif var == 'first_name':
                sample_data[var] = 'John'
            elif var == 'last_name':
                sample_data[var] = 'Doe'
            elif var == 'full_name':
                sample_data[var] = 'John Doe'
            elif var == 'website':
                sample_data[var] = 'https://example.com'
            elif var == 'address':
                sample_data[var] = '123 Main St, City, State 12345'
            elif var == 'amount':
                sample_data[var] = '$99.99'
            elif var == 'order_id':
                sample_data[var] = 'ORD-12345'
            elif var == 'product':
                sample_data[var] = 'Premium Widget'
            else:
                # Generate generic sample data
                sample_data[var] = f'Sample {var.replace("_", " ").title()}'

        return sample_data

    def get_template_suggestions(self, content_type: str = 'general') -> Dict[str, Any]:
        """Get template suggestions and examples"""
        suggestions = {
            'general': {
                'variables': ['name', 'email', 'date', 'company'],
                'subject_templates': [
                    'Hello {{name}}!',
                    'Important Update for {{company}}',
                    'Message from {{company}} - {{date}}'
                ],
                'content_templates': [
                    'Dear {{name}},\n\nThank you for your interest in {{company}}.\n\nBest regards,\nTeam',
                    'Hello {{name}},\n\nWe wanted to reach out regarding...\n\nSincerely,\n{{company}}'
                ]
            },
            'welcome': {
                'variables': ['name', 'email', 'company', 'date'],
                'subject_templates': [
                    'Welcome to {{company}}!',
                    'Your {{company}} Account is Ready',
                    'Getting Started with {{company}}'
                ],
                'content_templates': [
                    'Welcome {{name}}!\n\nThank you for joining {{company}}. We\'re excited to have you on board.\n\nBest regards,\nThe {{company}} Team'
                ]
            },
            'newsletter': {
                'variables': ['name', 'date', 'company'],
                'subject_templates': [
                    '{{company}} Newsletter - {{date}}',
                    'Weekly Update from {{company}}',
                    'Latest News from {{company}}'
                ],
                'content_templates': [
                    'Hi {{name}},\n\nHere\'s this week\'s newsletter from {{company}}...\n\nPublished on {{date}}'
                ]
            },
            'notification': {
                'variables': ['name', 'email', 'date', 'message'],
                'subject_templates': [
                    'Notification: {{message}}',
                    'Important Update for {{name}}',
                    'Alert from {{company}}'
                ],
                'content_templates': [
                    'Hello {{name}},\n\n{{message}}\n\nTimestamp: {{date}}'
                ]
            }
        }

        return suggestions.get(content_type, suggestions['general'])

# Global template service instance
template_service = TemplateService()