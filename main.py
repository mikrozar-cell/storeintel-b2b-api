"""StoreIntel B2B & E-Commerce Intelligence REST API for RapidAPI Hub & Render.

Exposes all 8 browserless StoreIntel intelligence engines + system endpoints:
- GET /
- GET /health
- GET /v1/shopify/inspect
- GET /v1/shopify/app-reviews
- GET /v1/skool/inspect
- GET /v1/domain/contacts
- GET /v1/ats/jobs
- GET /v1/yc/company
- GET /v1/wordpress/plugin
- GET /v1/substack/posts
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
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
REQUEST_TIMEOUT_SEC = 18.0
MAX_HTML_BYTES = 2_500_000

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

WEB_STACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
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

ATS_STACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS", re.compile(r"\b(AWS|Amazon Web Services|EC2|S3|ECS|EKS|Lambda)\b")),
    ("GCP", re.compile(r"\b(GCP|Google Cloud Platform|BigQuery|GKE|Vertex AI)\b")),
    ("Azure", re.compile(r"\b(Azure|Microsoft Azure|AKS)\b")),
    ("Snowflake", re.compile(r"\bSnowflake\b", re.I)),
    ("Databricks", re.compile(r"\bDatabricks\b", re.I)),
    ("dbt", re.compile(r"\bdbt\b")),
    ("Kubernetes", re.compile(r"\b(Kubernetes|K8s)\b", re.I)),
    ("Terraform", re.compile(r"\bTerraform\b", re.I)),
    ("Datadog", re.compile(r"\bDatadog\b", re.I)),
    ("Sentry", re.compile(r"\bSentry\b", re.I)),
    ("Salesforce", re.compile(r"\b(Salesforce|SFDC)\b", re.I)),
    ("HubSpot", re.compile(r"\bHubSpot\b", re.I)),
    ("Stripe", re.compile(r"\bStripe\b")),
    ("OpenAI", re.compile(r"\b(OpenAI|GPT-4|ChatGPT)\b", re.I)),
    ("Anthropic", re.compile(r"\b(Anthropic|Claude)\b")),
    ("Python", re.compile(r"\bPython\b")),
    ("TypeScript", re.compile(r"\bTypeScript\b", re.I)),
    ("React", re.compile(r"\b(React|Next\.js|ReactJS)\b")),
    ("Go", re.compile(r"\b(Golang|Go language)\b", re.I)),
    ("Rust", re.compile(r"\bRust\b")),
    ("PostgreSQL", re.compile(r"\b(PostgreSQL|Postgres)\b", re.I)),
)

PAIN_POINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pricing_billing", re.compile(r"\b(expensive|overpriced|price|pricing|charged|billing|refund|subscription|fee)\b", re.I)),
    ("customer_support", re.compile(r"\b(support|customer service|no response|unhelpful|rude|ignored|ticket|reply)\b", re.I)),
    ("bugs_reliability", re.compile(r"\b(bug|bugs|broken|crash|error|glitch|not working|stopped working|issue|failed)\b", re.I)),
    ("performance_speed", re.compile(r"\b(slow|slowed|speed|lag|loading|page speed|heavy)\b", re.I)),
    ("missing_features", re.compile(r"\b(missing|lack|lacks|limited|basic|no option|limitation)\b", re.I)),
    ("usability_setup", re.compile(r"\b(complicated|confusing|hard to use|difficult|setup|install|clunky|ux|ui)\b", re.I)),
)

SALARY_RANGE_RE = re.compile(
    r"\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3}\s*[kK])\s*(?:-|–|—|to)\s*\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3}\s*[kK])"
)

SOCIAL_EXTRACTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("linkedin", re.compile(r"https?://(?:www\.)?linkedin\.com/(?:in|company)/[a-zA-Z0-9._%\-/]+", re.I)),
    ("twitter_x", re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/(?!share\b|intent\b)[a-zA-Z0-9_]{1,30}", re.I)),
    ("github", re.compile(r"https?://(?:www\.)?github\.com/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9._\-]+)?", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._]{1,40}", re.I)),
    ("youtube", re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|channel/|c/)[a-zA-Z0-9._\-]+", re.I)),
)

app = FastAPI(
    title="StoreIntel B2B & E-Commerce Intelligence API",
    version="2.0.0",
    description=(
        "Complete suite of 8 browserless B2B data enrichment endpoints: Shopify stores, "
        "Shopify App Store 1-star churn leads, Skool community MRR, universal website contacts/tech stack, "
        "Greenhouse/Lever/Ashby ATS jobs, Y Combinator founders, WordPress plugins, and Substack engagement."
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


def _safe_slug(raw: str, strip_prefix: str = "") -> str:
    s = (raw or "").strip()
    if "://" in s or "/" in s:
        if "://" not in s:
            s = "https://" + s
        parts = [p for p in urlparse(s).path.split("/") if p]
        if strip_prefix and strip_prefix in parts:
            idx = parts.index(strip_prefix)
            s = parts[idx + 1] if idx + 1 < len(parts) else (parts[-1] if parts else "")
        else:
            s = parts[0] if parts else ""
    s = s.split("?")[0].split("#")[0].lower().strip("/")
    if not s or not re.fullmatch(r"[a-z0-9._\-]{1,120}", s):
        raise HTTPException(status_code=400, detail="Invalid slug or URL format.")
    return s


def _clean_html_text(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    txt = unescape(raw_html)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _detect_patterns(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[str]:
    if not text:
        return []
    return [name for name, rx in patterns if rx.search(text)]


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


def _parse_shopify_app_reviews(html: str, app_handle: str, limit: int) -> list[dict[str, Any]]:
    chunks = html.split("data-merchant-review")
    if len(chunks) <= 1:
        return []

    reviews: list[dict[str, Any]] = []
    for chunk in chunks[1:]:
        id_match = re.search(r'data-review-(?:content-)?id="([^"]+)"', chunk)
        star_match = re.search(r'aria-label="(\d)\s+out of 5 stars"', chunk)
        copy_match = re.search(r'data-truncate-content-copy[^>]*>(.*?)</div>', chunk, re.S)
        date_match = re.search(r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", chunk)
        merchant_match = re.search(r'tw-order-1.*?<span[^>]*title="([^"]+)"', chunk, re.S)
        if not merchant_match:
            merchant_match = re.search(r'tw-order-1.*?tw-text-heading-xs[^>]*>(.*?)</', chunk, re.S)

        usage_match = re.search(r">([^<]*(?:using the app|free plan)[^<]*)<", chunk, re.I)
        review_text = _clean_html_text(copy_match.group(1)) if copy_match else ""
        if not review_text and not merchant_match:
            continue

        reviews.append({
            "app_handle": app_handle,
            "review_id": id_match.group(1) if id_match else "",
            "rating": int(star_match.group(1)) if star_match else None,
            "merchant_store": unescape(merchant_match.group(1)).strip() if merchant_match else "Shopify Merchant",
            "usage_duration": _clean_html_text(usage_match.group(1)) if usage_match else "",
            "published_at": date_match.group(1) if date_match else "",
            "pain_points": _detect_patterns(review_text, PAIN_POINT_PATTERNS),
            "review_text": review_text[:800],
        })
        if len(reviews) >= limit:
            break
    return reviews


def _extract_salary(text: str) -> str | None:
    if not text:
        return None
    m = SALARY_RANGE_RE.search(text)
    if not m:
        return None
    try:
        vals = []
        for raw in (m.group(1), m.group(2)):
            s = raw.lower().replace(",", "").strip()
            vals.append(float(s[:-1].strip()) * 1000.0 if s.endswith("k") else float(s))
        if 20_000 <= vals[0] <= vals[1] <= 2_500_000:
            return f"${int(vals[0]):,} - ${int(vals[1]):,} USD"
    except ValueError:
        pass
    return None


@app.get("/", tags=["System"])
async def root() -> dict[str, Any]:
    """Welcome metadata endpoint for direct browser visits."""
    return {
        "status": "ok",
        "service": "StoreIntel B2B & E-Commerce Intelligence API",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": [
            "/v1/shopify/inspect",
            "/v1/shopify/app-reviews",
            "/v1/skool/inspect",
            "/v1/domain/contacts",
            "/v1/ats/jobs",
            "/v1/yc/company",
            "/v1/wordpress/plugin",
            "/v1/substack/posts",
        ],
    }


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    """Liveness endpoint for Render and API uptime monitors."""
    return {"status": "ok", "service": "StoreIntel B2B Intelligence API", "version": "2.0.0"}


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

    installed_apps = _detect_patterns(html, SHOPIFY_APP_SIGNATURES)
    emails, socials = _extract_emails_and_socials(html)

    products: list[dict[str, Any]] = []
    try:
        prod_resp = await _safe_get(f"{origin}/products.json?limit={limit}")
        if prod_resp.status_code == 200:
            for item in prod_resp.json().get("products", [])[:limit]:
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


@app.get("/v1/shopify/app-reviews", tags=["SaaS Churn Intelligence"], dependencies=[Depends(verify_rapidapi_secret)])
async def get_shopify_app_reviews(
    app_slug: str = Query(..., description="Shopify App handle or apps.shopify.com URL", examples=["klaviyo-email-marketing"]),
    rating: int = Query(1, ge=1, le=5, description="Filter reviews by star rating (1 = churn leads)"),
    limit: int = Query(20, ge=1, le=50, description="Max reviews to return"),
) -> dict[str, Any]:
    """Extract competitor 1-star to 5-star reviews & frustrated merchant leads from the Shopify App Store."""
    handle = _safe_slug(app_slug)
    url = f"https://apps.shopify.com/{handle}/reviews?ratings%5B%5D={rating}&sort_by=newest&page=1"
    resp = await _safe_get(url)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Shopify App Store returned HTTP {resp.status_code} for '{handle}'.")

    reviews = _parse_shopify_app_reviews(resp.text[:MAX_HTML_BYTES], handle, limit)
    return {
        "app_handle": handle,
        "source_url": url,
        "rating_filter": rating,
        "count": len(reviews),
        "reviews": reviews,
    }


@app.get("/v1/skool/inspect", tags=["Creator Economy"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_skool_community(
    slug_or_url: str = Query(..., description="Skool community slug (or 'discovery') or full skool.com URL", examples=["ai-automation-agency"]),
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
    page_title = re.sub(r"\s+", " ", unescape(title_match.group(1))).strip()[:160] if title_match else ""

    emails, socials = _extract_emails_and_socials(html)
    tech_stack = _detect_patterns(html, WEB_STACK_PATTERNS)

    return {
        "final_url": str(resp.url),
        "domain": parsed.netloc.lower().removeprefix("www."),
        "page_title": page_title,
        "emails": emails,
        "social_links": socials,
        "tech_stack": tech_stack,
    }


@app.get("/v1/ats/jobs", tags=["Hiring & Tech Stack Intent"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_ats_jobs(
    board_url: str = Query(
        ...,
        description="Greenhouse, Lever, or Ashby job board URL or company slug (e.g. https://boards.greenhouse.io/stripe)",
        examples=["https://boards.greenhouse.io/stripe"],
    ),
    limit: int = Query(20, ge=1, le=50, description="Max job postings to inspect"),
) -> dict[str, Any]:
    """Fetch open roles from Greenhouse, Lever, or AshbyHQ with salary ranges and B2B tech stack signals."""
    raw = board_url.strip()
    low = raw.lower()
    hint = "greenhouse" if "greenhouse.io" in low else ("lever" if "lever.co" in low else ("ashby" if "ashbyhq.com" in low else ""))
    slug = _safe_slug(raw, strip_prefix="boards")
    if hint == "" and "jobs.lever.co" in low:
        hint = "lever"

    providers = [hint] if hint else ["greenhouse", "lever", "ashby"]
    jobs: list[dict[str, Any]] = []
    detected_ats = ""

    for ats in providers:
        if ats == "greenhouse":
            resp = await _safe_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            if resp.status_code == 200:
                raw_jobs = resp.json().get("jobs", [])
                if isinstance(raw_jobs, list):
                    detected_ats = "greenhouse"
                    for j in raw_jobs[:limit]:
                        title = str(j.get("title") or "")
                        desc = _clean_html_text(j.get("content"))
                        loc = (j.get("location") or {}).get("name") if isinstance(j.get("location"), dict) else None
                        depts = j.get("departments") or []
                        dept = depts[0].get("name") if depts and isinstance(depts[0], dict) else None
                        jobs.append({
                            "title": title,
                            "department": dept,
                            "location": loc,
                            "is_remote": bool((loc and "remote" in loc.lower()) or "remote" in title.lower()),
                            "salary": _extract_salary(desc),
                            "tech_stack": _detect_patterns(f"{title} {desc}", ATS_STACK_PATTERNS),
                            "job_url": j.get("absolute_url"),
                        })
                    break
        elif ats == "lever":
            resp = await _safe_get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if resp.status_code == 200 and isinstance(resp.json(), list):
                detected_ats = "lever"
                for j in resp.json()[:limit]:
                    title = str(j.get("text") or "")
                    cats = j.get("categories") if isinstance(j.get("categories"), dict) else {}
                    loc = cats.get("location")
                    desc = f"{j.get('descriptionPlain') or ''} {j.get('additionalPlain') or ''}".strip()
                    jobs.append({
                        "title": title,
                        "department": cats.get("department") or cats.get("team"),
                        "location": loc,
                        "is_remote": bool(str(j.get("workplaceType") or "").lower() == "remote" or (loc and "remote" in str(loc).lower())),
                        "salary": _extract_salary(desc),
                        "tech_stack": _detect_patterns(f"{title} {desc}", ATS_STACK_PATTERNS),
                        "job_url": j.get("hostedUrl") or j.get("applyUrl"),
                    })
                break
        elif ats == "ashby":
            resp = await _safe_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
            if resp.status_code == 200 and isinstance(resp.json().get("jobs"), list):
                detected_ats = "ashby"
                for j in resp.json()["jobs"][:limit]:
                    title = str(j.get("title") or "")
                    desc = str(j.get("descriptionPlain") or _clean_html_text(j.get("descriptionHtml")))
                    comp = j.get("compensation") if isinstance(j.get("compensation"), dict) else {}
                    sal = comp.get("scrapeableCompensationSalarySummary") or _extract_salary(desc)
                    jobs.append({
                        "title": title,
                        "department": j.get("department") or j.get("team"),
                        "location": j.get("location"),
                        "is_remote": bool(j.get("isRemote") or str(j.get("workplaceType") or "").lower() == "remote"),
                        "salary": sal,
                        "tech_stack": _detect_patterns(f"{title} {desc}", ATS_STACK_PATTERNS),
                        "job_url": j.get("jobUrl") or j.get("applyUrl"),
                    })
                break

    if not detected_ats:
        raise HTTPException(status_code=404, detail=f"No Greenhouse, Lever, or Ashby job board found for '{slug}'.")

    stack_freq: dict[str, int] = {}
    for job in jobs:
        for tech in job["tech_stack"]:
            stack_freq[tech] = stack_freq.get(tech, 0) + 1

    return {
        "company_slug": slug,
        "ats_platform": detected_ats,
        "jobs_returned": len(jobs),
        "company_tech_stack_signals": sorted(stack_freq.keys(), key=lambda k: -stack_freq[k]),
        "jobs": jobs,
    }


@app.get("/v1/yc/company", tags=["Startup & Founder Intelligence"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_yc_company(
    slug: str = Query(..., description="Y Combinator company slug or URL (e.g. supabase, stripe)", examples=["supabase"]),
) -> dict[str, Any]:
    """Extract Y Combinator startup profile, batch, headcount, and founders' LinkedIn/X URLs."""
    company_slug = _safe_slug(slug, strip_prefix="companies")
    resp = await _safe_get(f"https://www.ycombinator.com/companies/{company_slug}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=404, detail=f"YC company '{company_slug}' not found (HTTP {resp.status_code}).")

    match = re.search(r'data-page="([^"]+)"', resp.text[:MAX_HTML_BYTES])
    if not match:
        raise HTTPException(status_code=404, detail=f"Could not extract YC structured metadata for '{company_slug}'.")

    comp = json.loads(unescape(match.group(1))).get("props", {}).get("company")
    if not isinstance(comp, dict) or not comp.get("slug"):
        raise HTTPException(status_code=404, detail=f"YC company payload empty for '{company_slug}'.")

    founders = []
    for f in comp.get("founders") or []:
        if isinstance(f, dict) and f.get("full_name"):
            founders.append({
                "full_name": f.get("full_name"),
                "title": f.get("title") or "Founder",
                "linkedin_url": f.get("linkedin_url"),
                "twitter_url": f.get("twitter_url"),
            })

    return {
        "company_slug": comp.get("slug"),
        "company_name": comp.get("name"),
        "batch_name": comp.get("batch_name") or comp.get("batch"),
        "status": comp.get("ycdc_status"),
        "team_size": comp.get("team_size"),
        "website": comp.get("website"),
        "one_liner": comp.get("one_liner"),
        "location": comp.get("location") or comp.get("city"),
        "tags": comp.get("tags") or [],
        "company_linkedin_url": comp.get("linkedin_url"),
        "company_twitter_url": comp.get("twitter_url"),
        "founders": founders,
    }


@app.get("/v1/wordpress/plugin", tags=["WordPress Ecosystem"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_wordpress_plugin(
    slug: str = Query(..., description="WordPress.org plugin slug or URL (e.g. woocommerce, elementor)", examples=["woocommerce"]),
) -> dict[str, Any]:
    """Fetch WordPress.org plugin active installs, rating, support resolution rate, and developer metadata."""
    plugin_slug = _safe_slug(slug, strip_prefix="plugins")
    url = (
        "https://api.wordpress.org/plugins/info/1.2/?action=plugin_information"
        f"&request[slug]={plugin_slug}"
        "&request[fields][short_description]=1"
        "&request[fields][active_installs]=1"
        "&request[fields][downloaded]=1"
        "&request[fields][support_threads]=1"
        "&request[fields][support_threads_resolved]=1"
        "&request[fields][business_model]=1"
        "&request[fields][commercial_support_url]=1"
        "&request[fields][sections]=0&request[fields][versions]=0"
    )
    resp = await _safe_get(url)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"WordPress.org API returned HTTP {resp.status_code}.")

    data = resp.json()
    if not isinstance(data, dict) or not data.get("slug"):
        raise HTTPException(status_code=404, detail=f"WordPress plugin '{plugin_slug}' not found.")

    st_total = int(data.get("support_threads") or 0)
    st_resolved = int(data.get("support_threads_resolved") or 0)
    num_ratings = int(data.get("num_ratings") or 0)
    rating_100 = data.get("rating")

    return {
        "slug": data.get("slug"),
        "name": unescape(str(data.get("name") or plugin_slug)),
        "version": data.get("version"),
        "author": _clean_html_text(str(data.get("author") or "")),
        "active_installs": data.get("active_installs"),
        "downloaded": data.get("downloaded"),
        "rating_5": round(float(rating_100) / 20.0, 2) if rating_100 is not None and num_ratings > 0 else None,
        "num_ratings": num_ratings,
        "support_threads": st_total,
        "support_threads_resolved": st_resolved,
        "support_resolution_rate_pct": round((st_resolved / st_total) * 100.0, 1) if st_total > 0 else None,
        "last_updated": str(data.get("last_updated") or "")[:10],
        "business_model": data.get("business_model"),
        "commercial_support_url": data.get("commercial_support_url"),
        "short_description": unescape(str(data.get("short_description") or "")),
    }


@app.get("/v1/substack/posts", tags=["Newsletter & Media Outreach"], dependencies=[Depends(verify_rapidapi_secret)])
async def inspect_substack_posts(
    subdomain: str = Query(..., description="Substack subdomain or newsletter URL (e.g. pragmaticengineer)", examples=["pragmaticengineer"]),
    limit: int = Query(10, ge=1, le=25, description="Max recent posts to return"),
) -> dict[str, Any]:
    """Fetch Substack newsletter author profile, engagement averages, and recent posts via public API."""
    s = subdomain.strip()
    if not s:
        raise HTTPException(status_code=400, detail="subdomain cannot be empty.")

    if "://" in s or "." in s:
        normalized = _validate_public_url(s)
        host = urlparse(normalized).netloc.lower()
        handle = host.replace(".substack.com", "")
        base_url = f"https://{host}"
    else:
        handle = _safe_slug(s)
        base_url = f"https://{handle}.substack.com"

    qs = f"sort=new&limit={limit}"
    resp = await _safe_get(f"{base_url}/api/v1/archive?{qs}")
    if resp.status_code != 200 and ".substack.com" in base_url:
        alt_base = f"https://www.{handle}.com"
        resp2 = await _safe_get(f"{alt_base}/api/v1/archive?{qs}")
        if resp2.status_code == 200:
            resp = resp2
            base_url = alt_base

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail=f"Could not fetch Substack archive for '{handle}'.")

    raw_data = resp.json()
    raw_posts = raw_data if isinstance(raw_data, list) else (raw_data.get("posts") if isinstance(raw_data, dict) else [])
    posts: list[dict[str, Any]] = []
    primary_author: dict[str, Any] = {}

    for p in (raw_posts or [])[:limit]:
        if not isinstance(p, dict):
            continue
        bylines = p.get("publishedBylines") if isinstance(p.get("publishedBylines"), list) else []
        by0 = bylines[0] if bylines and isinstance(bylines[0], dict) else {}
        if not primary_author and by0:
            tw = by0.get("twitter_screen_name")
            primary_author = {
                "name": by0.get("name"),
                "handle": by0.get("handle"),
                "bio": by0.get("bio"),
                "twitter_x": f"https://x.com/{tw}" if tw else None,
            }
        posts.append({
            "title": p.get("title"),
            "subtitle": p.get("subtitle"),
            "published_at": p.get("post_date"),
            "audience": p.get("audience") or "everyone",
            "reaction_count": int(p.get("reaction_count") or 0),
            "comment_count": int(p.get("comment_count") or 0),
            "restack_count": int(p.get("restacks") or 0),
            "word_count": int(p.get("wordcount") or 0),
            "post_url": p.get("canonical_url") or f"{base_url}/p/{p.get('slug')}",
        })

    avg_react = round(sum(x["reaction_count"] for x in posts) / len(posts), 1) if posts else 0.0
    avg_comm = round(sum(x["comment_count"] for x in posts) / len(posts), 1) if posts else 0.0

    return {
        "publication_handle": handle,
        "publication_url": base_url,
        "primary_author": primary_author,
        "posts_returned": len(posts),
        "avg_reactions": avg_react,
        "avg_comments": avg_comm,
        "posts": posts,
    }
