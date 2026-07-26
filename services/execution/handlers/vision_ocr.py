"""Vision OCR - Reusable text extraction using Qwen2.5-VL or similar vision LLM.

Sends screenshots to the user-configured LLM proxy for structured text extraction.
Returns full_text plus contextual fields (headline, subtext, badge).

Usage from execution service:
    from .vision_ocr import vision_ocr_screenshot, get_ollama_url
    text = await vision_ocr_screenshot("/tmp/screenshot.png", user_context=user)
"""

import asyncio
import base64
import json
import logging
from io import BytesIO
from typing import Optional

from PIL import Image

log = logging.getLogger("execution.vision_ocr")


async def get_ollama_url() -> Optional[str]:
    """Fetch LLM proxy URL from Identity settings (user-configured 'llm_local_url')."""
    try:
        import aiohttp
        from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 200:
                settings = await resp.json()
                for s in settings:
                    if s.get("key") == "llm_local_url" and s.get("value"):
                        return s["value"].rstrip("/")
    except Exception as e:
        log.warning(f"Failed to get LLM URL from Identity: {e}")
    return None


async def get_vision_ocr_model() -> Optional[str]:
    """Fetch vision OCR model from Identity settings (user-configured 'vision_ocr_model').

    Returns None if not set - caller should use default vision model.
    """
    try:
        import aiohttp
        from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 200:
                settings = await resp.json()
                for s in settings:
                    if s.get("key") == "vision_ocr_model" and s.get("value"):
                        return s["value"].strip()
    except Exception as e:
        log.warning(f"Failed to get vision OCR model from Identity: {e}")
    return None


async def vision_ocr_screenshot(
    image_path: str,
    user_context=None,
    proxy_url: Optional[str] = None,
    model: Optional[str] = None,
    max_size: int = 1024,
    task: str = "general",
) -> dict:
    """
    Extract text from a screenshot using the vision LLM.

    Args:
        image_path: Path to screenshot image (PNG/JPG)
        user_context: UserContext from execution (for LLM URL resolution)
        proxy_url: Override LLM proxy URL (default: from Identity settings)
        model: Override model name (default: user's configured vision_ocr_model setting)
        max_size: Max thumbnail size for image (reduces token usage)
        task: Task type for prompt selection ('general', 'price_scrape', 'document')

    Returns:
        dict with keys: full_text, headline, subtext, badge
    """
    # Resolve proxy URL
    if not proxy_url:
        proxy_url = await get_ollama_url()

    if not proxy_url:
        log.error("Vision OCR: no LLM proxy URL configured in Identity settings")
        return {"full_text": "", "headline": "", "subtext": "", "badge": "", "_error": "LLM URL not configured"}

    # Resolve model (from settings or caller override)
    if not model:
        model = await get_vision_ocr_model()
        if not model:
            model = "qwen2.5-vl:qwen2.5-vl-7b-instruct-q8_0"  # fallback

    # Load and prepare image
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_size, max_size))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64_image = base64.b64encode(buf.getvalue()).decode("utf-8")

    # Task-specific prompts
    price_prompt = (
        "You are an expert e-commerce price extraction assistant.\n"
        "Analyze the uploaded screenshot and extract ALL product prices and names.\n\n"
        "Respond ONLY with a valid JSON object with the following structure:\n"
        "{\n"
        '  "full_text": "Complete extracted text from top to bottom, line by line...",\n'
        '  "items": [\n'
        '    {"product": "Product Name Here", "price": 49.99},\n'
        '    {"product": "Another Product", "price": 29.99}\n'
        '  ],\n'
        '  "headline": "Page title or search results header",\n'
        '  "subtext": "Any filtering or sorting text",\n'
        '  "badge": "Promotional text like Deal of the Day or Prime"\n'
        "}\n\n"
        "CRITICAL RULES:\n"
        "- Extract EVERY product with its price\n"
        "- Include shipping costs if shown separately\n"
        '- Skip UI noise like "bought in past month", "add to cart", dates\n'
        "- Price must be a number (not a string), in the displayed currency\n"
        "- Preserve exact prices: $49.99 not $4999\n"
        "- If a product has no price, skip it\n"
    )
    doc_prompt = (
        "You are an expert document OCR assistant.\n"
        "Transcribe the uploaded document image completely.\n\n"
        "Respond ONLY with a valid JSON object:\n"
        "{\n"
        '  "full_text": "Complete transcription line by line...",\n'
        '  "headline": "Document title or main heading",\n'
        '  "subtext": "Body text or details",\n'
        '  "badge": "Headers, footers, page numbers, stamps"\n'
        "}\n\n"
        "Preserve all text exactly, including numbers, dates, and formatting."
    )
    general_prompt = (
        "You are an expert Document AI and OCR vision assistant.\n"
        "Analyze the uploaded image and extract ALL visible text.\n\n"
        "Respond ONLY with a valid JSON object with the following structure:\n"
        "{\n"
        '  "full_text": "Complete extracted text from top to bottom, line by line...",\n'
        '  "headline": "Main title or headline text found in the image",\n'
        '  "subtext": "Subtitle, body text, or event details",\n'
        '  "badge": "Badge, price tag, or call-to-action text"\n'
        "}\n\n"
        "IMPORTANT: Include EVERY line of text you see. Do not summarize. "
        "Preserve prices, numbers, and formatting exactly as shown."
    )
    prompt = {"price_scrape": price_prompt, "document": doc_prompt}.get(task, general_prompt)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
            ],
        }
    ]

    # Determine model name
    if not model:
        model = "qwen2.5-vl:qwen2.5-vl-7b-instruct-q8_0"

    proxy_model = model.replace("--", ":") if ("--" in model and ":" not in model) else model

    try:
        import aiohttp
        payload = {
            "model": proxy_model,
            "messages": messages,
            "max_tokens": 2000,
            "temperature": 0.1,
        }

        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{proxy_url}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120.0),
            )

            if resp.status != 200:
                log.error(f"Vision OCR API error: {resp.status}")
                return {
                    "full_text": "",
                    "headline": "",
                    "subtext": "",
                    "badge": "",
                    "_error": f"LLM API error {resp.status}",
                }

            data = await resp.json()
            raw_text = data["choices"][0]["message"]["content"]

        # Parse JSON response
        try:
            clean_json = raw_text.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_json:
                clean_json = clean_json.split("```")[1].split("```")[0].strip()
            result = json.loads(clean_json)
            # Ensure all required keys exist
            return {
                "full_text": result.get("full_text", ""),
                "headline": result.get("headline", ""),
                "subtext": result.get("subtext", ""),
                "badge": result.get("badge", ""),
            }
        except json.JSONDecodeError:
            log.warning(f"Vision OCR failed to parse LLM response as JSON")
            return {
                "full_text": raw_text,
                "headline": "",
                "subtext": "",
                "badge": "",
                "_raw": raw_text,
            }

    except asyncio.TimeoutError:
        log.error("Vision OCR timed out waiting for LLM response")
        return {"full_text": "", "headline": "", "subtext": "", "badge": "", "_error": "LLM timeout"}
    except Exception as e:
        log.error(f"Vision OCR failed: {e}")
        return {"full_text": "", "headline": "", "subtext": "", "badge": "", "_error": str(e)}
