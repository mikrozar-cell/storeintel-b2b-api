"""StoreIntel B2B Intelligence REST API for RapidAPI Hub & Render.

Provides four browserless, high-speed endpoints:
- GET /health
- GET /v1/shopify/inspect
- GET /v1/skool/inspect
- GET /v1/domain/contacts
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SEC = 15.0
MAX_HTML_BYTES = 2_000_000

EMAIL_RX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
IGNORED_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".css", ".js", ".woff", ".woff2")
IGNORED_EMAIL_DOMAINS = ("example.com", "sentry.io", "wixpress.com", "yourdomain.com")

SHOPIFY_APP_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Klaviyo", re.compile(r"klaviyo\.com", re.I)),
    ("Judge.me Reviews", re.compile(r"judge\.me", re.I)),
    ("Loox Reviews", re.compile(r"loox\.io", re.I)),
    ("Yotpo", re.compile(r"yotpo\.com", re.I)),
    ("Okendo Reviews", re.compile(r"okendo\.io", re.I)),
    ("Recharge Subscriptions", re.compile(r"rechargeapps\.com|rechargepayments", re.I)),
    ("Gorgias Helpdesk", re.compile(r"gorgias\.chat", re.I)),
    ("Omnisend", re.compile(r"omnisend\.com", re.I)),
    ("Attentive SMS", re.compile(r"attentivemobile\.com", re.I)),
    ("Postscript SMS", re.compile(r"postscript\.io", re.I)),
    ("Privy Popups", re.compile(r"privy\.com", re.I)),
    ("Meta Pixel", re.compile(r"connect\.facebook\.net|fbq\(", re.I)),
    ("TikTok Pixel", re.compile(r"analytics\.tiktok\.com", re.I)),
    ("Google Analytics 4", re.compile(r"googletagmanager\.com", re.I)),
)

TECH_STACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Next.js", re.compile(r"__NEXT_DATA__|_next/static", re.I)),
    ("React", re.compile(r"data-reactroot|__reactContainer", re.I)),
    ("Shopify", re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I)),
    ("WordPress", re.compile(r"wp-content|wp-includes", re.I)),
    ("Webflow", re.compile(r"webflow\.com|w-mod-js", re.I)),
    ("Stripe", re.compile(r"js\.stripe\.com|checkout\.stripe\.com", re.I)),
    ("HubSpot", re.compile(r"hs-scripts\.com|hubspot\.com", re.I)),
    ("Intercom", re.compile(r"widget\.intercom\.io", re.I)),
    ("Segment", re.compile(r"cdn\.segment\.com", re.I)),
    ("Gumroad", re.compile(r"gumroad\.com", re.I)),
    ("Tailwind CSS", re.compile(r"tailwindcss", re.I)),
    ("Cloudflare", re.compile(r"cloudflareinsights|cdn-cgi/", re.I)),
    ("Google Analytics 4", re.compile(r"googletagmanager\.com", re.I)),
)

SOCIAL_EXTRACTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("linkedin", re.compile(r"https?://(?:www\.)?linkedin\.com/(?:in|company)/[a-zA-Z0-9._%\-/]+", re.I)),
    ("twitter_x", re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/(?!share\b|intent\b)[a-zA-Z0-9_]{1,30}", re.I)),
    ("github", re.compile(r"https?://(?:www\.)?github\.com/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9._\-]+)?", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._]{1,40}", re.I)),
    ("youtube", re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|channel/|c/)[a-zA-Z0-9._\-]+", re.I)),
)

app = FastAPI(
    title="StoreIntel B2B Intelligence API",
    version="1.0.0",
    description=(
        "Browserless REST API for inspecting Shopify storefronts, estimating Skool community MRR, "
        "and extracting verified public emails, social profiles, and tech stacks from any domain."
    ),
)


def verify_rapidapi_secret(x_rapidapi_proxy_secret: str | None = Header(default=None)) -> None:
    """Enforce RapidAPI origin proxy secret when RAPIDAPI_PROXY_SECRET is set."""
    expected = os.getenv("RAPIDAPI_PROXY_SECRET", "").strip()
    if expected and x_rapidapi_proxy_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing RapidAPI proxy secret.")


def _validate_public_url(raw_input: str) -> str:
    """Normalize domain/URL and reject non-HTTP schemes or private/loopback IPs."""
    candidate = raw_input.strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="Target domain or URL cannot be empty.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Only valid public http/https URLs or domains are supported.")

    host = parsed.hostname.lower().strip(".")
    if host in ("localhost", "0.0.0.0") or host.endswith((".local", ".internal", ".localhost")):
        raise HTTPException(status_code=400, detail="Private or local hostnames are not permitted.")

    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail=f"Could not resolve hostname '{host}'.") from exc

    for item in addr_info:
        ip = ipaddress.ip_address(item[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(status_code=400, detail="Target resolves to a restricted non-public IP range.")

    return parsed.geturl()


async def _safe_get(url: str, max_redirects: int = 3) -> httpx.Response:
    """Fetch a public URL safely without following redirects into private network ranges."""
    current = _validate_public_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC, follow_redirects=False, headers=headers) as client:
        for _ in range(max_redirects + 1):
            try:
                resp = await client.get(current)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc.__class__.__name__}") from exc

            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                current = _validate_public_url(urljoin(current, resp.headers["location"]))
                continue
            return resp

    raise HTTPException(status_code=502, detail="Too many redirects from target domain.")


def _extract_emails_and_socials(html: str) -> tuple[list[str], dict[str, list[str]]]:
    emails: list[str] = []
    seen_emails: set[str] = set()
    for match in EMAIL_RX.findall(html):
        lower = match.lower().strip()
        if any(lower.endswith(sfx) for sfx in IGNORED_EMAIL_SUFFIXES):
            continue
        if any(bad in lower for bad in IGNORED_EMAIL_DOMAINS):
            continue
        if lower not in seen_emails:
            seen_emails.add(lower)
            emails.append(lower)
        if len(emails) >= 50:
            break

    socials: dict[str, list[str]] = {}
    for key, pattern in SOCIAL_EXTRACTORS:
        found = []
        seen: set[str] = set()
        for url in pattern.findall(html):
            clean = url.rstrip("/.,;\"'")
            if clean not in seen:
                seen.add(clean)
                found.append(clean)
            if len(found) >= 15:
                break
        socials[key] = found

    return emails, socials


def _parse_nested_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_skool_group(group: dict[str, Any], fallback_url: str) -> dict[str, Any]:
    meta = group.get("metadata") or {}
    slug = group.get("name") or ""
    members = int(meta.get("totalMembers") or meta.get("numMembers") or 0)

    price_usd = 0.0
    dp = _parse_nested_json(meta.get("displayPrice"))
    if dp.get("amount"):
        price_usd = float(dp["amount"]) / 100.0
        if dp.get("recurring_interval") == "year" and price_usd > 0:
            price_usd = price_usd / 12.0
    elif meta.get("currentCoursePrice"):
        price_usd = float(meta["currentCoursePrice"]) / 100.0
    elif meta.get("billingPrice"):
        price_usd = float(meta["billingPrice"]) / 100.0

    price_usd = round(price_usd, 2)
    est_mrr = round(members * price_usd, 2)

    owner_obj = _parse_nested_json(meta.get("owner"))
    owner_name = f"{owner_obj.get('first_name', '')} {owner_obj.get('last_name', '')}".strip() or owner_obj.get("name", "")

    return {
        "name": meta.get("displayName") or slug or "Skool Community",
        "slug": slug,
        "url": f"https://www.skool.com/{slug}/about" if slug else fallback_url,
        "members": members,
        "monthly_price_usd": price_usd,
        "estimated_mrr_usd": est_mrr,
        "estimated_arr_usd": round(est_mrr * 12, 2),
        "owner": owner_name,
        "socials": {
            "website": meta.get("linkWebsite") or "",
            "twitter_x": meta.get("linkTwitter") or "",
            "youtube": meta.get("linkYoutube") or "",
            "instagram": meta.get("linkInstagram") or "",
        },
    }


@app.get("/", tags=["System"])
async def root() -> dict[str, Any]:
    """Welcome metadata endpoint for direct browser visits."""
    return {
        "status": "ok",
        "service": "StoreIntel B2B Intelligence API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": ["/v1/shopify/inspect", "/v1/skool/inspect", "/v1/domain/contacts"],
    }


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    """Liveness endpoint for Render and API uptime monitors."""
    return {"status": "ok", "service": "StoreIntel B2B Intelligence API", "version": "1.0.0"}


@app.get("/v1/shopify/inspect", tags=["E-Commerce Intelligence"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_shopify_store(
    domain: str = Query(..., description="Shopify storefront domain or URL (e.g. tentree.com)", examples=["tentree.com"]),
    limit: int = Query(50, ge=1, le=250, description="Max public products to return from /products.json (1-250)"),
) -> dict[str, Any]:
    """Inspect any public Shopify storefront's theme, installed apps, emails, and catalog pricing."""
    normalized = _validate_public_url(domain)
    parsed = urlparse(normalized)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    home_resp = await _safe_get(origin)
    if home_resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Target store returned HTTP {home_resp.status_code}.")

    html = home_resp.text[:MAX_HTML_BYTES]
    is_shopify = ("cdn.shopify.com" in html) or ("Shopify.theme" in html) or ("shopify-section" in html)

    theme_name = "Custom / Hidden"
    theme_match = re.search(r'Shopify\.theme\s*=\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
    if theme_match:
        theme_name = theme_match.group(1)

    installed_apps = [name for name, rx in SHOPIFY_APP_SIGNATURES if rx.search(html)]
    emails, socials = _extract_emails_and_socials(html)

    products: list[dict[str, Any]] = []
    products_url = f"{origin}/products.json?limit={limit}"
    try:
        prod_resp = await _safe_get(products_url)
        if prod_resp.status_code == 200:
            payload = prod_resp.json()
            for item in payload.get("products", [])[:limit]:
                variant = (item.get("variants") or [{}])[0]
                image = (item.get("images") or [{}])[0].get("src", "")
                handle = item.get("handle") or ""
                products.append({
                    "title": item.get("title") or "",
                    "price": variant.get("price") or "0.00",
                    "compare_at_price": variant.get("compare_at_price") or "",
                    "vendor": item.get("vendor") or "",
                    "product_type": item.get("product_type") or "",
                    "sku": variant.get("sku") or "",
                    "available": bool(variant.get("available")),
                    "product_url": f"{origin}/products/{handle}" if handle else origin,
                    "image_url": image,
                })
    except (HTTPException, ValueError):
        pass

    numeric_prices = []
    for p in products:
        try:
            val = float(p["price"])
            if val > 0:
                numeric_prices.append(val)
        except (TypeError, ValueError):
            continue

    return {
        "domain": parsed.netloc,
        "origin": origin,
        "is_shopify": is_shopify or bool(products),
        "theme_name": theme_name,
        "installed_apps": installed_apps,
        "emails": emails,
        "social_links": socials,
        "pricing_summary": {
            "products_returned": len(products),
            "avg_price": round(sum(numeric_prices) / len(numeric_prices), 2) if numeric_prices else 0.0,
            "min_price": round(min(numeric_prices), 2) if numeric_prices else 0.0,
            "max_price": round(max(numeric_prices), 2) if numeric_prices else 0.0,
        },
        "products": products,
    }


@app.get("/v1/skool/inspect", tags=["Creator Economy"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_skool_community(
    slug_or_url: str = Query(
        ...,
        description="Skool community slug or full skool.com URL (e.g. ai-automation-agency)",
        examples=["ai-automation-agency"],
    ),
) -> dict[str, Any]:
    """Calculate estimated Skool community MRR/ARR and extract founder metadata & social links."""
    raw = slug_or_url.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="slug_or_url cannot be empty.")

    if "://" in raw or "skool.com" in raw.lower():
        normalized = _validate_public_url(raw)
        parsed = urlparse(normalized)
        if parsed.hostname and not parsed.hostname.lower().endswith("skool.com"):
            raise HTTPException(status_code=400, detail="URL must belong to skool.com.")
        parts = [p for p in parsed.path.split("/") if p]
        slug = parts[0] if parts else "discovery"
    else:
        slug = raw.strip("/")

    if not re.fullmatch(r"[a-zA-Z0-9._\-]{1,120}", slug):
        raise HTTPException(status_code=400, detail="Invalid Skool community slug format.")

    target_url = "https://www.skool.com/discovery" if slug == "discovery" else f"https://www.skool.com/{slug}/about"
    resp = await _safe_get(target_url)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Skool returned HTTP {resp.status_code} for '{slug}'.")

    html = resp.text[:MAX_HTML_BYTES]
    next_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if next_match:
        try:
            next_data = json.loads(next_match.group(1))
            page_props = next_data.get("props", {}).get("pageProps", {})
            if slug == "discovery" and isinstance(page_props.get("groups"), list):
                items = [_extract_skool_group(g.get("group") or g, target_url) for g in page_props["groups"]]
                return {"mode": "discovery", "count": len(items), "communities": items}
            if isinstance(page_props.get("currentGroup"), dict):
                return {"mode": "single", "community": _extract_skool_group(page_props["currentGroup"], target_url)}
        except (ValueError, KeyError):
            pass

    raise HTTPException(status_code=404, detail=f"Could not locate Skool community metadata for '{slug}'.")


@app.get("/v1/domain/contacts", tags=["B2B Lead Enrichment"], dependencies=[Depends(verify_rapidapi_secret)])
async def extract_domain_contacts(
    url: str = Query(..., description="Public website domain or URL (e.g. resend.com)", examples=["resend.com"]),
) -> dict[str, Any]:
    """Extract public contact emails, LinkedIn/X/GitHub links, and frontend tech stack signals from any webpage."""
    normalized = _validate_public_url(url)
    resp = await _safe_get(normalized)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Target site returned HTTP {resp.status_code}.")

    html = resp.text[:MAX_HTML_BYTES]
    parsed = urlparse(str(resp.url))
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    page_title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:160] if title_match else ""

    emails, socials = _extract_emails_and_socials(html)
    tech_stack = [name for name, rx in TECH_STACK_PATTERNS if rx.search(html)]

    return {
        "final_url": str(resp.url),
        "domain": parsed.netloc.lower().removeprefix("www."),
        "page_title": page_title,
        "emails": emails,
        "social_links": socials,
        "tech_stack": tech_stack,
    }

