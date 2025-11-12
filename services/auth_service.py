import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status
import redis.asyncio as redis

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthService:
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis_client = redis_client
        self.secret_key = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        self.algorithm = "HS256"
        self.access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours
        self.admin_password = os.getenv("ADMIN_PASSWORD")
        self.session_timeout_hours = int(os.getenv("SESSION_TIMEOUT_HOURS", "24"))

        if not self.admin_password:
            raise ValueError("ADMIN_PASSWORD environment variable is required")

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        """Hash a password"""
        return pwd_context.hash(password)

    def authenticate_admin(self, password: str) -> Dict[str, Any]:
        """
        Authenticate admin user with password

        Args:
            password: Plain text password

        Returns:
            Dict with authentication result and token if successful
        """
        try:
            # For simplicity, we're using a single admin password from environment
            # In a more complex system, you might look up users from database
            if password == self.admin_password:
                # Create access token
                access_token = self.create_access_token(data={"sub": "admin", "type": "admin"})

                return {
                    "success": True,
                    "access_token": access_token,
                    "token_type": "bearer",
                    "expires_in": self.access_token_expire_minutes * 60
                }
            else:
                return {
                    "success": False,
                    "error": "Invalid password"
                }

        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return {
                "success": False,
                "error": "Authentication failed"
            }

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt

    async def verify_token(self, token: str) -> Dict[str, Any]:
        """Verify JWT token and extract payload"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            username: str = payload.get("sub")
            token_type: str = payload.get("type")

            if username is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Check if token is blacklisted (for logout functionality)
            if await self.is_token_blacklisted(token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            return {
                "valid": True,
                "username": username,
                "type": token_type,
                "payload": payload
            }

        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def blacklist_token(self, token: str) -> bool:
        """Add token to blacklist (for logout)"""
        if not self.redis_client:
            return False

        try:
            # Get token expiration time
            try:
                payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm], options={"verify_exp": False})
                exp_timestamp = payload.get("exp")

                if exp_timestamp:
                    # Calculate remaining time until expiration
                    remaining_time = exp_timestamp - datetime.utcnow().timestamp()
                    if remaining_time > 0:
                        # Add to blacklist with expiration
                        blacklist_key = f"blacklist_token:{token}"
                        await self.redis_client.setex(
                            blacklist_key,
                            int(remaining_time),
                            "1"
                        )
                        logger.info("Token added to blacklist")
                        return True

            except jwt.JWTError:
                logger.warning("Invalid token format for blacklisting")
                return False

        except Exception as e:
            logger.error(f"Error blacklisting token: {str(e)}")

        return False

    async def is_token_blacklisted(self, token: str) -> bool:
        """Check if token is blacklisted"""
        if not self.redis_client:
            return False

        try:
            blacklist_key = f"blacklist_token:{token}"
            result = await self.redis_client.get(blacklist_key)
            return result is not None

        except Exception as e:
            logger.error(f"Error checking token blacklist: {str(e)}")
            return False

    async def create_session(self, username: str, session_data: Dict[str, Any]) -> str:
        """Create user session in Redis"""
        if not self.redis_client:
            return ""

        try:
            session_id = f"session:{username}:{datetime.utcnow().timestamp()}"
            session_key = f"admin_session:{session_id}"

            # Store session data with expiration
            await self.redis_client.hset(session_key, mapping=session_data)
            await self.redis_client.expire(
                session_key,
                self.session_timeout_hours * 3600
            )

            return session_id

        except Exception as e:
            logger.error(f"Error creating session: {str(e)}")
            return ""

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data from Redis"""
        if not self.redis_client:
            return None

        try:
            session_key = f"admin_session:{session_id}"
            session_data = await self.redis_client.hgetall(session_key)

            if session_data:
                # Refresh expiration
                await self.redis_client.expire(
                    session_key,
                    self.session_timeout_hours * 3600
                )
                return session_data

            return None

        except Exception as e:
            logger.error(f"Error getting session: {str(e)}")
            return None

    async def delete_session(self, session_id: str) -> bool:
        """Delete session from Redis"""
        if not self.redis_client:
            return False

        try:
            session_key = f"admin_session:{session_id}"
            await self.redis_client.delete(session_key)
            return True

        except Exception as e:
            logger.error(f"Error deleting session: {str(e)}")
            return False

    async def get_active_sessions(self) -> list:
        """Get all active admin sessions"""
        if not self.redis_client:
            return []

        try:
            pattern = "admin_session:*"
            keys = await self.redis_client.keys(pattern)
            sessions = []

            for key in keys:
                session_data = await self.redis_client.hgetall(key)
                if session_data:
                    sessions.append({
                        'session_id': key.decode('utf-8').replace('admin_session:', ''),
                        'data': session_data
                    })

            return sessions

        except Exception as e:
            logger.error(f"Error getting active sessions: {str(e)}")
            return []

    def get_password_requirements(self) -> Dict[str, Any]:
        """Get password requirements for UI"""
        return {
            "min_length": 8,
            "require_uppercase": False,
            "require_lowercase": False,
            "require_numbers": False,
            "require_special": False,
            "description": "Admin password should be at least 8 characters long"
        }

    def is_admin_password_set(self) -> bool:
        """Check if admin password is configured"""
        return bool(self.admin_password and len(self.admin_password.strip()) > 0)

    async def update_admin_password(self, new_password: str) -> bool:
        """
        Update admin password (this would require environment variable update)
        Note: In a real application, you'd store this in database
        """
        try:
            # For this implementation, we can't update environment variables
            # In a real app, you'd update database record
            logger.info("Admin password update requested (requires environment variable change)")
            return False

        except Exception as e:
            logger.error(f"Error updating admin password: {str(e)}")
            return False

    async def get_auth_stats(self) -> Dict[str, Any]:
        """Get authentication statistics"""
        try:
            active_sessions = await self.get_active_sessions()

            return {
                "active_sessions": len(active_sessions),
                "admin_password_configured": self.is_admin_password_set(),
                "token_expiry_minutes": self.access_token_expire_minutes,
                "session_timeout_hours": self.session_timeout_hours,
                "sessions": active_sessions
            }

        except Exception as e:
            logger.error(f"Error getting auth stats: {str(e)}")
            return {
                "active_sessions": 0,
                "admin_password_configured": self.is_admin_password_set(),
                "token_expiry_minutes": self.access_token_expire_minutes,
                "session_timeout_hours": self.session_timeout_hours,
                "error": str(e)
            }

# Global auth service instance
auth_service = AuthService()