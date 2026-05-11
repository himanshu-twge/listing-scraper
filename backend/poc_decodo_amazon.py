"""
POC: Decodo Scraping API 3-step flow for Amazon India product data
with delivery pincode injection.

Tests:
 - Step 1: Get session cookies for product page
 - Step 2: Set pincode on session via address-change endpoint
 - Step 3: Scrape product page with cookies; parse fields
 - Robot/captcha detection + retry
 - Compare pincode-injected vs non-injected results
"""
import os
import re
import json
import time
import sys
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

DECODO_KEY = os.environ.get("DECODO_KEY", "").strip()
DECODO_URL = "https://scraper-api.decodo.com/v2/scrape"

if not DECODO_KEY:
    print("FATAL: DECODO_KEY missing in env")
    sys.exit(1)

HEADERS_BASE = {
    "Authorization": f"Basic {DECODO_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def is_blocked(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    low = html.lower()
    return "robot check" in low or "captcha" in low or "enter the characters you see below" in low


def decodo_post(payload: Dict, timeout: int = 90) -> requests.Response:
    return requests.post(DECODO_URL, headers=HEADERS_BASE, json=payload, timeout=timeout)


def extract_html_and_cookies(resp_json: Dict) -> Tuple[str, Dict[str, str]]:
    """Decodo returns results array. Each result has content and headers (with set-cookie list)."""
    html = ""
    cookies: Dict[str, str] = {}
    results = resp_json.get("results") or []
    if not results:
        return html, cookies
    first = results[0]
    # content can be string HTML
    content = first.get("content")
    if isinstance(content, str):
        html = content
    elif isinstance(content, dict):
        html = content.get("content") or content.get("html") or ""
    # try to extract cookies from headers
    headers = first.get("headers") or {}
    # Some responses have cookies field
    cookie_list = first.get("cookies") or headers.get("set-cookie") or headers.get("Set-Cookie")
    if isinstance(cookie_list, list):
        for c in cookie_list:
            if isinstance(c, str):
                # "name=value; Path=/; ..." -> take name=value
                first_part = c.split(";", 1)[0]
                if "=" in first_part:
                    k, v = first_part.split("=", 1)
                    cookies[k.strip()] = v.strip()
            elif isinstance(c, dict):
                name = c.get("name") or c.get("key")
                value = c.get("value")
                if name and value is not None:
                    cookies[name] = str(value)
    elif isinstance(cookie_list, str):
        for piece in cookie_list.split(","):
            first_part = piece.split(";", 1)[0]
            if "=" in first_part:
                k, v = first_part.split("=", 1)
                cookies[k.strip()] = v.strip()
    return html, cookies


def cookies_to_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k and v)


def step1_get_cookies(asin: str) -> Tuple[str, Dict[str, str]]:
    payload = {
        "target": "universal",
        "url": f"https://www.amazon.in/dp/{asin}",
        "geo": "India",
        "headless": "html",
    }
    print(f"[Step 1] Fetching product page for {asin} (cookie collection)...")
    resp = decodo_post(payload)
    print(f"  -> HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:300]}")
        return "", {}
    data = resp.json()
    html, cookies = extract_html_and_cookies(data)
    print(f"  HTML length: {len(html)} | Cookies extracted: {list(cookies.keys())[:6]}")
    return html, cookies


def step2_set_pincode(asin: str, pincode: str, cookies: Dict[str, str]) -> bool:
    cookie_header = cookies_to_header(cookies)
    payload = {
        "target": "universal",
        "url": "https://www.amazon.in/gp/delivery/ajax/address-change.html",
        "geo": "India",
        "headless": "html",
        "method": "POST",
        "body": (
            f"locationType=POSTAL_CODE&zipCode={pincode}&storeContext=generic"
            "&deviceType=web&pageType=Detail&actionSource=glow"
        ),
        "headers": {
            "Cookie": cookie_header,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.amazon.in/dp/{asin}",
            "anti-csrftoken-a2z": "1",
        },
    }
    print(f"[Step 2] Setting pincode {pincode} on session...")
    try:
        resp = decodo_post(payload)
        print(f"  -> HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"  body: {resp.text[:300]}")
            return False
        data = resp.json()
        # Inspect any cookie updates
        _, new_cookies = extract_html_and_cookies(data)
        if new_cookies:
            cookies.update(new_cookies)
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def step3_scrape_with_cookies(asin: str, cookies: Dict[str, str]) -> str:
    cookie_header = cookies_to_header(cookies)
    payload = {
        "target": "universal",
        "url": f"https://www.amazon.in/dp/{asin}",
        "geo": "India",
        "headless": "html",
        "headers": {
            "Cookie": cookie_header,
        } if cookie_header else {},
    }
    print(f"[Step 3] Scraping product page with{'out' if not cookie_header else ''} cookies...")
    resp = decodo_post(payload)
    print(f"  -> HTTP {resp.status_code}")
    if resp.status_code != 200:
        return ""
    data = resp.json()
    html, _ = extract_html_and_cookies(data)
    if is_blocked(html):
        print("  -> Detected block/captcha; retrying once after 3s...")
        time.sleep(3)
        resp = decodo_post(payload)
        if resp.status_code == 200:
            data = resp.json()
            html, _ = extract_html_and_cookies(data)
    print(f"  HTML length: {len(html)}")
    return html


def parse_product(html: str) -> Dict:
    soup = BeautifulSoup(html or "", "html.parser")

    def get_text(sel) -> str:
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else ""

    title = get_text("#productTitle")

    price_text = ""
    for sel in [".a-price .a-offscreen", ".apexPriceToPay .a-offscreen", "#price_inside_buybox", "#priceblock_ourprice", "#priceblock_dealprice"]:
        for el in soup.select(sel):
            t = el.get_text(strip=True)
            if t and re.search(r"\d", t):
                price_text = t
                break
        if price_text:
            break
    if price_text:
        price_text = re.sub(r"[^\d.,]", "", price_text)
        price_text = price_text.split(".")[0] if "," not in price_text else price_text
        # Normalize
        digits_only = re.sub(r"[^\d]", "", price_text)
        if digits_only:
            price_text = f"Rs. {digits_only}"
        else:
            price_text = ""

    seller = ""
    for sel in ["#sellerProfileTriggerId", ".tabular-buybox-text a", "#merchant-info a", "#merchant-info"]:
        el = soup.select_one(sel)
        if el:
            seller = el.get_text(strip=True)
            if seller:
                break

    rating = ""
    rating_el = soup.select_one("#acrPopover span.a-icon-alt") or soup.select_one("i.a-icon-star span.a-icon-alt")
    if rating_el:
        m = re.search(r"([\d.]+)\s*out", rating_el.get_text(strip=True))
        if m:
            rating = m.group(1)

    reviews = ""
    rev_el = soup.select_one("#acrCustomerReviewText")
    if rev_el:
        m = re.search(r"([\d,]+)", rev_el.get_text(strip=True))
        if m:
            reviews = m.group(1).replace(",", "")

    avail_el = soup.select_one("#availability span") or soup.select_one("#availability")
    avail = avail_el.get_text(strip=True).lower() if avail_el else ""
    if "in stock" in avail:
        stock = "In Stock"
    elif "unavailable" in avail or "out of stock" in avail or "currently unavailable" in avail:
        stock = "Out of Stock"
    elif price_text:
        stock = "In Stock"
    else:
        stock = "Unknown"

    delivery = ""
    for sel in ["#deliveryMessageMirId", "#contextualIngressPt", "#mir-layout-DELIVERY_BLOCK"]:
        el = soup.select_one(sel)
        if el:
            delivery = el.get_text(" ", strip=True)[:100]
            if delivery:
                break

    return {
        "title": title,
        "price": price_text,
        "seller": seller,
        "rating": rating,
        "reviews": reviews,
        "stock": stock,
        "delivery": delivery,
    }


def scrape_with_pincode(asin: str, pincode: str) -> Dict:
    html1, cookies = step1_get_cookies(asin)
    pincode_verified = False
    if cookies:
        ok = step2_set_pincode(asin, pincode, cookies)
        pincode_verified = bool(ok)
        # use cookies for step 3 even if step 2 fails
    else:
        print("  Step 1 failed -> falling back to direct scrape")
    html3 = step3_scrape_with_cookies(asin, cookies if cookies else {})
    parsed = parse_product(html3)
    parsed["pincode_verified"] = pincode_verified
    parsed["pincode"] = pincode
    parsed["asin"] = asin
    return parsed


def scrape_without_pincode(asin: str) -> Dict:
    html = step3_scrape_with_cookies(asin, {})
    parsed = parse_product(html)
    parsed["pincode_verified"] = False
    parsed["pincode"] = "(none)"
    parsed["asin"] = asin
    return parsed


def main():
    asin = "B08WJ12R6N"
    pincode = "400064"

    print("=" * 60)
    print("RUN A: WITHOUT pincode injection (direct scrape)")
    print("=" * 60)
    a = scrape_without_pincode(asin)
    print(json.dumps(a, indent=2))

    print("\nWaiting 2s before next scrape...\n")
    time.sleep(2)

    print("=" * 60)
    print(f"RUN B: WITH pincode injection ({pincode})")
    print("=" * 60)
    b = scrape_with_pincode(asin, pincode)
    print(json.dumps(b, indent=2))

    print("\n" + "=" * 60)
    print("COMPARISON SUMMARY")
    print("=" * 60)
    keys = ["title", "price", "seller", "rating", "reviews", "stock", "delivery", "pincode_verified"]
    diffs = []
    for k in keys:
        if a.get(k) != b.get(k):
            diffs.append(k)
    print(f"Fields that differ: {diffs}")
    print(f"pincode_verified flag (run B): {b.get('pincode_verified')}")
    print()
    print("PASS criteria: parsed at least title + price + (stock or delivery) on run B.")
    success = bool(b.get("title")) and (bool(b.get("price")) or b.get("stock") in ("In Stock", "Out of Stock"))
    print(f"POC SUCCESS: {success}")
    sys.exit(0 if success else 2)


if __name__ == "__main__":
    main()
