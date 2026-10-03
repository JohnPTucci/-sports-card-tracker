import base64
import time

import httpx

from config import (
    EBAY_CLIENT_ID,
    EBAY_CLIENT_SECRET,
    EBAY_MARKETPLACE_ID,
    EBAY_AFFILIATE_CAMPAIGN_ID,
    DEFAULT_CATEGORY_ID,
    MAX_ITEMS_PER_SEARCH,
)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

_token_cache = {"token": None, "expires_at": 0}


class EbayConfigError(Exception):
    pass


async def _get_app_token() -> str:
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise EbayConfigError(
            "Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET. Copy .env.example to .env "
            "and fill in your eBay Developer production keys."
        )

    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    creds = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TOKEN_URL, headers=headers, data=data)
        resp.raise_for_status()
        payload = resp.json()

    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = time.time() + payload.get("expires_in", 7200)
    return _token_cache["token"]


def _normalize_item(item: dict) -> dict:
    price_info = item.get("price") or {}
    image = item.get("image") or {}
    # When an EPN campaign ID is configured, eBay returns itemAffiliateWebUrl.
    # Always prefer it: it's the link that credits the commission.
    url = item.get("itemAffiliateWebUrl") or item.get("itemWebUrl")
    return {
        "item_id": item.get("itemId"),
        "title": item.get("title"),
        "price": float(price_info.get("value")) if price_info.get("value") else None,
        "currency": price_info.get("currency"),
        "condition": item.get("condition"),
        "url": url,
        "image_url": image.get("imageUrl"),
    }


async def search_listings(
    query: str,
    category_id: str = DEFAULT_CATEGORY_ID,
    max_items: int = MAX_ITEMS_PER_SEARCH,
):
    """Search active eBay listings via the Browse API, paging until max_items."""
    token = await _get_app_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
    }
    if EBAY_AFFILIATE_CAMPAIGN_ID:
        headers["X-EBAY-C-ENDUSERCTX"] = (
            f"affiliateCampaignId={EBAY_AFFILIATE_CAMPAIGN_ID},"
            f"affiliateReferenceId=cardtracker"
        )

    results, seen = [], set()
    offset = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while len(results) < max_items:
            params = {
                "q": query,
                "limit": min(200, max_items - len(results)),
                "offset": offset,
            }
            if category_id:
                params["category_ids"] = category_id

            resp = await client.get(SEARCH_URL, headers=headers, params=params)
            resp.raise_for_status()
            payload = resp.json()

            items = payload.get("itemSummaries", [])
            if not items:
                break
            for item in items:
                normalized = _normalize_item(item)
                if normalized["item_id"] and normalized["item_id"] not in seen:
                    seen.add(normalized["item_id"])
                    results.append(normalized)

            offset += len(items)
            if offset >= payload.get("total", 0):
                break

    return results
