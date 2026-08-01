#!/usr/bin/env python3
"""
WebScraper - Reusable web scraping tool with Playwright + Tesseract OCR
Captures screenshots of product pages and extracts prices/text using OCR.

Usage:
    python webscraper.py --query "tesla p40" --urls ebay amazon newegg
    python webscraper.py --query "32gb ddr4 3200" --mobile
    python webscraper.py --query "gpu replacement" --desktop --output /tmp/results.json
"""

import argparse
import asyncio
import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote


@dataclass
class PriceItem:
    product: str = ""
    price: float = 0.0
    currency: str = "USD"
    shipping: float = 0.0
    total: float = 0.0
    seller: str = ""
    rating: str = ""
    url: str = ""
    raw_text: str = ""
    description: str = ""
    specifications: dict = field(default_factory=dict)
    features: list = field(default_factory=list)
    model: str = ""
    availability: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class ScrapeResult:
    query: str
    source: str
    prices: list[PriceItem] = field(default_factory=list)
    raw_ocr: str = ""
    ocr_data: dict | None = None
    screenshot_path: str = ""
    error: str = ""
    specifications: list[dict] = field(default_factory=list)
    product_details: list[dict] = field(default_factory=list)
    full_description: str = ""

    def to_dict(self):
        return {
            "query": self.query,
            "source": self.source,
            "prices": [p.to_dict() for p in self.prices],
            "screenshot": self.screenshot_path,
            "error": self.error,
            "specifications": self.specifications,
            "product_details": self.product_details,
            "full_description": self.full_description,
        }


URLS = {
    "ebay": "https://www.ebay.com/sch/i.html?_nkw={query}&_sop=13&_ipg=60",
    "amazon": "https://www.amazon.com/s?k={query}&ref=sr_st_keyword-optimization",
    "walmart": "https://www.walmart.com/search?q={query}",
    "newegg": "https://www.newegg.com/p/pl?d={query}",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.php?st={query}",
    "aliexpress": "https://www.aliexpress.com/w/wholesale-{query_slug}.html",
    "google_shopping": "https://www.google.com/search?q={query}&tbm=shop",
}


async def launch_camoufox(headless=True, is_mobile=True):
    from camoufox.async_api import AsyncCamoufox

    mobile_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    )

    desktop_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    ctx = await AsyncCamoufox(headless=headless).__aenter__()

    page = await ctx.new_page()

    return page, ctx

async def launch_browser(headless=True, is_mobile=True, engine="playwright"):
    if engine == "camoufox":
        return await launch_camoufox(headless=headless, is_mobile=is_mobile)

    from playwright.async_api import async_playwright  # type: ignore[import-untyped]

    p = await async_playwright().start()

    ctx = None  # type: ignore[assignment]
    browser = None  # type: ignore[assignment]

    launch_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=PlaywrightControl",
        "--disable-infobars",
        "--disable-default-apps",
        "--disable-hang-monitor",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--disable-background-timer-throttling",
        "--disable-popup-blocking",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=0,0",
    ]

    mobile_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    )

    desktop_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    browser = await p.chromium.launch(
        headless=headless,
        args=launch_args
    )
    ctx = await browser.new_context(
        user_agent=mobile_ua if is_mobile else desktop_ua,
        viewport={"width": 1366, "height": 768} if not is_mobile else {"width": 390, "height": 844},
        locale="en-US",
        timezone_id="America/New_York",
        device_scale_factor=1 if not is_mobile else 3,
        has_touch=is_mobile,
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )

    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        if (!window.chrome) window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'connection', { get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }) });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                originalQuery(parameters)
        );
    """)

    page = await ctx.new_page()

    return p, page, ctx, browser


async def scrape_page(url: str, query: str, output_dir: Path, is_mobile: bool = False, headless: bool = True, ocr_model: str = "", ocr_proxy: str = "", browser_engine: str = "playwright") -> ScrapeResult:
    result = ScrapeResult(query=query, source=url.split("/")[2])

    p = None
    page = None
    ctx = None  # type: ignore[assignment]
    browser = None  # type: ignore[assignment]
    camoufox_ctx = None

    try:
        if browser_engine == "camoufox":
            page, camoufox_ctx = await launch_browser(headless=headless, is_mobile=is_mobile, engine="camoufox")
        else:
            p, page, ctx, browser = await launch_browser(headless=headless, is_mobile=is_mobile, engine=browser_engine)

        import random
        import asyncio as aio

        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        # Let JS-rendered results settle before capturing (eBay et al. render
        # listings async; domcontentloaded fires before prices exist on screen)
        await page.wait_for_timeout(6000)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        screenshot_path = output_dir / f"screenshot_{result.source}_{hash(query) % 10000}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        result.screenshot_path = str(screenshot_path)

        # OCR the RESULTS AREA (viewport shot near the top of the listing), not
        # the squished full page: a 7934px page thumbnailed to 768px is
        # unreadable to the vision model, and transcribing it blows the token
        # cap (truncated JSON -> parse failure -> 'No prices found').
        try:
            await page.evaluate("window.scrollTo(0, 500)")
            await page.wait_for_timeout(800)
        except Exception:
            pass
        ocr_shot_path = output_dir / f"ocr_{result.source}_{hash(query) % 10000}.png"
        try:
            await page.screenshot(path=str(ocr_shot_path), full_page=False)
        except Exception:
            ocr_shot_path = screenshot_path

        # Vision OCR (Qwen2.5-VL via proxy) — primary method
        try:
            from vision_ocr import extract_text
            ocr_result_data = extract_text(str(ocr_shot_path), task="price_scrape", model=ocr_model or None, proxy_url=ocr_proxy or None, max_size=768)
            result.raw_ocr = ocr_result_data.get("full_text", "")
            result.ocr_data = ocr_result_data

            if ocr_result_data.get("items"):
                for item in ocr_result_data["items"]:
                    try:
                        price_val = float(item["price"]) if item.get("price") else 0.0
                        specs = item.get("specifications", {})
                        if isinstance(specs, str):
                            try:
                                specs = json.loads(specs)
                            except (json.JSONDecodeError, TypeError):
                                specs = {}
                        elif not isinstance(specs, dict):
                            specs = {}
                        
                        result.prices.append(
                            PriceItem(
                                product=item.get("product", ""),
                                price=price_val,
                                shipping=0.0,
                                total=price_val,
                                description=item.get("description", ""),
                                specifications=specs,
                                features=item.get("features", []) or [],
                                model=item.get("model", ""),
                                availability=item.get("availability", ""),
                            )
                        )
                    except (ValueError, TypeError):
                        pass

            if not result.prices and result.raw_ocr:
                result.prices = extract_prices(result.raw_ocr)

            # Aggregate top-level spec/detail fields from OCR
            all_specs = []
            all_details = []
            descriptions = []
            if ocr_result_data.get("items"):
                for item in ocr_result_data["items"]:
                    if item.get("specifications"):
                        all_specs.append({"product": item.get("product", ""), **item["specifications"]})
                    if item.get("description"):
                        descriptions.append(item["description"])
                    if item.get("features"):
                        all_details.append({"product": item.get("product", ""), "features": item["features"]})
                    if item.get("model"):
                        all_details.append({"product": item.get("product", ""), "model": item["model"]})
            result.specifications = all_specs
            result.product_details = all_details
            result.full_description = "\n\n".join(descriptions) if descriptions else ""

        except Exception as ocr_err:
            result.error = f"Vision OCR failed: {ocr_err}"
            result.raw_ocr = ""

    except Exception as e:
        result.error = str(e)
    finally:
        if page:
            if browser_engine == "camoufox" and camoufox_ctx:
                try:
                    await camoufox_ctx.close()
                except Exception:
                    pass
            elif ctx:
                await ctx.close()
            elif browser:
                await browser.close()
        if p:
            await p.stop()

    return result


def _parse_ebay(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse eBay search results with source-specific heuristics."""
    items = []
    lines = ocr_text.split("\n")

    # eBay typically has format: "$XX.XX" or "$XX.XX + $X.XX shipping"
    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
    shipping_pattern = re.compile(r'[+\s]\$\s*([\d,]+(?:\.\d{1,2})?)\s*(?:shipping|delivery|postage)')

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            # Look for product name in nearby lines
            product_name = ""
            for j in range(max(0, i - 3), i):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 10 and not candidate.startswith("$"):
                    product_name = candidate
                    break

            # Look for shipping cost
            shipping = 0.0
            ship_match = shipping_pattern.search("\n".join(lines[i:min(i+3, len(lines))]))
            if ship_match:
                try:
                    shipping = float(ship_match.group(1).replace(",", ""))
                except ValueError:
                    pass

            if product_name:
                items.append(PriceItem(
                    product=product_name[:100],
                    price=price,
                    shipping=shipping,
                    total=price + shipping,
                ))
        i += 1

    # Deduplicate
    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _parse_amazon(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse Amazon search results with source-specific heuristics."""
    items = []
    lines = ocr_text.split("\n")

    # Amazon format: "$XX.XX" often with "Prime" or free shipping
    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
    prime_pattern = re.compile(r"prime", re.I)

    # Patterns to skip (UI noise, not product names)
    skip_patterns = re.compile(
        r"(bought in past|used & new offers|Now Price:|\d+\s*\(\d+\)|"
        r"Add to cart|See options|Join Prime|delivery|shipping|"
        r"Non-members|Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Jul|Aug|Sep|"
        r"Conditions of Use|Privacy|Amazon\.com|Your Orders|AmazonFresh|"
        r"Gift Cards|Registry|Browsing History|Customer Service)",
        re.I
    )

    def is_noise(line: str) -> bool:
        return bool(skip_patterns.search(line)) or len(line) < 10

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            # Skip if price is absurdly high (likely OCR error) or low for GPUs
            if price > 10000 or price < 10:
                i += 1
                continue

            # Look for product name in nearby lines - skip noise
            product_name = ""
            for j in range(max(0, i - 6), i):
                candidate = lines[j].strip()
                if (candidate and len(candidate) > 12 and not candidate.startswith("$")
                        and not candidate.startswith("Prime") and not is_noise(candidate)):
                    product_name = candidate
                    break

            # Free shipping for Prime
            shipping = 0.0
            if i + 1 < len(lines) and prime_pattern.search(lines[i + 1]):
                shipping = 0.0  # Free with Prime

            if product_name:
                items.append(PriceItem(
                    product=product_name[:100],
                    price=price,
                    shipping=shipping,
                    total=price + shipping,
                ))
        i += 1

    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _parse_newegg(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse Newegg search results."""
    items = []
    lines = ocr_text.split("\n")

    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            product_name = ""
            for j in range(max(0, i - 3), i):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 10 and not candidate.startswith("$"):
                    product_name = candidate
                    break

            shipping = 0.0
            if i + 1 < len(lines):
                if "free shipping" in lines[i + 1].lower():
                    shipping = 0.0
                elif "shipping" in lines[i + 1].lower():
                    ship_p = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", lines[i + 1])
                    if ship_p:
                        try:
                            shipping = float(ship_p.group(1).replace(",", ""))
                        except ValueError:
                            pass

            if product_name:
                items.append(PriceItem(
                    product=product_name[:100],
                    price=price,
                    shipping=shipping,
                    total=price + shipping,
                ))
        i += 1

    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _parse_walmart(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse Walmart search results."""
    items = []
    lines = ocr_text.split("\n")

    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            product_name = ""
            for j in range(max(0, i - 3), i):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 10 and not candidate.startswith("$"):
                    product_name = candidate
                    break

            shipping = 0.0
            if i + 1 < len(lines):
                if "free shipping" in lines[i + 1].lower() or "free pickup" in lines[i + 1].lower():
                    shipping = 0.0
                elif "shipping" in lines[i + 1].lower():
                    ship_p = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", lines[i + 1])
                    if ship_p:
                        try:
                            shipping = float(ship_p.group(1).replace(",", ""))
                        except ValueError:
                            pass

            if product_name:
                items.append(PriceItem(
                    product=product_name[:100],
                    price=price,
                    shipping=shipping,
                    total=price + shipping,
                ))
        i += 1

    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _parse_aliexpress(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse AliExpress search results."""
    items = []
    lines = ocr_text.split("\n")

    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            product_name = ""
            for j in range(max(0, i - 4), i):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 10 and not candidate.startswith("$"):
                    product_name = candidate
                    break

            # AliExpress often has "free shipping"
            shipping = 0.0
            if i + 1 < len(lines):
                if "free shipping" in lines[i + 1].lower():
                    shipping = 0.0

            if product_name:
                items.append(PriceItem(
                    product=product_name[:100],
                    price=price,
                    shipping=shipping,
                    total=price + shipping,
                ))
        i += 1

    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _parse_bestbuy(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse Best Buy search results."""
    items = []
    lines = ocr_text.split("\n")

    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            product_name = ""
            for j in range(max(0, i - 4), i):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 10 and not candidate.startswith("$"):
                    product_name = candidate
                    break

            # Best Buy often shows "Save X" or clearance info
            shipping = 0.0
            if i + 1 < len(lines):
                next_line = lines[i + 1].lower()
                if "free shipping" in next_line:
                    shipping = 0.0
                elif "shipping" in next_line:
                    ship_p = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", lines[i + 1])
                    if ship_p:
                        try:
                            shipping = float(ship_p.group(1).replace(",", ""))
                        except ValueError:
                            pass

            if product_name:
                items.append(PriceItem(
                    product=product_name[:100],
                    price=price,
                    shipping=shipping,
                    total=price + shipping,
                ))
        i += 1

    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _parse_google_shopping(ocr_text: str, html_snippet: str = "") -> list[PriceItem]:
    """Parse Google Shopping results."""
    items = []
    lines = ocr_text.split("\n")

    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        price_match = price_pattern.search(line)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                i += 1
                continue

            product_name = ""
            for j in range(max(0, i - 3), i):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 10 and not candidate.startswith("$"):
                    product_name = candidate
                    break

            # Try to capture the store/retailer name
            store = ""
            for j in range(i + 1, min(i + 3, len(lines))):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("$") and len(candidate) > 2 and len(candidate) < 40:
                    store = candidate
                    break

            shipping = 0.0
            if i + 1 < len(lines):
                if "free" in lines[i + 1].lower() and "ship" in lines[i + 1].lower():
                    shipping = 0.0

            items.append(PriceItem(
                product=product_name[:100],
                price=price,
                shipping=shipping,
                total=price + shipping,
                seller=store,
            ))
        i += 1

    seen = set()
    unique = []
    for item in items:
        key = (item.price, item.product[:30])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


# Source-specific parser registry
_SOURCE_PARSERS = {
    "ebay.com": _parse_ebay,
    "amazon.com": _parse_amazon,
    "newegg.com": _parse_newegg,
    "walmart.com": _parse_walmart,
    "bestbuy.com": _parse_bestbuy,
    "aliexpress.com": _parse_aliexpress,
    "google.com": _parse_google_shopping,
}


def _detect_source(source: str) -> str:
    """Extract domain from source string."""
    source = source.lower()
    for domain in _SOURCE_PARSERS:
        if domain in source:
            return domain
    return source


def extract_prices(ocr_text: str, source: str = "", html_snippet: str = "") -> list[PriceItem]:
    """Extract price items from OCR text using heuristic parsing."""
    items = []
    lines = ocr_text.split("\n")

    # Pattern to find dollar amounts
    price_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    # Group consecutive lines into product blocks
    i = 0
    current_block: list[str] = []

    def parse_block(block_lines):
        text = "\n".join(block_lines)
        prices = price_pattern.findall(text)
        if not prices:
            return

        # Try to extract product name (usually lines above/below prices)
        product_name = ""
        seller = ""
        rating = ""
        shipping = 0.0

        # Extract main price
        try:
            price = float(prices[0].replace(",", ""))
        except (ValueError, IndexError):
            return

        # Look for product context
        full_text = "\n".join(block_lines)
        for line in block_lines[:5]:
            line = line.strip()
            if line and not line.startswith("$") and len(line) > 10:
                if not product_name:
                    product_name = line

        # Look for seller info
        seller_match = re.search(r'(\w+)\s+\((\d+\.?\d*)%', full_text)
        if seller_match:
            seller = seller_match.group(1)
            rating = seller_match.group(2)

        # Look for shipping
        ship_match = re.search(r'\+\s*\$[\d,]+\.?\d*\s*delivery|shipping', full_text, re.I)
        if ship_match:
            ship_price_match = re.search(r'\+\s*(\$\s*[\d,]+\.?\d*)', full_text)
            if ship_price_match:
                try:
                    shipping = float(ship_price_match.group(1).replace(",", "").replace("$", ""))
                except ValueError:
                    pass

        item = PriceItem(
            product=product_name,
            price=price,
            shipping=shipping,
            total=price + shipping,
            seller=seller,
            rating=rating,
        )
        items.append(item)

    for line in lines:
        has_price = "$" in line and any(c.isdigit() for c in line)
        if has_price or current_block:
            current_block.append(line)
            if has_price and len(current_block) > 1:
                parse_block(current_block[-3:])  # Last 3 lines of block
                current_block = []
        elif current_block:
            parse_block(current_block[:3])
            current_block = []

    # Handle remaining block
    if current_block:
        parse_block(current_block)

    # Deduplicate by price
    seen = set()
    unique_items = []
    for item in items:
        key = (item.price, item.product[:20])
        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    return unique_items


def format_results(results: list[ScrapeResult]) -> str:
    """Format scrape results as a readable string."""
    output = []
    for r in results:
        output.append(f"\n{'='*60}")
        output.append(f"QUERY: {r.query}")
        output.append(f"SOURCE: {r.source}")
        output.append(f"{'='*60}")

        if r.error:
            output.append(f"ERROR: {r.error}")
            continue

        if not r.prices:
            output.append("No prices found.")
            continue

        # Sort by total price
        sorted_prices = sorted(r.prices, key=lambda x: x.total)

        output.append(f"{'Product':<50} {'Price':>10} {'Ship':>10} {'Total':>10} {'Seller/Rating'}")
        output.append("-" * 100)

        for p in sorted_prices[:15]:
            seller_info = f"{p.seller} ({p.rating}%)" if p.seller and p.rating else ""
            if not seller_info and not p.seller:
                seller_info = "-"
            output.append(
                f"{p.product[:48]:<50} ${p.price:>9.2f} ${p.shipping:>9.2f} ${p.total:>9.2f} {seller_info}"
            )

    return "\n".join(output)


def save_results(results: list[ScrapeResult], output_path: str):
    """Save results to JSON file."""
    data = {
        "results": [r.to_dict() for r in results],
        "summary": {
            "total_queries": len(results),
            "total_sources": len(set(r.source for r in results)),
            "total_prices_found": sum(len(r.prices) for r in results),
        },
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nResults saved to: {output_path}")


async def scrape_multiple(
    query: str,
    urls: list[str],
    mobile: bool = False,
    headless: bool = True,
    output_dir: str | Path | None = None,
    output_file: str | None = None,
    ocr_model: str = "",
    ocr_proxy: str = "",
    browser_engine: str = "playwright",
    json_output: bool = False,
):
    output_dir_path: Path = Path(output_dir or "/tmp/webscraper_output")
    output_dir_path.mkdir(parents=True, exist_ok=True)

    results = []
    for url_template in urls:
        # Resolve named sources to full URLs
        if url_template in URLS:
            url_template = URLS[url_template]
        if "{query}" in url_template:
            encoded_query = quote(query, safe='')
            url = url_template.format(query=encoded_query)
        elif "{query_slug}" in url_template:
            url = url_template.format(query_slug=query.replace(" ", "-"))
        else:
            url = url_template

        print(f"Scraping ({browser_engine}): {url}")
        result = await scrape_page(
            url, query, output_dir_path,
            is_mobile=mobile,
            headless=headless,
            ocr_model=ocr_model,
            ocr_proxy=ocr_proxy,
            browser_engine=browser_engine,
        )
        results.append(result)
        await asyncio.sleep(1)

    print(format_results(results))

    if output_file:
        save_results(results, output_file)

    if json_output:
        json_data = {
            "results": [r.to_dict() for r in results],
            "summary": {
                "total_queries": len(results),
                "total_sources": len(set(r.source for r in results)),
                "total_prices_found": sum(len(r.prices) for r in results),
            },
        }
        print(json.dumps(json_data, indent=2))

    return results


def main():
    parser = argparse.ArgumentParser(description="Web scraper with OCR price extraction")
    parser.add_argument("--query", "-q", required=True, help="Search query")
    parser.add_argument("--urls", "-u", nargs="+", default=["ebay", "amazon", "newegg", "walmart", "bestbuy"],
                        help="URL sources (ebay, amazon, newegg, aliexpress, google_shopping, walmart, bestbuy) or full URLs")
    parser.add_argument("--mobile", action="store_true", help="Use mobile viewport")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless (default: True)")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run with browser visible")
    parser.add_argument("--browser", "-b", default="playwright", choices=["playwright", "camoufox"],
                        help="Browser engine to use (default: playwright)")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--output-dir", default=None, help="Directory for screenshots/OCR (default: /tmp/webscraper_output)")
    parser.add_argument("--ocr-model", default=None, help="OCR vision model name (e.g. qwen2.5-vl:7b)")
    parser.add_argument("--ocr-proxy", default=None, help="OCR proxy URL (e.g. http://jeremiah-home-desktop.local:11434)")
    parser.add_argument("--json-output", action="store_true", help="Print structured JSON to stdout for programmatic parsing")

    args = parser.parse_args()

    resolved_urls = []
    for u in args.urls:
        if u in URLS:
            resolved_urls.append(URLS[u])
        elif u.startswith("http"):
            resolved_urls.append(u)
        else:
            print(f"Warning: Unknown source '{u}', treating as full URL")
            resolved_urls.append(u)

    asyncio.run(scrape_multiple(
        query=args.query,
        urls=resolved_urls,
        mobile=args.mobile,
        headless=args.headless,
        output_dir=args.output_dir,
        output_file=args.output,
        ocr_model=args.ocr_model or "",
        ocr_proxy=args.ocr_proxy or "",
        browser_engine=args.browser,
        json_output=args.json_output,
    ))


if __name__ == "__main__":
    main()
