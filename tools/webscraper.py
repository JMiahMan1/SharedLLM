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
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


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

    def to_dict(self):
        return {
            "query": self.query,
            "source": self.source,
            "prices": [p.to_dict() for p in self.prices],
            "screenshot": self.screenshot_path,
            "error": self.error,
        }


URLS = {
    "ebay": "https://www.ebay.com/sch/i.html?_nkw={query}&_sop=13&_ipg=60",
    "amazon": "https://www.amazon.com/s?k={query}&ref=sr_st_keyword-optimization",
    "walmart": "https://www.walmart.com/search?q={query}",
    "newegg": "https://www.newegg.com/p/pl?d={query}",
    "aliexpress": "https://www.aliexpress.com/w/wholesale-{query_slug}.html",
    "google_shopping": "https://www.google.com/search?q={query}&tbm=shop",
}


async def launch_browser(headless=True, is_mobile=False):
    from playwright.async_api import async_playwright  # type: ignore[import-untyped]

    p = await async_playwright().start()

    ctx = None  # type: ignore[assignment]
    browser = None  # type: ignore[assignment]

    if is_mobile:
        mobile_ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
            "Mobile/15E148 Safari/604.1"
        )
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=mobile_ua,
        )
        page = await ctx.new_page()
    else:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

    return p, page, ctx, browser


async def scrape_page(url: str, query: str, output_dir: Path, is_mobile: bool = False, headless: bool = True, ocr_model: str = "", ocr_proxy: str = "") -> ScrapeResult:
    result = ScrapeResult(query=query, source=url.split("/")[2])

    p = None
    page = None
    ctx = None  # type: ignore[assignment]
    browser = None  # type: ignore[assignment]

    try:
        p, page, ctx, browser = await launch_browser(headless=headless, is_mobile=is_mobile)

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        screenshot_path = output_dir / f"screenshot_{result.source}_{hash(query) % 10000}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        result.screenshot_path = str(screenshot_path)

        # Vision OCR (Qwen2.5-VL via proxy) — primary method
        try:
            from vision_ocr import extract_text
            ocr_result_data = extract_text(str(screenshot_path), task="price_scrape", model=ocr_model or None, proxy_url=ocr_proxy or None)
            result.raw_ocr = ocr_result_data.get("full_text", "")
            result.ocr_data = ocr_result_data

            # Use vision LLM structured items when available
            if ocr_result_data.get("items"):
                for item in ocr_result_data["items"]:
                    try:
                        result.prices.append(
                            PriceItem(
                                product=item.get("product", ""),
                                price=float(item["price"]) if item.get("price") else 0.0,
                                shipping=0.0,
                                total=float(item["price"]) if item.get("price") else 0.0,
                            )
                        )
                    except (ValueError, TypeError):
                        pass

            if not result.prices and result.raw_ocr:
                # LLM returned text but no structured items — try heuristic parser
                result.prices = extract_prices(result.raw_ocr)

        except Exception as ocr_err:
            result.error = f"Vision OCR failed: {ocr_err}"
            result.raw_ocr = ""

    except Exception as e:
        result.error = str(e)
    finally:
        if page and ctx:
            await ctx.close()
        elif page and browser:
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
):
    output_dir_path: Path = Path(output_dir or "/tmp/webscraper_output")
    output_dir_path.mkdir(parents=True, exist_ok=True)

    results = []
    for url_template in urls:
        # Format URL with query
        if "{query}" in url_template:
            url = url_template.format(query=query)
        elif "{query_slug}" in url_template:
            url = url_template.format(query_slug=query.replace(" ", "-"))
        else:
            url = url_template

        print(f"Scraping: {url}")
        result = await scrape_page(url, query, output_dir_path, is_mobile=mobile, headless=headless, ocr_model=ocr_model, ocr_proxy=ocr_proxy)
        results.append(result)

        # Brief delay between requests
        await asyncio.sleep(1)

    # Print results
    print(format_results(results))

    # Save if requested
    if output_file:
        save_results(results, output_file)

    return results


def main():
    parser = argparse.ArgumentParser(description="Web scraper with OCR price extraction")
    parser.add_argument("--query", "-q", required=True, help="Search query")
    parser.add_argument("--urls", "-u", nargs="+", default=["ebay", "amazon", "newegg", "walmart"],
                        help="URL sources (ebay, amazon, newegg, aliexpress, google_shopping, walmart) or full URLs")
    parser.add_argument("--mobile", action="store_true", help="Use mobile viewport")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless (default: True)")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--output-dir", default=None, help="Directory for screenshots/OCR (default: /tmp/webscraper_output)")

    args = parser.parse_args()

    # Resolve URL sources
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
    ))


if __name__ == "__main__":
    main()
