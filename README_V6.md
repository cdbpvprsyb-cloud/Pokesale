# PokeSale v6 — AI-assisted local retail radar

## What this patch adds
- AI Web Scout using the OpenAI Responses API + web search.
- Southern Maryland geographic guardrails: generic nationwide chatter is rejected.
- Persistent `signals` table with fingerprints so repeated discoveries are deduplicated.
- Evidence labels: confirmed retailer/store availability vs strong/unverified public-web signals.
- Official Best Buy Products/Stores API integration for store-specific SKU availability.
- Improved retail-to-TCGCSV product matching and configurable BUY thresholds.
- `/api/signals`, expanded `/health`, and v6 diagnostics.
- Existing Target/Walmart/GameStop/Five Below/Dollar General catalog collectors retained as catalog evidence only.

## Commit these files
Replace the repository-root `app.py`, `render.yaml`, and `requirements.txt` with the v6 files.

## Render secrets you must add
`OPENAI_API_KEY` — enables the AI public-web scout. Keep this server-side only.
`BESTBUY_API_KEY` — enables official Best Buy local SKU/store availability.

The Blueprint marks both as `sync: false`, so secrets are not committed to GitHub.

## Optional settings
- `SCOUT_POSTAL=20639`
- `SCAN_MINUTES=15`
- `SCOUT_MINUTES=60`
- `MIN_PROFIT=15`
- `MIN_ROI=25`
- `OPENAI_MODEL=gpt-5.6-luna`

## How v6 behaves
Retail catalog observations never become local-stock confirmations. Best Buy official store availability is labeled confirmed. AI/web discoveries must contain a useful local Maryland connection and retain their source URL/evidence label. Duplicate source+store+product signals update their last-seen time instead of creating a new alert record.

## Endpoints
- `/` dashboard
- `/scan` manually starts retail + AI scout scans
- `/api/signals` structured local signals
- `/api/opportunities` retail/resale opportunities
- `/diagnostics` collector/API status
- `/health` v6 integration status

## Next engineering milestone
After this patch is deployed and producing real signals, add direct Reddit OAuth ingestion and additional retailer-specific permitted integrations. eBay current listings can be added through Browse API; true 90-day sold-history via Marketplace Insights requires eBay approval, so do not fake sold comps from asking prices.
