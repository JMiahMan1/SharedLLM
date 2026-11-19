"""Email settings and notifications API."""
from __future__ import annotations

import aiohttp
import logging
import smtplib
from datetime import datetime, UTC
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.gateway.config import CONTROL_PLANE_URL, IDENTITY_SVC, INTERNAL_SECRET
from services.gateway.shared import shared_http_client, secret_matches, log

router = APIRouter(prefix="/api/email", tags=["email"])


class EmailSettings(BaseModel):
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_email: str
    use_tls: bool = True
    to_email: str | None = None


class TestEmailRequest(BaseModel):
    user_id: str | None = None  # defaults to authenticated user


class SendEmailRequest(BaseModel):
    user_id: str
    template: str
    context: dict


@router.get("/settings")
async def get_email_settings(request: Request):
    """Get current user's email settings (password masked)."""
    from services.gateway.main import _resolve_identity_from_request
    
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(401, "Unauthorized")
    
    user_id = creds.get("user") or creds.get("id")
    if not user_id:
        raise HTTPException(400, "Could not determine user")
    
    # Try to fetch from Identity
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/users/{user_id}/email-settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5),
            )
            if resp.status == 200:
                settings = await resp.json()
                # Mask password
                if "password" in settings:
                    settings["password"] = "***"
                return {"settings": settings}
            elif resp.status == 404:
                return {"settings": None}
    except Exception as e:
        log.error(f"Failed to fetch email settings: {e}")
    
    return {"settings": None}


@router.post("/settings")
async def save_email_settings(body: EmailSettings, request: Request):
    """Save email settings (tests connection first)."""
    from services.gateway.main import _resolve_identity_from_request
    
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(401, "Unauthorized")
    
    user_id = creds.get("user") or creds.get("id")
    if not user_id:
        raise HTTPException(400, "Could not determine user")
    
    # Test SMTP connection
    try:
        import smtplib
        def _test():
            server = smtplib.SMTP(body.smtp_host, body.smtp_port, timeout=10)
            if body.use_tls:
                server.starttls()
            server.login(body.username, body.password)
            server.quit()
            return True
        
        import asyncio
        await asyncio.to_thread(_test)
    except Exception as e:
        raise HTTPException(400, f"SMTP test failed: {e}")
    
    # Save to Identity
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{user_id}/email-settings",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            json=body.model_dump(),
            timeout=aiohttp.ClientTimeout(total=10),
        )
        if resp.status != 200:
            text = await resp.text()
            raise HTTPException(500, f"Failed to save settings: {text}")
        return await resp.json()


@router.post("/test")
async def send_test_email(body: TestEmailRequest, request: Request):
    """Send a test email to verify configuration."""
    from services.gateway.main import _resolve_identity_from_request
    
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(401, "Unauthorized")
    
    user_id = body.user_id or creds.get("user") or creds.get("id")
    if not user_id:
        raise HTTPException(400, "Could not determine user")
    
    # Send test email via email service
    try:
        async with shared_http_client() as client:
            resp = await client.post(
                f"{CONTROL_PLANE_URL}/api/email/send",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json={
                    "user_id": user_id,
                    "template": "device_arrival",
                    "context": {
                        "name": "Test Device",
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "ip": "192.168.1.100",
                        "hostname": "test-device",
                        "device_type": "phone",
                        "time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    },
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            if resp.status == 200:
                return {"success": True, "message": "Test email sent"}
            else:
                text = await resp.text()
                raise HTTPException(500, f"Failed to send test email: {text}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to send test email: {e}")


@router.post("/send")
async def send_email(body: SendEmailRequest, request: Request):
    """Send an email using a template (for automation/webhooks)."""
    # Check internal secret for service-to-service calls
    from services.gateway.main import secret_matches
    
    internal_secret = request.headers.get("X-Internal-Secret")
    if not secret_matches(internal_secret, INTERNAL_SECRET):
        raise HTTPException(401, "Invalid internal secret")
    
    try:
        async with shared_http_client() as client:
            resp = await client.post(
                f"{CONTROL_PLANE_URL}/api/email/send",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json=body.model_dump(),
                timeout=aiohttp.ClientTimeout(total=30),
            )
            if resp.status == 200:
                return await resp.json()
            else:
                text = await resp.text()
                raise HTTPException(500, f"Failed to send email: {text}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to send email: {e}")