"""Vision OCR - Reusable text extraction using Qwen2.5-VL or similar vision LLM.

Sends screenshots to the user-configured LLM proxy for structured text extraction.
Returns full_text plus contextual fields (headline, subtext, badge).

Usage from execution service:
    from .vision_ocr import vision_ocr_screenshot, get_ollama_url
    text = await vision_ocr_screenshot("/tmp/screenshot.png", user_context=user)
"""

import base64
import json
import logging
from io import BytesIO

from PIL import Image

log = logging.getLogger("execution.vision_ocr")


async def get_ollama_url() -> str | None:
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


async def get_vision_ocr_model() -> str | None:
    """Fetch vision OCR model from Identity settings (user-configured 'vision_ocr_model').

    Returns None if not set - caller must fail clearly with a configuration error.
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
    proxy_url: str | None = None,
    model: str | None = None,
    max_size: int = 1024,
    task: str = "general",
    context: str | None = None,
) -> dict:
    """
    Extract text from a screenshot using the vision LLM.

    Args:
        image_path: Path to screenshot image (PNG/JPG)
        user_context: UserContext from execution (for LLM URL resolution)
        proxy_url: Override LLM proxy URL (default: from Identity settings)
        model: Override model name (default: user's configured vision_ocr_model setting)
        max_size: Max thumbnail size for image (reduces token usage)
        task: Task type for prompt selection ('general', 'price_scrape', 'document',
            'recipe', 'plant_identify', 'plant_diagnose')
        context: Extra facts for the task's prompt (a plant's care history, symptoms)

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
            log.error("Vision OCR: no vision_ocr_model configured in Identity settings")
            return {"full_text": "", "headline": "", "subtext": "", "badge": "", "_error": "vision_ocr_model not configured in Identity settings"}

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
        "  ],\n"
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
    recipe_prompt = (
        "You are an expert recipe transcription assistant.\n"
        "Read the recipe in the uploaded image: a printed cookbook page, a recipe "
        "card, a screenshot or handwriting.\n\n"
        "Respond ONLY with a valid JSON object:\n"
        "{\n"
        '  "full_text": "Complete raw transcription, line by line, exactly as written...",\n'
        '  "recipe": {\n'
        '    "title": "The recipe name, or \\"\\" if none is shown",\n'
        '    "servings": "how many it serves, as written, or null",\n'
        '    "prep_minutes": 15,\n'
        '    "cook_minutes": 30,\n'
        '    "total_minutes": 45,\n'
        '    "ingredients": ["2 cups flour", "1 tsp salt"],\n'
        '    "steps": ["Mix the dry ingredients.", "Bake for 30 minutes."],\n'
        '    "notes": "Any tips or notes written on the page"\n'
        "  },\n"
        '  "uncertain": ["every word or number you were not sure of, with the line it was on"],\n'
        '  "suggestions": {"<an uncertain entry, copied exactly>": ["most likely reading", "another plausible reading"]}\n'
        "}\n\n"
        "RULES:\n"
        "- Never invent a quantity, time, ingredient or step that is not in the image.\n"
        "- A value you cannot read is null (or an empty list), never a guess.\n"
        "- Keep each ingredient and each step as its own list item.\n"
        "- List anything you were unsure of in 'uncertain' so a person can check it.\n"
        "- For each 'uncertain' entry add a 'suggestions' entry: up to 3 readings that "
        "fit the recipe, best first, keeping the same kind of word (a measure stays a "
        "measure, an ingredient stays an ingredient). They are offered to the cook to "
        "accept or ignore, so never change the recipe's meaning; leave a word out when "
        "you cannot suggest anything better."
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
    plant_identify_prompt = (
        "You are an expert horticulturist identifying a plant from a photo for a "
        "family garden app.\n"
        "The photo may show a houseplant, a garden plant, a herb, or a seed packet.\n\n"
        "Respond ONLY with a valid JSON object:\n"
        "{\n"
        '  "full_text": "One sentence describing what you see in the photo.",\n'
        '  "plant": {\n'
        '    "common_name": "the most common household name, or \\"\\" if unsure",\n'
        '    "scientific_name": "genus and species, or \\"\\" if unsure",\n'
        '    "category": "one of houseplant, herb, vegetable, fruit, flower, or \\"\\" if unsure",\n'
        '    "confidence": "high, medium or low",\n'
        '    "is_seed_packet": false,\n'
        '    "notes": "distinguishing features you actually see (leaf shape, variegation, flowers)"\n'
        "  },\n"
        '  "uncertain": ["anything you could not tell from the photo"]\n'
        "}\n\n"
        "RULES:\n"
        "- Name what the photo shows, never what you would like it to be.\n"
        "- A plant you cannot identify gets empty names and confidence 'low'.\n"
        "- is_seed_packet is true only when the photo is printed packaging.\n"
        "- Never invent a cultivar you cannot see."
    )
    plant_diagnose_prompt = (
        "You are an expert plant doctor diagnosing a sick plant from a photo for a "
        "family garden app.\n"
        "You may be given the plant's species, its recent care history, the weather and "
        "any symptoms the family typed.\n\n"
        "Respond ONLY with a valid JSON object:\n"
        "{\n"
        '  "full_text": "One sentence describing the visible problem.",\n'
        '  "diagnosis": {\n'
        '    "likely_causes": [{"cause": "overwatering", "confidence": "high", "why": "what in the photo supports it"}],\n'
        '    "confidence": "high, medium or low",\n'
        '    "urgency": "low, medium or high",\n'
        '    "recovery_steps": ["one concrete step at a time, in order"],\n'
        '    "care_adjustments": ["a change to watering, light, feeding or placement"],\n'
        '    "summary": "two sentences a person can act on"\n'
        "  },\n"
        '  "uncertain": ["anything the photo cannot show (roots, soil moisture, pests underneath)"]\n'
        "}\n\n"
        "RULES:\n"
        "- Diagnose from the photo and the facts given; say when the photo is not enough.\n"
        "- Never invent pesticide doses, chemical names or measurements.\n"
        "- Urgency 'high' only for a plant that may die within days without action.\n"
        "- Recovery steps are things a family can do at home, in order."
    )
    prompt = {
        "price_scrape": price_prompt,
        "document": doc_prompt,
        "recipe": recipe_prompt,
        "plant_identify": plant_identify_prompt,
        "plant_diagnose": plant_diagnose_prompt,
    }.get(task, general_prompt)
    if context and task in ("plant_identify", "plant_diagnose"):
        prompt = f"{prompt}\n\nWHAT WE ALREADY KNOW (use it, never contradict the photo):\n{context}"

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
            out = {
                "full_text": result.get("full_text", ""),
                "headline": result.get("headline", ""),
                "subtext": result.get("subtext", ""),
                "badge": result.get("badge", ""),
            }
            # The recipe task returns a structured reading, the words it was
            # unsure of and any readings it offers; pass them through for
            # services/common/recipe_ocr.py.
            # Structured readings pass through whole: a recipe, a plant ID or a
            # diagnosis is the point of its task, not the raw text.
            for _key in ("recipe", "plant", "diagnosis"):
                if isinstance(result.get(_key), dict):
                    out[_key] = result[_key]
            if result.get("uncertain"):
                out["uncertain"] = result["uncertain"]
            if isinstance(result.get("suggestions"), dict):
                out["suggestions"] = result["suggestions"]
            return out
        except json.JSONDecodeError:
            log.warning("Vision OCR failed to parse LLM response as JSON")
            return {
                "full_text": raw_text,
                "headline": "",
                "subtext": "",
                "badge": "",
                "_raw": raw_text,
            }

    except TimeoutError:
        log.error("Vision OCR timed out waiting for LLM response")
        return {"full_text": "", "headline": "", "subtext": "", "badge": "", "_error": "LLM timeout"}
    except Exception as e:
        log.error(f"Vision OCR failed: {e}")
        return {"full_text": "", "headline": "", "subtext": "", "badge": "", "_error": str(e)}
