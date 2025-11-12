from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

from services.auth_service import auth_service
from services.rate_limit_service import rate_limit_service
from database import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["authentication"])
security = HTTPBearer()
limiter = Limiter(key_func=get_remote_address)

@router.post("/login")
async def login(
    request: Request,
    password: str
) -> Dict[str, Any]:
    """
    Admin login endpoint
    """
    try:
        client_ip = get_remote_address(request)

        # Check rate limiting
        rate_limit_result = await rate_limit_service.is_rate_limited(
            key=client_ip,
            limit_type="api_auth"
        )

        if not rate_limit_result['allowed']:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=rate_limit_result['error']
            )

        # Check for suspicious activity
        suspicious_result = await rate_limit_service.check_suspicious_activity(
            ip_address=client_ip,
            action_type="auth"
        )

        if suspicious_result['blocked']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access blocked due to suspicious activity"
            )

        # Authenticate
        auth_result = auth_service.authenticate_admin(password)

        if auth_result['success']:
            return auth_result
        else:
            # Log failed attempt (this would be tracked in suspicious activity check)
            logger.warning(f"Failed login attempt from {client_ip}")

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=auth_result['error']
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )

@router.post("/logout")
async def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    Logout endpoint - blacklists the token
    """
    try:
        token = credentials.credentials

        # Verify token first
        token_result = await auth_service.verify_token(token)
        if not token_result['valid']:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )

        # Blacklist token
        blacklist_result = await auth_service.blacklist_token(token)

        return {
            "success": True,
            "message": "Logged out successfully",
            "token_blacklisted": blacklist_result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Logout error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logout failed"
        )

@router.get("/status")
async def auth_status(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    Check authentication status
    """
    try:
        token = credentials.credentials

        # Verify token
        token_result = await auth_service.verify_token(token)
        if not token_result['valid']:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )

        return {
            "authenticated": True,
            "username": token_result['username'],
            "type": token_result['type'],
            "payload": token_result['payload']
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth status error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Status check failed"
        )

@router.get("/stats")
async def auth_stats(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    Get authentication statistics (admin only)
    """
    try:
        token = credentials.credentials

        # Verify token
        token_result = await auth_service.verify_token(token)
        if not token_result['valid'] or token_result['type'] != 'admin':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )

        # Get stats
        stats = await auth_service.get_auth_stats()

        return {
            "success": True,
            "stats": stats
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth stats error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get auth stats"
        )

@router.post("/verify-token")
async def verify_token_endpoint(
    token: str
) -> Dict[str, Any]:
    """
    Verify a JWT token (for testing/debugging)
    """
    try:
        result = await auth_service.verify_token(token)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token verification error: {str(e)}")
        return {
            "valid": False,
            "error": str(e)
        }

@router.get("/config")
async def auth_config() -> Dict[str, Any]:
    """
    Get authentication configuration
    """
    try:
        return {
            "password_requirements": auth_service.get_password_requirements(),
            "admin_password_set": auth_service.is_admin_password_set(),
            "rate_limits": rate_limit_service.rate_limits
        }

    except Exception as e:
        logger.error(f"Auth config error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get auth config"
        )