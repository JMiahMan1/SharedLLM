#!/usr/bin/env python3
"""
Vision OCR - Reusable text extraction using Qwen2.5-VL vision model.

Sends screenshots to a local LLM proxy (alpaca-proxy) running Qwen2.5-VL
for structured text extraction. Returns full text and/or structured fields.

Usage:
    python vision_ocr.py --image screenshot.png --model qwen2.5-vl:qwen2.5-vl-7b-instruct-q8_0
    python vision_ocr.py --image screenshot.png --output-text extracted.txt
"""

import argparse
import base64
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image


VOCAB_DEFAULT_MODEL = os.environ.get("VISION_OCR_MODEL", "qwen2.5-vl:qwen2.5-vl-7b-instruct-q8_0")
VOCAB_DEFAULT_PROXY = os.environ.get("VISION_OCR_PROXY_URL", "")


def _image_to_base64(image_path: str, max_size: int = 1024) -> str:
    """Load image, thumbnail it, encode as base64 JPEG."""
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_size, max_size))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _build_prompt(task: str = "general") -> str:
    """Build task-specific OCR prompt."""
    prompts = {
        "price_scrape": (
            'You are an expert e-commerce price extraction assistant.\n'
            'Analyze the uploaded screenshot and extract ALL product prices and names.\n\n'
            'Respond ONLY with a valid JSON object with the following structure:\n'
            '{\n'
            '  "full_text": "Complete extracted text from top to bottom, line by line...",\n'
            '  "items": [\n'
            '    {"product": "Product Name Here", "price": 49.99},\n'
            '    {"product": "Another Product", "price": 29.99}\n'
            '  ],\n'
            '  "headline": "Page title or search results header",\n'
            '  "subtext": "Any filtering or sorting text",\n'
            '  "badge": "Promotional text like Deal of the Day or Prime"\n'
            "}\n\n"
            'CRITICAL RULES:\n'
            '- Extract EVERY product with its price\n'
            '- Include shipping costs if shown separately\n'
            '- Skip UI noise like "bought in past month", "add to cart", dates\n'
            '- Price must be a number (not a string), in the displayed currency\n'
            '- Preserve exact prices: $49.99 not $4999\n'
            '- If a product has no price, skip it\n'
        ),
        "document": (
            'You are an expert document OCR assistant.\n'
            'Transcribe the uploaded document image completely.\n\n'
            'Respond ONLY with a valid JSON object:\n'
            '{\n'
            '  "full_text": "Complete transcription line by line...",\n'
            '  "headline": "Document title or main heading",\n'
            '  "subtext": "Body text or details",\n'
            '  "badge": "Headers, footers, page numbers, stamps"\n'
            "}\n\n"
            'Preserve all text exactly, including numbers, dates, and formatting.'
        ),
    }
    price_prompt = prompts["price_scrape"]
    doc_prompt = prompts["document"]
    general_prompt = (
        'You are an expert Document AI and OCR vision assistant.\n'
        'Analyze the uploaded image and extract all visible text.\n\n'
        'Respond ONLY with a valid JSON object with the following structure:\n'
        '{\n'
        '  "full_text": "Complete extracted text from top to bottom, line by line...",\n'
        '  "headline": "Main title or headline text found in the image",\n'
        '  "subtext": "Subtitle, body text, or event details",\n'
        '  "badge": "Badge, price tag, or call-to-action text"\n'
        "}\n\n" +
        'IMPORTANT: Include EVERY line of text you see. Do not summarize. '
        'Preserve prices, numbers, and formatting exactly as shown.'
    )
    return {"price_scrape": price_prompt, "document": doc_prompt}.get(task, general_prompt)


def extract_text(
    image_path: str,
    proxy_url: Optional[str] = None,
    model: Optional[str] = None,
    max_size: int = 1024,
    task: str = "general",
) -> dict:
    """
    Extract text from an image using vision LLM (Qwen2.5-VL).

    Args:
        image_path: Path to screenshot image (PNG/JPG)
        proxy_url: Proxy URL (env: VISION_OCR_PROXY_URL)
        model: Model name (env: VISION_OCR_MODEL)
        max_size: Max thumbnail size for image
        task: Task type ('general', 'price_scrape', 'document')

    Returns dict with keys: full_text, headline, subtext, badge
    """
    proxy_url = (proxy_url or VOCAB_DEFAULT_PROXY).rstrip("/")
    model = model or VOCAB_DEFAULT_MODEL

    b64_image = _image_to_base64(image_path, max_size=max_size)

    prompt = _build_prompt(task)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
            ],
        }
    ]

    proxy_model = model.replace("--", ":") if ("--" in model and ":" not in model) else model

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{proxy_url}/v1/chat/completions",
            json={
                "model": proxy_model,
                "messages": messages,
                "max_tokens": 2000,
                "temperature": 0.1,
            },
        )

        if resp.status_code != 200:
            return {
                "full_text": "",
                "headline": "",
                "subtext": "",
                "badge": "",
                "_error": resp.text[:500],
            }

        data = resp.json()
        raw_text = data["choices"][0]["message"]["content"]

        # Parse JSON response
        try:
            clean_json = raw_text.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_json:
                clean_json = clean_json.split("```")[1].split("```")[0].strip()
            return json.loads(clean_json)
        except (json.JSONDecodeError, KeyError, IndexError):
            return {
                "full_text": raw_text,
                "headline": "",
                "subtext": "",
                "badge": "",
                "_raw": raw_text,
            }


def extract_text_from_file(
    image_path: str,
    output_text_path: Optional[str] = None,
    proxy_url: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Extract text from image and optionally save to file. Returns full_text."""
    result = extract_text(image_path, proxy_url=proxy_url, model=model)
    full_text = result.get("full_text", "")

    if output_text_path:
        Path(output_text_path).write_text(full_text, encoding="utf-8")

    return full_text


def main():
    parser = argparse.ArgumentParser(description="Vision OCR using Qwen2.5-VL")
    parser.add_argument("--image", "-i", required=True, help="Path to screenshot image")
    parser.add_argument("--model", "-m", default=None, help="Vision model ID (env: VISION_OCR_MODEL)")
    parser.add_argument("--proxy", "-p", default=None, help="Proxy URL (env: VISION_OCR_PROXY_URL)")
    parser.add_argument("--output-text", "-o", default=None, help="Output text file path")
    parser.add_argument("--json", action="store_true", help="Output full JSON structure")

    args = parser.parse_args()

    result = extract_text(args.image, proxy_url=args.proxy, model=args.model)
    full_text = result.get("full_text", "")

    if args.json:
        # Clean internal fields for clean output
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        print(json.dumps(clean, indent=2))
    else:
        print(full_text)

    if args.output_text:
        Path(args.output_text).write_text(full_text, encoding="utf-8")
        print(f"\nText saved to: {args.output_text}")


if __name__ == "__main__":
    main()
