import os
from dotenv import load_dotenv

load_dotenv()

EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")

# Optional: your eBay Partner Network (EPN) campaign ID. When set, listing links
# become affiliate links so clicks/purchases through them can earn commission.
# Leave blank until you've been approved for EPN.
EBAY_AFFILIATE_CAMPAIGN_ID = os.getenv("EBAY_AFFILIATE_CAMPAIGN_ID", "").strip()

# Default eBay category: Sports Trading Cards.
DEFAULT_CATEGORY_ID = "212"

# How many eBay listings to pull per search (paged in up to 200 per API call).
MAX_ITEMS_PER_SEARCH = int(os.getenv("MAX_ITEMS_PER_SEARCH", "600"))

DB_PATH = os.path.join(os.path.dirname(__file__), "listings.db")
