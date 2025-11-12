import os
import logging
import dns.resolver
import re
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import redis.asyncio as redis
from email_validator import validate_email, EmailNotValidError
from models.verification import EmailVerification, VerificationStatus

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailVerificationService:
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis_client = redis_client
        self.cache_ttl = 3600  # 1 hour cache TTL

        # List of known disposable email domains
        self.disposable_domains = {
            '10minutemail.com', '20minutemail.com', 'guerrillamail.com',
            'mailinator.com', 'yopmail.com', 'tempmail.org', 'tempmail.com',
            'throwaway.email', 'maildrop.cc', 'fakemail.io', 'mailnull.com',
            'incognitomail.com', 'spambox.info', 'temp-mail.org', 'maildu.de',
            'nowmymail.com', 'mailme.ir', 'mailnesia.com', 'trashmail.com',
            'mailcatch.com', 'zebins.com', 'yogamaven.com', 'mailinator2.com',
            'deadaddress.com', 'reallymymail.com', 'mailsac.com', 'temp-mail.io'
        }

        # List of known webmail providers
        self.webmail_domains = {
            'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'aol.com',
            'icloud.com', 'protonmail.com', 'tutanota.com', 'zoho.com', 'mail.com'
        }

    async def verify_email(self, email: str, force_verify: bool = False) -> Dict[str, Any]:
        """
        Comprehensive email verification including syntax, domain, MX records,
        and disposable detection

        Args:
            email: Email address to verify
            force_verify: Skip cache and force verification

        Returns:
            Dict with verification results
        """
        try:
            # Check cache first unless forced
            if not force_verify and self.redis_client:
                cached_result = await self._get_cached_result(email)
                if cached_result:
                    logger.info(f"Using cached verification result for {email}")
                    return cached_result

            # Start verification process
            result = {
                'email': email,
                'status': VerificationStatus.UNKNOWN,
                'is_valid_syntax': False,
                'has_mx_record': False,
                'is_disposable': False,
                'is_webmail': False,
                'domain': '',
                'errors': [],
                'verified_at': None
            }

            # Step 1: Syntax validation
            syntax_result = await self._verify_syntax(email)
            result.update(syntax_result)

            if not result['is_valid_syntax']:
                result['status'] = VerificationStatus.INVALID
                await self._cache_result(email, result)
                return result

            # Step 2: Domain extraction and checks
            domain = result['domain']

            # Step 3: Disposable email detection
            result['is_disposable'] = await self._is_disposable_email(domain)

            # Step 4: Webmail detection
            result['is_webmail'] = await self._is_webmail_email(domain)

            # Step 5: MX record verification
            mx_result = await self._verify_mx_record(domain)
            result.update(mx_result)

            # Step 6: Determine final status
            result['status'] = self._determine_status(result)
            result['verified_at'] = datetime.utcnow()

            # Cache the result
            if self.redis_client:
                await self._cache_result(email, result)

            logger.info(f"Email verification completed for {email}: {result['status']}")
            return result

        except Exception as e:
            logger.error(f"Error verifying email {email}: {str(e)}")
            return {
                'email': email,
                'status': VerificationStatus.UNKNOWN,
                'is_valid_syntax': False,
                'has_mx_record': False,
                'is_disposable': False,
                'is_webmail': False,
                'domain': '',
                'errors': [str(e)],
                'verified_at': None
            }

    async def verify_bulk_emails(self, emails: List[str], force_verify: bool = False) -> Dict[str, Any]:
        """
        Verify multiple emails in batch

        Args:
            emails: List of email addresses to verify
            force_verify: Skip cache and force verification

        Returns:
            Dict with aggregated results
        """
        results = []

        # Verify emails concurrently
        tasks = [self.verify_email(email, force_verify) for email in emails]
        verified_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        valid_count = 0
        invalid_count = 0
        risky_count = 0
        unknown_count = 0

        for i, result in enumerate(verified_results):
            if isinstance(result, Exception):
                logger.error(f"Error verifying email {emails[i]}: {str(result)}")
                results.append({
                    'email': emails[i],
                    'status': VerificationStatus.UNKNOWN,
                    'error': str(result)
                })
                unknown_count += 1
            else:
                results.append(result)

                if result['status'] == VerificationStatus.VALID:
                    valid_count += 1
                elif result['status'] == VerificationStatus.INVALID:
                    invalid_count += 1
                elif result['status'] == VerificationStatus.RISKY:
                    risky_count += 1
                else:
                    unknown_count += 1

        return {
            'total': len(emails),
            'valid': valid_count,
            'invalid': invalid_count,
            'risky': risky_count,
            'unknown': unknown_count,
            'results': results
        }

    async def _verify_syntax(self, email: str) -> Dict[str, Any]:
        """Verify email syntax using email-validator"""
        try:
            validated = validate_email(email)
            return {
                'is_valid_syntax': True,
                'domain': validated.domain.lower(),
                'normalized_email': validated.email
            }
        except EmailNotValidError as e:
            return {
                'is_valid_syntax': False,
                'domain': '',
                'errors': [str(e)]
            }

    async def _verify_mx_record(self, domain: str) -> Dict[str, Any]:
        """Verify MX records for the domain"""
        try:
            # Use a timeout to avoid hanging
            mx_records = dns.resolver.resolve(domain, 'MX', lifetime=5)
            return {
                'has_mx_record': True,
                'mx_records': [str(mx) for mx in mx_records]
            }
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.Timeout):
            return {
                'has_mx_record': False,
                'mx_records': []
            }
        except Exception as e:
            logger.error(f"Error checking MX records for {domain}: {str(e)}")
            return {
                'has_mx_record': False,
                'mx_records': [],
                'errors': [str(e)]
            }

    async def _is_disposable_email(self, domain: str) -> bool:
        """Check if email domain is known disposable email provider"""
        domain = domain.lower()

        # Direct match
        if domain in self.disposable_domains:
            return True

        # Subdomain match
        for disposable_domain in self.disposable_domains:
            if domain.endswith(f'.{disposable_domain}'):
                return True

        return False

    async def _is_webmail_email(self, domain: str) -> bool:
        """Check if email domain is known webmail provider"""
        domain = domain.lower()

        # Direct match
        if domain in self.webmail_domains:
            return True

        # Subdomain match for some webmail providers
        for webmail_domain in self.webmail_domains:
            if domain.endswith(f'.{webmail_domain}'):
                return True

        return False

    def _determine_status(self, result: Dict[str, Any]) -> VerificationStatus:
        """Determine final verification status based on all checks"""
        if not result['is_valid_syntax']:
            return VerificationStatus.INVALID

        if result['is_disposable']:
            return VerificationStatus.RISKY

        if not result['has_mx_record']:
            return VerificationStatus.INVALID

        # If it has valid syntax and MX records, it's considered valid
        # even if it's a webmail provider
        return VerificationStatus.VALID

    async def _get_cached_result(self, email: str) -> Optional[Dict[str, Any]]:
        """Get cached verification result from Redis"""
        if not self.redis_client:
            return None

        try:
            cache_key = f"email_verification:{email.lower()}"
            cached_data = await self.redis_client.hgetall(cache_key)

            if cached_data:
                # Convert cached strings back to appropriate types
                result = {
                    'email': email,
                    'status': cached_data.get('status', VerificationStatus.UNKNOWN),
                    'is_valid_syntax': cached_data.get('is_valid_syntax', 'false').lower() == 'true',
                    'has_mx_record': cached_data.get('has_mx_record', 'false').lower() == 'true',
                    'is_disposable': cached_data.get('is_disposable', 'false').lower() == 'true',
                    'is_webmail': cached_data.get('is_webmail', 'false').lower() == 'true',
                    'domain': cached_data.get('domain', ''),
                    'verified_at': cached_data.get('verified_at')
                }

                # Parse verified_at if it exists
                if result['verified_at']:
                    try:
                        result['verified_at'] = datetime.fromisoformat(result['verified_at'])
                    except ValueError:
                        result['verified_at'] = None

                return result

        except Exception as e:
            logger.error(f"Error getting cached verification result for {email}: {str(e)}")

        return None

    async def _cache_result(self, email: str, result: Dict[str, Any]) -> None:
        """Cache verification result in Redis"""
        if not self.redis_client:
            return

        try:
            cache_key = f"email_verification:{email.lower()}"

            # Prepare data for Redis storage
            cache_data = {
                'status': result['status'],
                'is_valid_syntax': str(result['is_valid_syntax']),
                'has_mx_record': str(result['has_mx_record']),
                'is_disposable': str(result['is_disposable']),
                'is_webmail': str(result['is_webmail']),
                'domain': result['domain']
            }

            if result['verified_at']:
                cache_data['verified_at'] = result['verified_at'].isoformat()

            # Store in Redis hash with TTL
            await self.redis_client.hset(cache_key, mapping=cache_data)
            await self.redis_client.expire(cache_key, self.cache_ttl)

        except Exception as e:
            logger.error(f"Error caching verification result for {email}: {str(e)}")

    async def clear_cache(self, email: Optional[str] = None) -> None:
        """Clear cached verification results"""
        if not self.redis_client:
            return

        try:
            if email:
                # Clear specific email cache
                cache_key = f"email_verification:{email.lower()}"
                await self.redis_client.delete(cache_key)
                logger.info(f"Cleared cache for {email}")
            else:
                # Clear all email verification caches
                pattern = "email_verification:*"
                keys = await self.redis_client.keys(pattern)
                if keys:
                    await self.redis_client.delete(*keys)
                    logger.info(f"Cleared {len(keys)} email verification caches")

        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")

    def get_verification_stats(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get statistics from verification results"""
        stats = {
            'total': len(results),
            'valid': 0,
            'invalid': 0,
            'risky': 0,
            'unknown': 0,
            'domains': {},
            'disposable_count': 0,
            'webmail_count': 0,
            'syntax_errors': 0,
            'mx_errors': 0
        }

        for result in results:
            # Count by status
            status = result.get('status', VerificationStatus.UNKNOWN)
            if status == VerificationStatus.VALID:
                stats['valid'] += 1
            elif status == VerificationStatus.INVALID:
                stats['invalid'] += 1
            elif status == VerificationStatus.RISKY:
                stats['risky'] += 1
            else:
                stats['unknown'] += 1

            # Count domains
            domain = result.get('domain', '')
            if domain:
                stats['domains'][domain] = stats['domains'].get(domain, 0) + 1

            # Count special categories
            if result.get('is_disposable'):
                stats['disposable_count'] += 1
            if result.get('is_webmail'):
                stats['webmail_count'] += 1
            if not result.get('is_valid_syntax'):
                stats['syntax_errors'] += 1
            if not result.get('has_mx_record'):
                stats['mx_errors'] += 1

        return stats