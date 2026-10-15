import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

from fastapi import HTTPException, Response, status

from core.config import get_settings
from core.security import create_access_token, get_password_hash, verify_password
from schemas.auth import (
    LoginRequest,
    RegisterRequest,
    ResendOTPRequest,
    VerifyOTPRequest,
    TokenResponse,
)


settings = get_settings()

user_db: Dict[str, Dict[str, Any]] = {}
otp_store: Dict[str, Dict[str, Any]] = {}
OTP_TTL_SECONDS = 300
RESEND_COOLDOWN_SECONDS = 45


def _generate_otp() -> str:
    return f"{random.randint(100000, 999999)}"


def _send_otp(email: str, otp: str) -> None:
    if settings.DEBUG:
        print(f"OTP for {email}: {otp}")


def _store_otp(email: str, otp: str) -> None:
    now = datetime.now(timezone.utc)
    otp_store[email] = {
        "code": otp,
        "expires_at": now + timedelta(seconds=OTP_TTL_SECONDS),
        "last_sent_at": now,
    }


def _get_pending_otp(email: str) -> Dict[str, Any] | None:
    return otp_store.get(email)


def _create_auth_tokens(email: str) -> Tuple[str, str]:
    access_token = create_access_token(
        data={"sub": email},
        expires_delta=timedelta(minutes=settings.JWT.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_access_token(
        data={"sub": email, "type": "refresh"},
        expires_delta=timedelta(days=settings.JWT.RESFRESH_TOKEN_EXPIRE_DAYS),
    )
    return access_token, refresh_token


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=int(settings.JWT.ACCESS_TOKEN_EXPIRE_MINUTES) * 60,
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


def register_user(payload: RegisterRequest) -> TokenResponse:
    if payload.email in user_db:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already exists",
        )

    user_db[payload.email] = {
        "email": payload.email,
        "password_hash": get_password_hash(payload.password),
        "is_verified": False,
    }

    otp = _generate_otp()
    _store_otp(payload.email, otp)
    _send_otp(payload.email, otp)

    return TokenResponse(message="User registered successfully. OTP sent to email.")


def login_user(payload: LoginRequest) -> TokenResponse:
    user = user_db.get(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    otp = _generate_otp()
    _store_otp(payload.email, otp)
    _send_otp(payload.email, otp)

    return TokenResponse(message="OTP sent to email.")


def verify_otp(payload: VerifyOTPRequest, response: Response) -> TokenResponse:
    user = user_db.get(payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    pending = _get_pending_otp(payload.email)
    if not pending or pending["code"] != payload.otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )

    if pending["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP expired",
        )

    user["is_verified"] = True
    access_token, refresh_token = _create_auth_tokens(payload.email)
    _set_auth_cookies(response, access_token, refresh_token)

    return TokenResponse(message="OTP verified successfully.")


def resend_otp(payload: ResendOTPRequest) -> TokenResponse:
    user = user_db.get(payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    pending = _get_pending_otp(payload.email)
    if pending:
        elapsed = (datetime.now(timezone.utc) - pending["last_sent_at"]).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {int(RESEND_COOLDOWN_SECONDS - elapsed)} seconds before resending OTP.",
            )

    otp = _generate_otp()
    _store_otp(payload.email, otp)
    _send_otp(payload.email, otp)

    return TokenResponse(message="OTP resent successfully.")
