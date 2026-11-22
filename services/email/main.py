"""Per-user email integration for notifications.

Each user can configure their own SMTP settings (stored encrypted in Identity).
Supports templates for common notifications (device arrival/departure, alerts).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
from datetime import datetime, UTC
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiohttp

from services.common.secrets import secret_matches
from services.config import (
    IDENTITY_SVC,
    INTERNAL_SECRET,
    REDIS_URL,
)

import redis.asyncio as redis

log = logging.getLogger("email.service")

EMAIL_SETTINGS_PREFIX = "email:settings:"
EMAIL_TEMPLATE_PREFIX = "email:template:"
TEMPLATE_TTL = 3600  # 1 hour cache for templates

# Default templates
DEFAULT_TEMPLATES = {
    "device_arrival": {
        "subject": "{name} arrived home",
        "text": """Hi there,

{name} ({mac}) just connected to the home network at {time}.

Device info:
- IP: {ip}
- Hostname: {hostname}
- Device type: {device_type}

This is an automated notification from your Jarvis home system.
""",
        "html": """<html><body>
<h2>{name} arrived home</h2>
<p>{name} ({mac}) just connected to the home network at {time}.</p>
<ul>
<li><strong>IP:</strong> {ip}</li>
<li><strong>Hostname:</strong> {hostname}</li>
<li><strong>Device type:</strong> {device_type}</li>
</ul>
<p>This is an automated notification from your Jarvis home system.</p>
</body></html>"""
    },
    "device_departure": {
        "subject": "{name} left home",
        "text": """Hi there,

{name} ({mac}) just disconnected from the home network at {time}.

Last known info:
- IP: {ip}
- Hostname: {hostname}

This is an automated notification from your Jarvis home system.
""",
        "html": """<html><body>
<h2>{name} left home</h2>
<p>{name} ({mac}) just disconnected from the home network at {time}.</p>
<ul>
<li><strong>Last IP:</strong> {ip}</li>
<li><strong>Hostname:</strong> {hostname}</li>
</ul>
<p>This is an automated notification from your Jarvis home system.</p>
</body></html>"""
    },
    "unknown_device": {
        "subject": "Unknown device detected on network",
        "text": """Hi there,

An unknown device just joined your network at {time}:

- MAC: {mac}
- IP: {ip}
- Hostname: {hostname}

This device is not registered in your device registry. You may want to:
1. Check if it's a guest device
2. Register it in Jarvis > Devices > Network
3. Or ignore if it's expected

This is an automated notification from your Jarvis home system.
""",
        "html": """<html><body>
<h2>Unknown device detected</h2>
<p>An unknown device just joined your network at {time}:</p>
<ul>
<li><strong>MAC:</strong> {mac}</li>
<li><strong>IP:</strong> {ip}</li>
<li><strong>Hostname:</strong> {hostname}</li>
</ul>
<p>This device is not registered in your device registry.</p>
<p>This is an automated notification from your Jarvis home system.</p>
</body></html>"""
    },
}


class EmailService:
    """Manages per-user email settings and delivery."""
    
    def __init__(self, redis_client: redis.Redis, http_session: aiohttp.ClientSession):
        self.r = redis_client
        self.session = http_session
        self._smtp_cache: dict[str, dict[str, Any]] = {}
    
    async def _get_user_settings(self, user_id: str) -> dict[str, Any] | None:
        """Get user's email settings from Redis cache or Identity."""
        key = f"{EMAIL_SETTINGS_PREFIX}{user_id}"
        cached = await self.r.get(key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass
        
        # Fetch from Identity
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{IDENTITY_SVC}/api/users/{user_id}/email-settings",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        settings = await resp.json()
                        await self.r.set(key, json.dumps(settings), ex=300)  # 5 min cache
                        return settings
        except Exception as e:
            log.warning(f"Failed to fetch email settings for {user_id}: {e}")
        return None
    
    async def save_settings(self, user_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """Save user's email settings (encrypted in Identity)."""
        # Validate required fields
        required = ["smtp_host", "smtp_port", "username", "password", "from_email"]
        for field in required:
            if not settings.get(field):
                raise ValueError(f"Missing required field: {field}")
        
        # Test connection before saving
        test_result = await self._test_smtp(settings)
        if not test_result["success"]:
            raise ValueError(f"SMTP test failed: {test_result['error']}")
        
        # Save to Identity (encrypted)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{IDENTITY_SVC}/api/users/{user_id}/email-settings",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    json=settings,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise ValueError(f"Failed to save settings: {text}")
                    result = await resp.json()
                    # Invalidate cache
                    await self.r.delete(f"{EMAIL_SETTINGS_PREFIX}{user_id}")
                    return result
        except aiohttp.ClientError as e:
            raise ValueError(f"Identity service error: {e}")
    
    async def _test_smtp(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Test SMTP connection."""
        try:
            host = settings["smtp_host"]
            port = int(settings["smtp_port"])
            username = settings["username"]
            password = settings["password"]
            use_tls = settings.get("use_tls", True)
            
            # Run in thread pool since smtplib is synchronous
            def _test():
                if use_tls:
                    server = smtplib.SMTP(host, port, timeout=10)
                    server.starttls()
                else:
                    server = smtplib.SMTP(host, port, timeout=10)
                server.login(username, password)
                server.quit()
                return True
            
            await asyncio.to_thread(_test)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def send_email(self, user_id: str, template_name: str, context: dict[str, Any]) -> bool:
        """Send email using user's settings and a template."""
        settings = await self._get_user_settings(user_id)
        if not settings:
            log.warning(f"No email settings for user {user_id}")
            return False
        
        # Get template (custom or default)
        template = await self._get_template(user_id, template_name)
        if not template:
            template = DEFAULT_TEMPLATES.get(template_name)
            if not template:
                log.error(f"No template found for {template_name}")
                return False
        
        # Render template
        try:
            subject = template["subject"].format(**context)
            text_body = template["text"].format(**context)
            html_body = template.get("html", "").format(**context)
        except KeyError as e:
            log.error(f"Template missing context key: {e}")
            return False
        
        # Send email
        return await self._send_via_smtp(settings, subject, text_body, html_body)
    
    async def _send_via_smtp(self, settings: dict[str, Any], subject: str, text: str, html: str) -> bool:
        """Send email via SMTP."""
        def _send():
            host = settings["smtp_host"]
            port = int(settings["smtp_port"])
            username = settings["username"]
            password = settings["password"]
            from_email = settings["from_email"]
            use_tls = settings.get("use_tls", True)
            
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = settings.get("to_email", from_email)  # default to self
            
            msg.attach(MIMEText(text, "plain"))
            if html:
                msg.attach(MIMEText(html, "html"))
            
            if use_tls:
                server = smtplib.SMTP(settings["smtp_host"], int(settings["smtp_port"]), timeout=30)
                server.starttls()
            else:
                server = smtplib.SMTP(settings["smtp_host"], int(settings["smtp_port"]), timeout=30)
            
            server.login(username, password)
            server.send_message(msg)
            server.quit()
            return True
        
        try:
            await asyncio.to_thread(_send)
            return True
        except Exception as e:
            log.error(f"SMTP send failed: {e}")
            return False
    
    async def _get_template(self, user_id: str, template_name: str) -> dict[str, Any] | None:
        """Get custom template for user."""
        key = f"{EMAIL_TEMPLATE_PREFIX}{user_id}:{template_name}"
        cached = await self.r.get(key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                return None
        return None
    
    async def save_template(self, user_id: str, template_name: str, template: dict[str, Any]) -> bool:
        """Save custom email template for user."""
        key = f"{EMAIL_TEMPLATE_PREFIX}{user_id}:{template_name}"
        try:
            await self.r.set(key, json.dumps(template), ex=TEMPLATE_TTL * 24 * 30)  # 30 days
            return True
        except Exception as e:
            log.error(f"Failed to save template: {e}")
            return False
    
    async def send_test_email(self, user_id: str) -> bool:
        """Send a test email to verify configuration."""
        context = {
            "name": "Test Device",
            "mac": "aa:bb:cc:dd:ee:ff",
            "ip": "192.168.1.100",
            "hostname": "test-device",
            "device_type": "phone",
            "time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        return await self.send_email(user_id, "device_arrival", context)


async def send_email(user_id: str, template_name: str, context: dict[str, Any]) -> bool:
    """Convenience function to send email."""
    r = redis.from_url(REDIS_URL, decode_responses=True)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        service = EmailService(r, session)
        return await service.send_email(user_id, template_name, context)


# Webhook endpoint for external triggers
async def send_webhook_email(request_data: dict[str, Any]) -> dict[str, Any]:
    """Webhook endpoint for external services to send emails."""
    user_id = request_data.get("user_id")
    template = request_data.get("template")
    context = request_data.get("context", {})
    
    if not user_id or not template:
        return {"success": False, "error": "Missing user_id or template"}
    
    success = await send_email(user_id, template, context)
    return {"success": success}