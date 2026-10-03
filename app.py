import csv
import io
import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import ebay_client
import setlist_catalog
from matcher import ChecklistMatcher

app = FastAPI(title="Sports Card Tracker")
db.init_db()


# ======================================================================
# Catalog browsing (Sport -> Manufacturer -> Year -> Set) + auto-import
# ======================================================================

@app.get("/api/catalog")
def api_catalog():
    """The full browsable catalog, sport -> manufacturer -> year -> [set entries]."""
    return setlist_catalog.load_catalog()


@app.post("/api/catalog/{slug}/load")
async def api_load_catalog_set(slug: str):
    """Pick a set from the catalog. If its checklist is already cached locally,
    just return that set. Otherwise fetch it from SetList, store it, and return
    the newly created set. Idempotent from the frontend's point of view — this
    is the single call the cascading picker makes on "Select"."""
    existing = db.find_set_by_slug(slug)
    if existing:
        return {"id": existing["id"], "already_loaded": True}

    entry = setlist_catalog.find_entry(slug)
    if not entry:
        raise HTTPException(404, f"'{slug}' isn't in the catalog.")

    if db.set_exists(entry["display_name"]):
        raise HTTPException(
            400,
            f'A set named "{entry["display_name"]}" already exists locally (probably '
            "imported manually before). Remove it first to load the catalog version.",
        )

    try:
        rows = await setlist_catalog.fetch_checklist_rows(slug)
    except setlist_catalog.SetlistFetchError as e:
        raise HTTPException(502, str(e))
    if not rows:
        raise HTTPException(502, f"SetList returned no cards for '{slug}'.")

    set_id = db.create_set(
        entry["display_name"],
        entry["year"],
        entry["sport"],
        entry["display_name"],
        catalog_slug=slug,
        checklist_source="setlist",
    )
    db.add_cards(set_id, rows)
    return {"id": set_id, "already_loaded": False, "cards_imported": len(rows)}


# ======================================================================
# Checklist CSV import
# ======================================================================

CHECKLIST_ALIASES = {
    "card_number": {"card #", "card number", "card no", "card no.", "no", "no.", "number", "#",
                    "card", "cardnumber", "card_number", "card num"},
    "player": {"player", "player name", "name", "athlete", "full name", "player_name"},
    "team": {"team", "team name", "club"},
    "subset": {"subset", "insert", "set", "card set", "parallel", "card type", "type",
               "variation", "subset name", "set name", "program"},
    "print_run": {"print run", "numbered", "serial", "serial #", "print_run", "run", "sn"},
}
HIT_WORDS = re.compile(
    r"auto|signature|signed|relic|patch|jersey|memorabilia|swatch|material|rpa", re.I
)


def _header_map(fieldnames, aliases):
    mapping = {}
    for raw in fieldnames or []:
        key = re.sub(r"\s+", " ", (raw or "").strip().lower().lstrip("﻿"))
        for field, names in aliases.items():
            if key in names and field not in mapping:
                mapping[field] = raw
    return mapping


def _sniff_reader(text):
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(io.StringIO(text), dialect=dialect)


def parse_checklist_csv(text):
    reader = _sniff_reader(text)
    mapping = _header_map(reader.fieldnames, CHECKLIST_ALIASES)
    if "player" not in mapping or "card_number" not in mapping:
        raise ValueError(
            "Couldn't find the required columns. The CSV needs a card number column "
            "(e.g. 'Card #') and a player column (e.g. 'Player'). "
            f"Columns found: {', '.join(reader.fieldnames or []) or 'none'}"
        )

    rows, skipped = [], 0
    for rec in reader:
        get = lambda f: (rec.get(mapping[f]) or "").strip() if f in mapping else ""
        player = get("player")
        if not player:
            skipped += 1
            continue
        subset = get("subset")
        rows.append({
            "card_number": get("card_number"),
            "player": player,
            "team": get("team"),
            "subset": subset,
            "print_run": get("print_run"),
            "is_hit": bool(HIT_WORDS.search(subset)),
        })
    return rows, skipped


# ======================================================================
# Sold comps CSV import
# ======================================================================

SOLD_ALIASES = {
    "player": {"player", "player name", "name", "athlete"},
    "card_number": {"card #", "card number", "card no", "no", "no.", "number", "#", "card"},
    "title": {"title", "listing title", "item title", "description", "card"},
    "price": {"price", "sold price", "sale price", "final price", "amount", "sold for"},
    "date": {"date", "sold date", "sale date", "end date", "date sold"},
    "url": {"url", "link", "listing url", "item url"},
    "source": {"source", "site", "marketplace"},
}
_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%y", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y"]


def _parse_price(raw):
    if raw is None:
        return None
    cleaned = re.sub(r"[^0-9.]", "", str(raw))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_sold_comps_csv(text, matcher, default_source=""):
    reader = _sniff_reader(text)
    mapping = _header_map(reader.fieldnames, SOLD_ALIASES)
    if "price" not in mapping:
        raise ValueError(
            "Couldn't find a price column (e.g. 'Price' or 'Sold Price'). "
            f"Columns found: {', '.join(reader.fieldnames or []) or 'none'}"
        )
    if "player" not in mapping and "title" not in mapping:
        raise ValueError(
            "Couldn't find a Player column or a Title column — need one or the other "
            "to match each sale to a card. "
            f"Columns found: {', '.join(reader.fieldnames or []) or 'none'}"
        )

    rows, skipped = [], 0
    for rec in reader:
        get = lambda f: (rec.get(mapping[f]) or "").strip() if f in mapping else ""
        price = _parse_price(get("price"))
        if price is None:
            skipped += 1
            continue

        player, number, title = get("player"), get("card_number"), get("title")
        if player:
            card_id, match_level = matcher.match_structured(player, number)
            display_title = title or " ".join(x for x in [number, player] if x)
        else:
            card_id, match_level = matcher.match(title)
            display_title = title

        if not display_title:
            skipped += 1
            continue

        date_raw = get("date")
        rows.append({
            "card_id": card_id,
            "match_level": match_level,
            "source": get("source") or default_source,
            "title": display_title,
            "price": price,
            "currency": "USD",
            "sold_date": _parse_date(date_raw),
            "sold_date_raw": date_raw or None,
            "url": get("url") or None,
        })
    return rows, skipped


class ImportPayload(BaseModel):
    name: str
    year: Optional[int] = None
    sport: Optional[str] = None
    search_query: Optional[str] = None
    csv_text: str


@app.post("/api/sets/import")
def api_import_set(payload: ImportPayload):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Set name is required")
    if db.set_exists(name):
        raise HTTPException(400, f'"{name}" already exists. Remove it first to re-import.')
    try:
        rows, skipped = parse_checklist_csv(payload.csv_text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "No cards found in that file.")

    set_id = db.create_set(name, payload.year, payload.sport, (payload.search_query or name).strip())
    db.add_cards(set_id, rows)
    return {"id": set_id, "cards_imported": len(rows), "rows_skipped": skipped}


class SoldCompsPayload(BaseModel):
    csv_text: str
    source: Optional[str] = None


@app.post("/api/sets/{set_id}/sold-comps/import")
def api_import_sold_comps(set_id: int, payload: SoldCompsPayload):
    saved_set = db.get_set(set_id)
    if not saved_set:
        raise HTTPException(404, "Set not found")

    matcher = ChecklistMatcher(db.get_cards(set_id), saved_set["year"])
    try:
        rows, skipped = parse_sold_comps_csv(payload.csv_text, matcher, payload.source or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "No priced rows found in that file.")

    db.add_sold_comps(set_id, rows)
    matched = sum(1 for r in rows if r["card_id"])
    return {"comps_imported": len(rows), "matched": matched, "rows_skipped": skipped}


@app.get("/api/sets/{set_id}/sold-comps")
def api_list_sold_comps(set_id: int):
    if not db.get_set(set_id):
        raise HTTPException(404, "Set not found")
    return db.list_sold_comps(set_id)


# ======================================================================
# Sets
# ======================================================================

@app.get("/api/sets")
def api_list_sets():
    return db.list_sets()


@app.delete("/api/sets/{set_id}")
def api_delete_set(set_id: int):
    if not db.get_set(set_id):
        raise HTTPException(404, "Set not found")
    db.delete_set(set_id)
    return {"ok": True}


@app.post("/api/sets/{set_id}/search")
async def api_search_set(set_id: int):
    saved_set = db.get_set(set_id)
    if not saved_set:
        raise HTTPException(404, "Set not found")

    try:
        items = await ebay_client.search_listings(saved_set["search_query"])
    except ebay_client.EbayConfigError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"eBay search failed: {e}")

    matcher = ChecklistMatcher(db.get_cards(set_id), saved_set["year"])
    for item in items:
        item["card_id"], item["match_level"] = matcher.match(item["title"])

    fetched_at = db.save_listings(set_id, items)
    return {"fetched_at": fetched_at, "count": len(items)}


@app.get("/api/sets/{set_id}/checklist")
def api_checklist(set_id: int):
    if not db.get_set(set_id):
        raise HTTPException(404, "Set not found")
    cards, fetched_at = db.checklist_with_status(set_id)
    return {"fetched_at": fetched_at, "cards": cards}


@app.get("/api/sets/{set_id}/listings")
def api_listings(set_id: int):
    if not db.get_set(set_id):
        raise HTTPException(404, "Set not found")
    listings, fetched_at = db.latest_listings(set_id)
    return {"fetched_at": fetched_at, "listings": listings}


@app.get("/api/sets/{set_id}/summary")
def api_summary(set_id: int):
    if not db.get_set(set_id):
        raise HTTPException(404, "Set not found")
    return db.set_stats(set_id)


@app.get("/api/sets/{set_id}/price-history")
def api_price_history(set_id: int):
    if not db.get_set(set_id):
        raise HTTPException(404, "Set not found")
    return {
        "asking": db.search_history(set_id),   # eBay active-listing snapshots over time
        "sold": db.sold_history(set_id),        # imported sold comps, grouped by date
    }


# ======================================================================
# Static frontend
# ======================================================================
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
