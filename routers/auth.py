from fastapi import APIRouter, Depends, HTTPException, Request, Response

from schemas.auth import (
    LoginRequest,
    RegisterRequest,
    ResendOTPRequest,
    TokenResponse,
    VerifyOTPRequest,
)
from services.auth_services import (
    login_user,
    register_user,
    resend_otp,
    verify_otp,
)

router = APIRouter(prefix="/auth")


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest) -> TokenResponse:
    return register_user(payload)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    return login_user(payload)


@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp_route(payload: VerifyOTPRequest, response: Response) -> TokenResponse:
    return verify_otp(payload, response)


@router.post("/resend-otp", response_model=TokenResponse)
def resend_otp_route(payload: ResendOTPRequest) -> TokenResponse:
    return resend_otp(payload)