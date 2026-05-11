"""Decodo Scraper API client + Amazon India parser.
With pincode injection via geo parameter.
The pincode_verified flag is set true when the delivery message contains the requested pincode.
"""
import os
import re
import time
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

DECODO_URL = "https://scraper-api.decodo.com/v2/scrape"
DECODO_KEY = os.environ.get("DECODO_KEY", "").strip()


def _is_blocked(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    low = html.lower()
    return (
        "robot check" in low
        or "captcha" in low
        or "enter the characters you see below" in low
    )


def _decodo_post(payload: Dict, timeout: int = 90) -> Optional[Dict]:
    if not DECODO_KEY:
        raise RuntimeError("DECODO_KEY missing from environment")
    headers = {
        "Authorization": f"Basic {DECODO_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(DECODO_URL, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _extract_html(data: Optional[Dict]) -> str:
    if not data:
        return ""
    results = data.get("results") or []
    if not results:
        return ""
    content = results[0].get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("content") or content.get("html") or ""
    return ""


def _ancestor_classes(el, depth: int = 5) -> str:
    out = []
    p = el.parent
    for _ in range(depth):
        if p is None:
            break
        out.extend(p.get("class", []) or [])
        p = p.parent
    return " ".join(out)


def _digits_to_price(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned:
        return ""
    int_part = re.sub(r"[^\d]", "", cleaned.split(".")[0])
    if not int_part:
        return ""
    return f"Rs. {int_part}"


def _is_excluded_price_node(el) -> bool:
    """True if the price node represents per-unit or M.R.P. (basis) or strikethrough."""
    own = " ".join(el.get("class", []) or [])
    anc = _ancestor_classes(el, depth=5)
    combined = own + " " + anc
    bad_markers = (
        "apex-priceperunit-value",
        "apex-basisprice-value",
        "a-text-price",          # Amazon's strikethrough M.R.P. wrapper
        "basisPrice",
        "priceBlockStrikePriceString",
    )
    return any(m in combined for m in bad_markers)


def _parse_price(soup) -> str:
    # Priority 1: explicit priceToPay (the actual Buy Box selling price).
    # Often .a-offscreen is empty so prefer .a-price-whole + .a-price-fraction.
    priority_selectors = [
        "#corePriceDisplay_desktop_feature_div span.priceToPay",
        "#corePrice_feature_div span.priceToPay",
        "#apex_desktop span.priceToPay",
        "#apex_desktop_newAccordion span.priceToPay",
        "span.priceToPay",
        "#price_inside_buybox",
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        "#priceblock_saleprice",
    ]
    for sel in priority_selectors:
        for el in soup.select(sel):
            if _is_excluded_price_node(el):
                continue
            # Try .a-offscreen first (often empty on priceToPay but populated on others)
            off = el.select_one(".a-offscreen") if hasattr(el, "select_one") else None
            if off:
                p = _digits_to_price(off.get_text(strip=True))
                if p:
                    return p
            # Try whole + (optional) fraction
            whole = el.select_one(".a-price-whole") if hasattr(el, "select_one") else None
            if whole:
                whole_text = re.sub(r"[^\d]", "", whole.get_text(strip=True))
                if whole_text:
                    return f"Rs. {whole_text}"
            # Last: text content
            p = _digits_to_price(el.get_text(strip=True))
            if p:
                return p

    # Priority 2: any .a-price .a-offscreen but skip per-unit / basis / strikethrough
    for el in soup.select(".a-price .a-offscreen, .apexPriceToPay .a-offscreen"):
        if _is_excluded_price_node(el):
            continue
        p = _digits_to_price(el.get_text(strip=True))
        if p:
            return p
    return ""


def _parse_seller(soup) -> str:
    for sel in [
        "#sellerProfileTriggerId",
        ".tabular-buybox-text a",
        "#merchant-info a",
        "#merchant-info",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                # Clean prefixes like "Sold by "
                txt = re.sub(r"^(Sold by|Ships from)\s*:?\s*", "", txt, flags=re.IGNORECASE)
                return txt[:120]
    return ""


def _parse_rating(soup) -> str:
    el = soup.select_one("#acrPopover span.a-icon-alt") or soup.select_one("i.a-icon-star span.a-icon-alt")
    if el:
        m = re.search(r"([\d.]+)\s*out", el.get_text(strip=True))
        if m:
            return m.group(1)
    return ""


def _parse_reviews(soup) -> str:
    el = soup.select_one("#acrCustomerReviewText")
    if el:
        m = re.search(r"([\d,]+)", el.get_text(strip=True))
        if m:
            return m.group(1).replace(",", "")
    return ""


def _parse_stock(soup, has_price: bool) -> str:
    # Check #outOfStock first (definitive when present)
    if soup.select_one("#outOfStock"):
        return "Out of Stock"
    el = soup.select_one("#availability span") or soup.select_one("#availability")
    txt = el.get_text(" ", strip=True).lower() if el else ""

    # Order matters: check unavailable / out-of-stock BEFORE "in stock"
    # because "back in stock" contains "in stock" as a substring.
    unavailable_markers = (
        "currently unavailable",
        "temporarily out of stock",
        "out of stock",
        "we don't know when",
        "this item is unavailable",
        "unavailable",
    )
    for m in unavailable_markers:
        if m in txt:
            return "Out of Stock"

    # Now check for explicit "in stock" using a word-boundary so we don't
    # match "back in stock" inside an unavailable banner.
    if re.search(r"\bin stock\b", txt) and "back in stock" not in txt:
        return "In Stock"
    if "only" in txt and "left in stock" in txt:
        return "In Stock"

    # Fallbacks
    add_to_cart = soup.select_one("#add-to-cart-button")
    buy_now = soup.select_one("#buy-now-button")
    if add_to_cart or buy_now:
        return "In Stock"
    if has_price:
        return "In Stock"
    return "Unknown"


def _parse_delivery(soup) -> str:
    for sel in [
        "#deliveryMessageMirId",
        "#contextualIngressPt",
        "#mir-layout-DELIVERY_BLOCK",
        "#glow-ingress-block",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt[:100]
    return ""


def parse_product(html: str, pincode: str) -> Dict:
    soup = BeautifulSoup(html or "", "html.parser")
    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el else ""
    price = _parse_price(soup)
    seller = _parse_seller(soup)
    rating = _parse_rating(soup)
    reviews = _parse_reviews(soup)
    stock = _parse_stock(soup, bool(price))
    delivery = _parse_delivery(soup)

    # pincode_verified: delivery text mentions the requested pincode (or its city)
    pincode_verified = False
    if delivery and pincode and pincode in delivery:
        pincode_verified = True

    return {
        "title": title,
        "price": price,
        "seller": seller,
        "rating": rating,
        "reviews": reviews,
        "stock": stock,
        "delivery": delivery,
        "pincode_verified": pincode_verified,
    }


def scrape_asin_pincode(asin: str, pincode: str) -> Dict:
    """Scrape a single Amazon India product page using Decodo with pincode injected via geo.
    Returns dict with parsed fields and pincode_verified flag.
    Uses single retry on robot/captcha/short response.
    """
    payload = {
        "url": f"https://www.amazon.in/dp/{asin}",
        "headless": "html",
        "geo": str(pincode),
    }

    data = _decodo_post(payload)
    html = _extract_html(data)

    # Retry once after 3s if blocked / empty / short
    if _is_blocked(html):
        time.sleep(3)
        data = _decodo_post(payload)
        html = _extract_html(data)

    # If still blocked, try fallback without geo to at least get the page (mark pincode-unverified)
    used_pincode = True
    if _is_blocked(html):
        used_pincode = False
        fallback_payload = {
            "url": f"https://www.amazon.in/dp/{asin}",
            "headless": "html",
        }
        data = _decodo_post(fallback_payload)
        html = _extract_html(data)

    parsed = parse_product(html, pincode)
    if not used_pincode:
        parsed["pincode_verified"] = False
    return parsed
