import os
import logging
import pandas as pd
import io
import re
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime
import uuid
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import hashlib

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FileProcessingError(Exception):
    """Custom exception for file processing errors"""
    pass

class FileUploadService:
    def __init__(self):
        self.s3_client = None
        self.s3_bucket_name = os.getenv("S3_BUCKET_NAME")
        self.allowed_extensions = {'.csv', '.xlsx', '.xls'}
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.required_columns = ['name', 'email']

        # Initialize S3 if configured
        if self.s3_bucket_name:
            try:
                self.s3_client = boto3.client(
                    's3',
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
                )
                logger.info("S3 client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize S3 client: {str(e)}")
                self.s3_client = None

    async def process_file_upload(
        self,
        file_content: bytes,
        filename: str,
        content_type: str
    ) -> Dict[str, Any]:
        """
        Process uploaded file (CSV/Excel) and validate structure

        Args:
            file_content: Raw file content
            filename: Original filename
            content_type: MIME type of the file

        Returns:
            Dict with processing results and data
        """
        try:
            # Validate file
            validation_result = self._validate_file(file_content, filename, content_type)
            if not validation_result['valid']:
                return {
                    'success': False,
                    'error': validation_result['error'],
                    'file_info': {
                        'filename': filename,
                        'size': len(file_content),
                        'type': content_type
                    }
                }

            # Process file based on type
            file_ext = self._get_file_extension(filename)

            if file_ext == '.csv':
                df = self._process_csv_file(file_content)
            elif file_ext in ['.xlsx', '.xls']:
                df = self._process_excel_file(file_content, file_ext)
            else:
                return {
                    'success': False,
                    'error': f"Unsupported file format: {file_ext}"
                }

            # Validate and clean data
            validation_result = self._validate_dataframe(df)
            if not validation_result['valid']:
                return {
                    'success': False,
                    'error': validation_result['error'],
                    'validation_errors': validation_result['errors'],
                    'file_info': {
                        'filename': filename,
                        'size': len(file_content),
                        'type': content_type
                    }
                }

            # Clean and standardize data
            cleaned_df = self._clean_dataframe(df)

            # Generate file info
            file_id = str(uuid.uuid4())
            s3_key = None

            # Upload to S3 if configured
            if self.s3_client and self.s3_bucket_name:
                s3_key = f"uploads/{file_id}/{filename}"
                s3_success = await self._upload_to_s3(file_content, s3_key, content_type)
                if not s3_success:
                    logger.warning(f"Failed to upload file to S3, continuing without S3 storage")

            # Prepare result
            processed_data = self._prepare_processed_data(cleaned_df)

            return {
                'success': True,
                'file_id': file_id,
                'filename': filename,
                'original_filename': filename,
                'file_size': len(file_content),
                'file_type': file_ext,
                's3_key': s3_key,
                'total_rows': len(processed_data['rows']),
                'valid_rows': len([r for r in processed_data['rows'] if r.get('valid', False)]),
                'invalid_rows': len([r for r in processed_data['rows'] if not r.get('valid', False)]),
                'processed_data': processed_data,
                'detected_columns': list(cleaned_df.columns),
                'sample_data': processed_data['rows'][:10],  # First 10 rows for preview
                'validation_errors': processed_data.get('validation_errors', [])
            }

        except Exception as e:
            logger.error(f"Error processing file {filename}: {str(e)}")
            return {
                'success': False,
                'error': f"File processing error: {str(e)}",
                'file_info': {
                    'filename': filename,
                    'size': len(file_content),
                    'type': content_type
                }
            }

    def _validate_file(
        self,
        file_content: bytes,
        filename: str,
        content_type: str
    ) -> Dict[str, Any]:
        """Validate file before processing"""
        # Check file size
        if len(file_content) > self.max_file_size:
            return {
                'valid': False,
                'error': f'File size ({len(file_content)} bytes) exceeds maximum allowed size ({self.max_file_size} bytes)'
            }

        # Check file extension
        file_ext = self._get_file_extension(filename)
        if file_ext not in self.allowed_extensions:
            return {
                'valid': False,
                'error': f'File type {file_ext} not allowed. Allowed types: {", ".join(self.allowed_extensions)}'
            }

        # Check content type
        allowed_types = [
            'text/csv',
            'application/csv',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ]
        if content_type not in allowed_types:
            logger.warning(f"Content type {content_type} may not match file extension {file_ext}")

        return {'valid': True}

    def _process_csv_file(self, file_content: bytes) -> pd.DataFrame:
        """Process CSV file content"""
        try:
            # Try different encodings
            encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']

            for encoding in encodings:
                try:
                    content_str = file_content.decode(encoding)
                    # Use StringIO to treat string as file
                    df = pd.read_csv(io.StringIO(content_str))
                    logger.info(f"Successfully processed CSV with encoding {encoding}")
                    return df
                except UnicodeDecodeError:
                    continue
                except Exception as e:
                    logger.warning(f"Error processing CSV with encoding {encoding}: {str(e)}")
                    continue

            # If all encodings fail, try with error handling
            content_str = file_content.decode('utf-8', errors='ignore')
            df = pd.read_csv(io.StringIO(content_str))
            logger.warning("Processed CSV with UTF-8 encoding (ignoring errors)")
            return df

        except Exception as e:
            raise FileProcessingError(f"Error processing CSV file: {str(e)}")

    def _process_excel_file(self, file_content: bytes, file_ext: str) -> pd.DataFrame:
        """Process Excel file content"""
        try:
            # Use BytesIO to treat bytes as file
            excel_file = io.BytesIO(file_content)

            if file_ext == '.xlsx':
                df = pd.read_excel(excel_file, engine='openpyxl')
            else:  # .xls
                df = pd.read_excel(excel_file, engine='xlrd')

            logger.info(f"Successfully processed Excel file: {file_ext}")
            return df

        except Exception as e:
            raise FileProcessingError(f"Error processing Excel file: {str(e)}")

    def _validate_dataframe(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Validate DataFrame structure and required columns"""
        errors = []

        # Check if DataFrame is empty
        if df.empty:
            errors.append("File contains no data")
            return {'valid': False, 'errors': errors}

        # Check for required columns (case-insensitive)
        df_columns_lower = [col.lower().strip() for col in df.columns]

        missing_columns = []
        for required_col in self.required_columns:
            if required_col not in df_columns_lower:
                missing_columns.append(required_col)

        if missing_columns:
            errors.append(f"Missing required columns: {', '.join(missing_columns)}")
            return {'valid': False, 'errors': errors}

        # Check for data in required columns
        for required_col in self.required_columns:
            # Find the actual column name (case-insensitive)
            actual_col = None
            for col in df.columns:
                if col.lower().strip() == required_col:
                    actual_col = col
                    break

            if actual_col and df[actual_col].isna().all():
                errors.append(f"Required column '{actual_col}' contains no data")

        return {
            'valid': len(errors) == 0,
            'errors': errors
        }

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean and standardize DataFrame"""
        # Make a copy to avoid modifying original
        cleaned_df = df.copy()

        # Standardize column names (lowercase, strip spaces)
        cleaned_df.columns = [col.lower().strip() for col in cleaned_df.columns]

        # Convert all values to strings and strip whitespace
        for col in cleaned_df.columns:
            cleaned_df[col] = cleaned_df[col].astype(str).str.strip()

        # Remove completely empty rows
        cleaned_df = cleaned_df.dropna(how='all')

        # Reset index
        cleaned_df = cleaned_df.reset_index(drop=True)

        return cleaned_df

    def _prepare_processed_data(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Prepare processed data for storage and validation"""
        rows = []
        validation_errors = []

        for index, row in df.iterrows():
            row_data = row.to_dict()
            row_validation = self._validate_row(row_data, index + 1)

            processed_row = {
                'row_number': index + 1,
                'data': row_data,
                'valid': row_validation['valid'],
                'errors': row_validation.get('errors', [])
            }

            rows.append(processed_row)

            if row_validation.get('errors'):
                validation_errors.extend([
                    f"Row {index + 1}: {error}"
                    for error in row_validation['errors']
                ])

        # Detect all available columns for template variables
        all_columns = list(df.columns)
        template_variables = [
            col for col in all_columns
            if col not in self.required_columns
        ]

        return {
            'rows': rows,
            'validation_errors': validation_errors,
            'template_variables': template_variables,
            'column_info': {
                'required': self.required_columns,
                'optional': template_variables,
                'all': all_columns
            }
        }

    def _validate_row(self, row_data: Dict[str, Any], row_number: int) -> Dict[str, Any]:
        """Validate individual row data"""
        errors = []
        valid = True

        # Validate email (required)
        email_value = row_data.get('email', '').strip()
        if not email_value:
            errors.append("Email is required")
            valid = False
        elif not self._is_valid_email_format(email_value):
            errors.append(f"Invalid email format: {email_value}")
            valid = False

        # Validate name (required)
        name_value = row_data.get('name', '').strip()
        if not name_value:
            errors.append("Name is required")
            valid = False

        return {
            'valid': valid,
            'errors': errors
        }

    def _is_valid_email_format(self, email: str) -> bool:
        """Basic email format validation"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    def _get_file_extension(self, filename: str) -> str:
        """Get file extension from filename"""
        return os.path.splitext(filename.lower())[1]

    async def _upload_to_s3(
        self,
        file_content: bytes,
        s3_key: str,
        content_type: str
    ) -> bool:
        """Upload file to S3"""
        if not self.s3_client or not self.s3_bucket_name:
            return False

        try:
            self.s3_client.put_object(
                Bucket=self.s3_bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType=content_type
            )
            logger.info(f"Successfully uploaded file to S3: {s3_key}")
            return True

        except (ClientError, NoCredentialsError) as e:
            logger.error(f"Failed to upload file to S3: {str(e)}")
            return False

    async def get_s3_file_url(self, s3_key: str, expiration: int = 3600) -> Optional[str]:
        """Generate presigned URL for S3 file"""
        if not self.s3_client or not self.s3_bucket_name:
            return None

        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.s3_bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            return url

        except Exception as e:
            logger.error(f"Failed to generate S3 presigned URL: {str(e)}")
            return None

    async def delete_s3_file(self, s3_key: str) -> bool:
        """Delete file from S3"""
        if not self.s3_client or not self.s3_bucket_name:
            return False

        try:
            self.s3_client.delete_object(
                Bucket=self.s3_bucket_name,
                Key=s3_key
            )
            logger.info(f"Successfully deleted file from S3: {s3_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete file from S3: {str(e)}")
            return False

    def generate_file_hash(self, file_content: bytes) -> str:
        """Generate SHA-256 hash of file content"""
        return hashlib.sha256(file_content).hexdigest()

    def get_file_info(self, file_content: bytes, filename: str) -> Dict[str, Any]:
        """Get basic file information"""
        return {
            'filename': filename,
            'size': len(file_content),
            'extension': self._get_file_extension(filename),
            'hash': self.generate_file_hash(file_content),
            'mime_type': self._guess_mime_type(filename)
        }

    def _guess_mime_type(self, filename: str) -> str:
        """Guess MIME type based on file extension"""
        ext = self._get_file_extension(filename)
        mime_types = {
            '.csv': 'text/csv',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel'
        }
        return mime_types.get(ext, 'application/octet-stream')

# Global file upload service instance
file_upload_service = FileUploadService()