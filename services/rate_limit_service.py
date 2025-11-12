import os
import logging
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import redis.asyncio as redis
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RateLimitService:
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis_client = redis_client

        # Rate limiting configuration from environment
        self.rate_limits = {
            'form_submission': {
                'requests': int(os.getenv("FORM_SUBMISSION_LIMIT", "5")),
                'window': int(os.getenv("FORM_SUBMISSION_WINDOW", "60"))  # seconds
            },
            'email_sending': {
                'requests': int(os.getenv("EMAIL_SENDING_LIMIT", "100")),
                'window': int(os.getenv("EMAIL_SENDING_WINDOW", "3600"))  # 1 hour
            },
            'bulk_email': {
                'requests': int(os.getenv("BULK_EMAIL_LIMIT", "1000")),
                'window': int(os.getenv("BULK_EMAIL_WINDOW", "86400"))  # 24 hours
            },
            'file_upload': {
                'requests': int(os.getenv("FILE_UPLOAD_LIMIT", "10")),
                'window': int(os.getenv("FILE_UPLOAD_WINDOW", "3600"))  # 1 hour
            },
            'api_auth': {
                'requests': int(os.getenv("API_AUTH_LIMIT", "20")),
                'window': int(os.getenv("API_AUTH_WINDOW", "900"))  # 15 minutes
            },
            'general_api': {
                'requests': int(os.getenv("GENERAL_API_LIMIT", "1000")),
                'window': int(os.getenv("GENERAL_API_WINDOW", "3600"))  # 1 hour
            }
        }

        # Suspicious activity thresholds
        self.suspicious_thresholds = {
            'multiple_ips_same_email': int(os.getenv("MULTIPLE_IPS_THRESHOLD", "3")),
            'high_frequency_submissions': int(os.getenv("HIGH_FREQUENCY_THRESHOLD", "20")),
            'failed_attempts_limit': int(os.getenv("FAILED_ATTEMPTS_LIMIT", "10"))
        }

    async def is_rate_limited(
        self,
        key: str,
        limit_type: str,
        identifier: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check if request is rate limited

        Args:
            key: Rate limiting key (e.g., IP address, email)
            limit_type: Type of rate limit to check
            identifier: Optional additional identifier

        Returns:
            Dict with rate limiting status
        """
        if not self.redis_client:
            # If Redis is not available, allow all requests
            return {
                'allowed': True,
                'limit': None,
                'remaining': None,
                'reset_time': None,
                'error': None
            }

        try:
            if limit_type not in self.rate_limits:
                logger.warning(f"Unknown rate limit type: {limit_type}")
                return {
                    'allowed': True,
                    'error': f"Unknown rate limit type: {limit_type}"
                }

            config = self.rate_limits[limit_type]
            rate_key = f"rate_limit:{limit_type}:{key}"

            if identifier:
                rate_key += f":{identifier}"

            # Check current count
            current_count = await self.redis_client.get(rate_key)
            current_count = int(current_count) if current_count else 0

            if current_count >= config['requests']:
                # Rate limit exceeded
                ttl = await self.redis_client.ttl(rate_key)
                reset_time = datetime.utcnow() + timedelta(seconds=ttl) if ttl > 0 else None

                # Log rate limit exceeded
                logger.warning(
                    f"Rate limit exceeded for {limit_type}: {key} "
                    f"({current_count}/{config['requests']})"
                )

                return {
                    'allowed': False,
                    'limit': config['requests'],
                    'window': config['window'],
                    'current': current_count,
                    'remaining': 0,
                    'reset_time': reset_time,
                    'retry_after': ttl,
                    'error': f"Rate limit exceeded: {config['requests']} requests per {config['window']} seconds"
                }

            # Increment counter
            pipe = self.redis_client.pipeline()
            pipe.incr(rate_key)
            pipe.expire(rate_key, config['window'])
            await pipe.execute()

            # Get updated count
            new_count = current_count + 1
            remaining = max(0, config['requests'] - new_count)

            return {
                'allowed': True,
                'limit': config['requests'],
                'window': config['window'],
                'current': new_count,
                'remaining': remaining,
                'reset_time': datetime.utcnow() + timedelta(seconds=config['window']),
                'error': None
            }

        except Exception as e:
            logger.error(f"Rate limiting error for {key}: {str(e)}")
            # Allow request if rate limiting fails
            return {
                'allowed': True,
                'error': f"Rate limiting error: {str(e)}"
            }

    async def check_suspicious_activity(
        self,
        ip_address: str,
        email: Optional[str] = None,
        action_type: str = 'form_submission'
    ) -> Dict[str, Any]:
        """
        Check for suspicious activity patterns

        Args:
            ip_address: Client IP address
            email: Email address (if available)
            action_type: Type of action being performed

        Returns:
            Dict with suspicious activity analysis
        """
        if not self.redis_client:
            return {'suspicious': False, 'reason': [], 'blocked': False}

        try:
            suspicious_indicators = []
            should_block = False

            # Check for multiple IPs using same email
            if email:
                ip_key = f"email_ips:{email}"
                existing_ips = await self.redis_client.smembers(ip_key)

                if len(existing_ips) >= self.suspicious_thresholds['multiple_ips_same_email']:
                    suspicious_indicators.append(f"Multiple IPs using email: {email}")
                    if len(existing_ips) >= self.suspicious_thresholds['multiple_ips_same_email'] * 2:
                        should_block = True

                # Add current IP to set
                await self.redis_client.sadd(ip_key, ip_address)
                await self.redis_client.expire(ip_key, 3600)  # 1 hour

            # Check for high frequency submissions from same IP
            freq_key = f"ip_frequency:{ip_address}:{action_type}"
            freq_count = await self.redis_client.incr(freq_key)
            await self.redis_client.expire(freq_key, 300)  # 5 minutes

            if freq_count >= self.suspicious_thresholds['high_frequency_submissions']:
                suspicious_indicators.append(f"High frequency submissions from IP: {ip_address}")
                should_block = True

            # Check failed authentication attempts
            if action_type == 'auth':
                fail_key = f"auth_failures:{ip_address}"
                fail_count = await self.redis_client.incr(fail_key)
                await self.redis_client.expire(fail_key, 900)  # 15 minutes

                if fail_count >= self.suspicious_thresholds['failed_attempts_limit']:
                    suspicious_indicators.append(f"Multiple failed auth attempts from IP: {ip_address}")
                    should_block = True

            # Check for known malicious IPs (you could integrate with threat intelligence)
            malicious_key = f"malicious_ip:{ip_address}"
            is_malicious = await self.redis_client.get(malicious_key)
            if is_malicious:
                suspicious_indicators.append("IP flagged as malicious")
                should_block = True

            # Log suspicious activity
            if suspicious_indicators:
                logger.warning(
                    f"Suspicious activity detected from {ip_address}: {', '.join(suspicious_indicators)}"
                )

                # Store suspicious activity record
                activity_key = f"suspicious_activity:{datetime.utcnow().strftime('%Y%m%d')}"
                activity_data = {
                    'ip': ip_address,
                    'email': email or 'N/A',
                    'action_type': action_type,
                    'indicators': json.dumps(suspicious_indicators),
                    'timestamp': datetime.utcnow().isoformat()
                }

                await self.redis_client.lpush(activity_key, json.dumps(activity_data))
                await self.redis_client.expire(activity_key, 86400 * 7)  # Keep for 7 days

            return {
                'suspicious': len(suspicious_indicators) > 0,
                'indicators': suspicious_indicators,
                'blocked': should_block,
                'score': min(100, len(suspicious_indicators) * 25)  # Simple scoring
            }

        except Exception as e:
            logger.error(f"Error checking suspicious activity: {str(e)}")
            return {'suspicious': False, 'error': str(e), 'blocked': False}

    async def block_ip_temporarily(self, ip_address: str, duration: int = 3600, reason: str = "Suspicious activity") -> bool:
        """
        Temporarily block an IP address

        Args:
            ip_address: IP address to block
            duration: Block duration in seconds (default: 1 hour)
            reason: Reason for blocking

        Returns:
            True if successful
        """
        if not self.redis_client:
            return False

        try:
            block_key = f"blocked_ip:{ip_address}"
            block_data = {
                'reason': reason,
                'blocked_at': datetime.utcnow().isoformat(),
                'duration': duration
            }

            await self.redis_client.hset(block_key, mapping=block_data)
            await self.redis_client.expire(block_key, duration)

            logger.info(f"IP {ip_address} blocked for {duration} seconds: {reason}")
            return True

        except Exception as e:
            logger.error(f"Error blocking IP {ip_address}: {str(e)}")
            return False

    async def is_ip_blocked(self, ip_address: str) -> Dict[str, Any]:
        """
        Check if an IP is currently blocked

        Args:
            ip_address: IP address to check

        Returns:
            Dict with block status and details
        """
        if not self.redis_client:
            return {'blocked': False}

        try:
            block_key = f"blocked_ip:{ip_address}"
            block_data = await self.redis_client.hgetall(block_key)

            if block_data:
                ttl = await self.redis_client.ttl(block_key)
                return {
                    'blocked': True,
                    'reason': block_data.get('reason', 'Unknown'),
                    'blocked_at': block_data.get('blocked_at'),
                    'remaining_seconds': ttl,
                    'duration': block_data.get('duration')
                }

            return {'blocked': False}

        except Exception as e:
            logger.error(f"Error checking IP block status: {str(e)}")
            return {'blocked': False, 'error': str(e)}

    async def get_rate_limit_stats(self) -> Dict[str, Any]:
        """Get rate limiting statistics"""
        if not self.redis_client:
            return {}

        try:
            stats = {}

            # Get current rate limit counts
            for limit_type in self.rate_limits.keys():
                pattern = f"rate_limit:{limit_type}:*"
                keys = await self.redis_client.keys(pattern)

                total_requests = 0
                for key in keys:
                    count = await self.redis_client.get(key)
                    if count:
                        total_requests += int(count)

                stats[limit_type] = {
                    'active_keys': len(keys),
                    'total_requests': total_requests,
                    'config': self.rate_limits[limit_type]
                }

            # Get blocked IPs
            blocked_pattern = "blocked_ip:*"
            blocked_keys = await self.redis_client.keys(blocked_pattern)
            stats['blocked_ips'] = len(blocked_keys)

            # Get suspicious activity count
            today = datetime.utcnow().strftime('%Y%m%d')
            activity_key = f"suspicious_activity:{today}"
            activity_count = await self.redis_client.llen(activity_key)
            stats['suspicious_activities_today'] = activity_count

            return stats

        except Exception as e:
            logger.error(f"Error getting rate limit stats: {str(e)}")
            return {'error': str(e)}

    async def clear_rate_limits(self, pattern: Optional[str] = None) -> int:
        """
        Clear rate limit data

        Args:
            pattern: Pattern to match (if None, clears all rate limits)

        Returns:
            Number of keys cleared
        """
        if not self.redis_client:
            return 0

        try:
            if pattern:
                search_pattern = f"rate_limit:{pattern}"
            else:
                search_pattern = "rate_limit:*"

            keys = await self.redis_client.keys(search_pattern)
            if keys:
                await self.redis_client.delete(*keys)
                logger.info(f"Cleared {len(keys)} rate limit keys")
                return len(keys)

            return 0

        except Exception as e:
            logger.error(f"Error clearing rate limits: {str(e)}")
            return 0

# Global rate limit service instance
rate_limit_service = RateLimitService()

# Create SlowAPI limiter instance
def get_identifier(request):
    """Get identifier for rate limiting"""
    # Use IP address as default identifier
    return get_remote_address(request)

# Initialize limiter
limiter = Limiter(key_func=get_identifier)