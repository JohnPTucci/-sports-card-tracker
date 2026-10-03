# Sports Card Tracker

A personal tool that shows, for a given set, how many of its cards are currently listed on eBay
(and how many of the "hits"), plus both eBay asking-price stats and real sold prices you import —
with a price-history chart combining both. Runs locally; data lives in a SQLite file.

## How it works

1. **Import a checklist** (CSV) for a set — the list of every card in the set. Pick it from the
   dropdown at top.
2. **Search eBay** to see which checklist cards are currently listed, with asking prices.
3. **Import sold comps** (CSV) whenever you have them — from 130point, eBay/Terapeak sold-listing
   exports, or anything you compile by hand. These are real sale prices, more reliable than asking
   prices, and accumulate over time (every import adds to history, nothing is overwritten).
4. The **Checklist** tab shows both an "Asking (eBay)" and a "Sold avg." column per card, with
   filters. **Other listings** holds anything that couldn't be matched to one card. **Price history**
   charts both series over time.

## Checklist CSV format

Required: a card number column (`Card #`, `Number`, `No`...) and a player column (`Player`, `Name`...).
Optional: `Team`, `Subset` (or `Insert`/`Card Set`/`Type`), `Print Run` (or `Numbered`).
Rows whose subset mentions auto/signature/relic/patch/jersey/memorabilia are flagged as **hits**.
See `static/sample_checklist.csv`.

There's no single official checklist database. Panini publishes release checklists as downloadable
spreadsheets; other sites compile checklists too. Check a source's terms before bulk-copying from it.

## Sold comps CSV format

Required: a `Price` column, plus **either**:
- `Player` (recommended — add `Card #` too for an exact match), **or**
- `Title` (a free-text listing title; matched the same heuristic way eBay search results are)

Optional: `Date` (most common formats work: `2026-01-05`, `01/05/2026`, `Jan 5, 2026`...), `URL`, `Source`.
See `static/sample_sold_comps.csv`. Rows that don't match any checklist card are still imported
(for overall averages and the price-history chart) — they just won't show against a specific card.

## Matching limits (be aware)

- Matching is heuristic; sellers/sources write text however they like. "Exact" needs a card number
  match, a player with only one card in the set, or a clearly best subset match.
- Parallels (Silver, Gold...) aren't separate checklist rows unless your CSV lists them, so a listed
  parallel counts toward its base card number.
- eBay's Browse API only exposes *active* listings — "Asking (eBay)" is what's currently for sale,
  not what things actually sold for. That's exactly why the sold-comps import exists.

## Price history

Each eBay search saves a timestamped snapshot (asking-price average that day). Each sold-comps
import adds dated sale rows. The Price History tab charts both as line series so you can see how
a set's market is trending, not just a single point-in-time number.

## eBay affiliate links (eBay Partner Network)

1. Apply for the eBay Partner Network (partnernetwork.ebay.com) and create a campaign; note its Campaign ID.
2. Put it in `.env`: `EBAY_AFFILIATE_CAMPAIGN_ID=your-campaign-id`
3. Restart the app. Every listing link now uses eBay's affiliate URL, so purchases through it can earn commission.

If the ID is blank, normal eBay links are used. If you ever make the site public, disclose that links are affiliate links.

## Setup / run

1. `python -m venv venv` then activate it (`venv\Scripts\Activate.ps1` on Windows PowerShell,
   `source venv/bin/activate` on Mac/Linux).
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your eBay production Client ID / Secret.
4. `uvicorn app:app --reload`, then open http://127.0.0.1:8000

Your `.env`, `venv/` folder, and `listings.db` are never touched by future updates — only the
`.py` and `static/` files get replaced.

## Deploying online (free, public link)

This repo includes a `render.yaml` so Render.com can deploy it with one click. Chosen setup:
**free tier, no password** — meaning:

- The app spins down after periods of inactivity and takes ~30-60 seconds to wake back up on
  the next visit (normal free-tier behavior, not a bug).
- The filesystem is **ephemeral**: `listings.db` can be wiped on restarts/redeploys, so checklists,
  eBay snapshots and imported sold comps saved there aren't guaranteed to persist. Upgrading the
  Render service to a paid instance with a persistent disk (~$7/mo) fixes this.
- The URL is **public and unprotected** — anyone who has it can open the app, run eBay searches
  (against your API quota), and see what you've imported. Don't share the link anywhere public.

To deploy: push this repo to GitHub, then on Render choose **New > Blueprint**, point it at the
repo (it reads `render.yaml` automatically), and add your `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`
(and optionally `EBAY_AFFILIATE_CAMPAIGN_ID`) as environment variables when prompted.
