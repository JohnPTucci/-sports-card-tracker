import sqlite3
import statistics
from datetime import datetime, timezone
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    year INTEGER,
    sport TEXT,
    search_query TEXT NOT NULL,
    created_at TEXT NOT NULL,
    catalog_slug TEXT,                        -- SetList slug, if auto-loaded from the catalog
    checklist_source TEXT DEFAULT 'manual'    -- 'setlist' or 'manual'
);

CREATE TABLE IF NOT EXISTS checklist_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL,
    card_number TEXT,
    player TEXT NOT NULL,
    team TEXT,
    subset TEXT,
    print_run TEXT,
    is_hit INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (set_id) REFERENCES card_sets (id)
);
CREATE INDEX IF NOT EXISTS idx_cards_set ON checklist_cards (set_id);

-- eBay active listings, one snapshot per search.
CREATE TABLE IF NOT EXISTS set_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL,
    card_id INTEGER,            -- matched checklist card, NULL if not confidently matched
    match_level TEXT,           -- 'exact', 'player', or NULL
    ebay_item_id TEXT,
    title TEXT,
    price REAL,
    currency TEXT,
    condition TEXT,
    url TEXT,
    image_url TEXT,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (set_id) REFERENCES card_sets (id)
);
CREATE INDEX IF NOT EXISTS idx_set_listings ON set_listings (set_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_set_listings_card ON set_listings (card_id, fetched_at);

-- Sold comps imported by the user (from 130point, Terapeak exports, etc).
-- Not tied to a single "fetch" like eBay listings are: every import adds rows,
-- so the table accumulates sale history over time.
CREATE TABLE IF NOT EXISTS sold_comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL,
    card_id INTEGER,
    match_level TEXT,
    source TEXT,
    title TEXT,
    price REAL,
    currency TEXT,
    sold_date TEXT,        -- ISO date (YYYY-MM-DD) if parseable, else NULL
    sold_date_raw TEXT,    -- whatever the CSV said, kept for display
    url TEXT,
    imported_at TEXT NOT NULL,
    FOREIGN KEY (set_id) REFERENCES card_sets (id)
);
CREATE INDEX IF NOT EXISTS idx_sold_comps_set ON sold_comps (set_id);
CREATE INDEX IF NOT EXISTS idx_sold_comps_card ON sold_comps (card_id);
CREATE INDEX IF NOT EXISTS idx_sold_comps_date ON sold_comps (set_id, sold_date);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migrate existing databases created before catalog_slug/checklist_source existed.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(card_sets)")}
        if "catalog_slug" not in existing:
            conn.execute("ALTER TABLE card_sets ADD COLUMN catalog_slug TEXT")
        if "checklist_source" not in existing:
            conn.execute("ALTER TABLE card_sets ADD COLUMN checklist_source TEXT DEFAULT 'manual'")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- sets ----------

def create_set(name, year, sport, search_query, catalog_slug=None, checklist_source="manual"):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO card_sets
               (name, year, sport, search_query, created_at, catalog_slug, checklist_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, year, sport, search_query, now_iso(), catalog_slug, checklist_source),
        )
        return cur.lastrowid


def find_set_by_slug(catalog_slug):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM card_sets WHERE catalog_slug = ?", (catalog_slug,)
        ).fetchone()
        return dict(row) if row else None


def set_exists(name):
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM card_sets WHERE name = ?", (name,)).fetchone() is not None


def list_sets():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*, (SELECT COUNT(*) FROM checklist_cards c WHERE c.set_id = s.id) AS card_count
               FROM card_sets s ORDER BY s.year DESC, s.name ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_set(set_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM card_sets WHERE id = ?", (set_id,)).fetchone()
        return dict(row) if row else None


def delete_set(set_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM set_listings WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM sold_comps WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM checklist_cards WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM card_sets WHERE id = ?", (set_id,))


# ---------- checklist ----------

def add_cards(set_id, rows):
    """rows: dicts with card_number, player, team, subset, print_run, is_hit."""
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO checklist_cards (set_id, card_number, player, team, subset, print_run, is_hit)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    set_id,
                    r.get("card_number"),
                    r["player"],
                    r.get("team"),
                    r.get("subset"),
                    r.get("print_run"),
                    1 if r.get("is_hit") else 0,
                )
                for r in rows
            ],
        )


def get_cards(set_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM checklist_cards WHERE set_id = ? ORDER BY id", (set_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- eBay listings ----------

def save_listings(set_id, items):
    """items: normalized eBay dicts plus card_id / match_level from the matcher."""
    fetched_at = now_iso()
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO set_listings
               (set_id, card_id, match_level, ebay_item_id, title, price, currency,
                condition, url, image_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    set_id,
                    it.get("card_id"),
                    it.get("match_level"),
                    it.get("item_id"),
                    it.get("title"),
                    it.get("price"),
                    it.get("currency"),
                    it.get("condition"),
                    it.get("url"),
                    it.get("image_url"),
                    fetched_at,
                )
                for it in items
            ],
        )
    return fetched_at


def latest_fetch(set_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT MAX(fetched_at) AS f FROM set_listings WHERE set_id = ?", (set_id,)
        ).fetchone()["f"]


def latest_listings(set_id):
    fetched_at = latest_fetch(set_id)
    if not fetched_at:
        return [], None
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM set_listings WHERE set_id = ? AND fetched_at = ? ORDER BY price ASC",
            (set_id, fetched_at),
        ).fetchall()
        return [dict(r) for r in rows], fetched_at


def search_history(set_id):
    """One row per search: asking-price trend over time."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT fetched_at, COUNT(*) AS count, AVG(price) AS avg_price,
                      COUNT(DISTINCT card_id) AS cards_listed
               FROM set_listings WHERE set_id = ?
               GROUP BY fetched_at ORDER BY fetched_at ASC""",
            (set_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- sold comps ----------

def add_sold_comps(set_id, rows):
    """rows: dicts with card_id, match_level, source, title, price, currency,
    sold_date (YYYY-MM-DD or None), sold_date_raw, url."""
    imported_at = now_iso()
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO sold_comps
               (set_id, card_id, match_level, source, title, price, currency,
                sold_date, sold_date_raw, url, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    set_id,
                    r.get("card_id"),
                    r.get("match_level"),
                    r.get("source"),
                    r.get("title"),
                    r.get("price"),
                    r.get("currency") or "USD",
                    r.get("sold_date"),
                    r.get("sold_date_raw"),
                    r.get("url"),
                    imported_at,
                )
                for r in rows
            ],
        )
    return imported_at


def list_sold_comps(set_id, limit=500):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM sold_comps WHERE set_id = ?
               ORDER BY COALESCE(sold_date, '') DESC, imported_at DESC LIMIT ?""",
            (set_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def sold_stats(set_id):
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM sold_comps WHERE set_id = ?", (set_id,)
        ).fetchone()["n"]
        prices = [
            r["price"]
            for r in conn.execute(
                "SELECT price FROM sold_comps WHERE set_id = ? AND card_id IS NOT NULL AND price IS NOT NULL",
                (set_id,),
            ).fetchall()
        ]
        matched_cards = conn.execute(
            "SELECT COUNT(DISTINCT card_id) AS n FROM sold_comps WHERE set_id = ? AND card_id IS NOT NULL",
            (set_id,),
        ).fetchone()["n"]
    stats = {
        "comp_count": total,
        "matched_comp_count": len(prices),
        "cards_with_comps": matched_cards,
        "avg_price": None,
        "median_price": None,
        "min_price": None,
        "max_price": None,
    }
    if prices:
        stats["avg_price"] = round(sum(prices) / len(prices), 2)
        stats["median_price"] = round(statistics.median(prices), 2)
        stats["min_price"] = round(min(prices), 2)
        stats["max_price"] = round(max(prices), 2)
    return stats


def sold_history(set_id):
    """Sold comps grouped by sold_date (dated rows only) for the price-history chart."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT sold_date, COUNT(*) AS count, AVG(price) AS avg_price
               FROM sold_comps WHERE set_id = ? AND sold_date IS NOT NULL
               GROUP BY sold_date ORDER BY sold_date ASC""",
            (set_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- combined checklist + summary ----------

def checklist_with_status(set_id):
    """Every checklist card plus its latest eBay match and its sold-comp stats."""
    fetched_at = latest_fetch(set_id) or ""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*,
                      COUNT(DISTINCT l.id) AS listing_count,
                      MIN(l.price) AS min_price,
                      (SELECT url FROM set_listings
                        WHERE card_id = c.id AND fetched_at = ?
                        ORDER BY price ASC LIMIT 1) AS best_url,
                      (SELECT COUNT(*) FROM sold_comps WHERE card_id = c.id) AS sold_count,
                      (SELECT AVG(price) FROM sold_comps WHERE card_id = c.id) AS sold_avg_price
               FROM checklist_cards c
               LEFT JOIN set_listings l ON l.card_id = c.id AND l.fetched_at = ?
               WHERE c.set_id = ?
               GROUP BY c.id
               ORDER BY c.id""",
            (fetched_at, fetched_at, set_id),
        ).fetchall()
        cards = [dict(r) for r in rows]
        for c in cards:
            if c["sold_avg_price"] is not None:
                c["sold_avg_price"] = round(c["sold_avg_price"], 2)
        return cards, (fetched_at or None)


def set_stats(set_id):
    fetched_at = latest_fetch(set_id)
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(is_hit), 0) AS hits FROM checklist_cards WHERE set_id = ?",
            (set_id,),
        ).fetchone()
        stats = {
            "fetched_at": fetched_at,
            "cards_total": total["n"],
            "hits_total": total["hits"],
            "cards_listed": 0,
            "hits_listed": 0,
            "listing_count": 0,
            "matched_listing_count": 0,
            "avg_price": None,
            "median_price": None,
            "min_price": None,
            "max_price": None,
        }
        if fetched_at:
            stats["listing_count"] = conn.execute(
                "SELECT COUNT(*) AS n FROM set_listings WHERE set_id = ? AND fetched_at = ?",
                (set_id, fetched_at),
            ).fetchone()["n"]

            listed = conn.execute(
                """SELECT COUNT(DISTINCT l.card_id) AS cards,
                          COUNT(DISTINCT CASE WHEN c.is_hit = 1 THEN l.card_id END) AS hits
                   FROM set_listings l JOIN checklist_cards c ON c.id = l.card_id
                   WHERE l.set_id = ? AND l.fetched_at = ? AND l.card_id IS NOT NULL""",
                (set_id, fetched_at),
            ).fetchone()
            stats["cards_listed"] = listed["cards"]
            stats["hits_listed"] = listed["hits"]

            prices = [
                r["price"]
                for r in conn.execute(
                    """SELECT price FROM set_listings
                       WHERE set_id = ? AND fetched_at = ? AND card_id IS NOT NULL AND price IS NOT NULL""",
                    (set_id, fetched_at),
                ).fetchall()
            ]
            stats["matched_listing_count"] = len(prices)
            if prices:
                stats["avg_price"] = round(sum(prices) / len(prices), 2)
                stats["median_price"] = round(statistics.median(prices), 2)
                stats["min_price"] = round(min(prices), 2)
                stats["max_price"] = round(max(prices), 2)

    stats["sold"] = sold_stats(set_id)
    return stats
