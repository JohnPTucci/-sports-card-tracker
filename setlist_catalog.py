"""Catalog of sets with known checklists, sourced from SetList (setlistcards.com),
whose checklists and CSV exports are published under CC BY-NC 4.0 (attribution,
non-commercial). See their /terms page. `data/setlist_catalog.json` is a snapshot of
their sitemap, parsed once; re-run the import script periodically to pick up new sets.

Coverage as of the snapshot: Football and Basketball (mostly Panini, 2019 onward),
one Topps football set, one Topps Bowman Chrome baseball set. No hockey, nothing
before 2019. Sets outside this coverage fall back to manual CSV import (app.py's
/api/sets/import), which is unaffected by any of this.
"""
import csv
import io
import json
import os
import re

import httpx

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "data", "setlist_catalog.json")
CSV_URL = "https://setlistcards.com/data/{slug}.csv"
ATTRIBUTION = "Checklist data from SetList (setlistcards.com), CC BY-NC 4.0"

HIT_WORDS = re.compile(
    r"auto|signature|signed|relic|patch|jersey|memorabilia|swatch|material|rpa", re.I
)

_catalog_cache = None


def load_catalog():
    """Nested dict: {sport: {manufacturer: {year: [set entries]}}}."""
    global _catalog_cache
    if _catalog_cache is None:
        with open(CATALOG_PATH) as f:
            _catalog_cache = json.load(f)
    return _catalog_cache


def find_entry(slug):
    for mans in load_catalog().values():
        for years in mans.values():
            for items in years.values():
                for entry in items:
                    if entry["slug"] == slug:
                        return entry
    return None


class SetlistFetchError(Exception):
    pass


async def fetch_checklist_rows(slug):
    """Download and parse a set's checklist CSV from SetList. Returns a list of
    row dicts shaped like app.py's manual-checklist rows (card_number, player,
    team, subset, print_run, is_hit) so the two import paths produce identical
    checklist_cards rows downstream."""
    url = CSV_URL.format(slug=slug)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code == 404:
            raise SetlistFetchError(f"SetList has no CSV for '{slug}' (404).")
        resp.raise_for_status()
        text = resp.text

    reader = csv.DictReader(io.StringIO(text))
    required = {"card_number", "player", "set"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise SetlistFetchError(
            f"Unexpected CSV format from SetList for '{slug}'. "
            f"Columns found: {', '.join(reader.fieldnames or []) or 'none'}"
        )

    rows = []
    for rec in reader:
        player = (rec.get("player") or "").strip()
        if not player:
            continue
        subset = (rec.get("set") or "").strip()
        numbered_to = (rec.get("numbered_to") or "").strip()
        rows.append({
            "card_number": (rec.get("card_number") or "").strip(),
            "player": player,
            "team": (rec.get("team") or "").strip(),
            "subset": subset,
            "print_run": f"/{numbered_to}" if numbered_to else "",
            "is_hit": bool(HIT_WORDS.search(subset)) or bool(HIT_WORDS.search(rec.get("rookie_subset") or "")),
        })
    return rows
