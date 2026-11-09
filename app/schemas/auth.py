from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(alias="refreshToken")


class LogoutRequest(BaseModel):
    refresh_token: str = Field(alias="refreshToken")


class DeleteAccountRequest(BaseModel):
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: str
    created_at: datetime = Field(alias="createdAt")


class TokenPair(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user: UserOut
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    expires_in: int = Field(alias="expiresIn")


class UsageOut(BaseModel):
    day: str
    uploads: int
    limit: int
