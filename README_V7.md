# PokeSale v7 — Zero-Cost

A no-paid-AI release of PokeSale. It preserves evidence labels: catalog observations are never called local stock, and RSS/web discoveries are unverified leads unless a store-specific source confirms them.

## Deploy
Commit `app.py`, `test_v7.py`, `render.yaml`, and `requirements.txt` to the repository root. Render's build step compiles the app and runs the regression suite before deployment.

## No paid keys required
The core app uses:
- retailer public pages where accessible
- TCGCSV market data (cached to once per 24h)
- public Google News RSS as a low-confidence discovery lead source
- deterministic location/product/restock parsing
- SQLite

## Optional free authenticated sources
Best Buy can be added later with a free developer key. Reddit must only be added after approved OAuth access; do not anonymously scrape Reddit.

## Evidence rules
- `RETAILER CATALOG + PRICE — LOCAL STOCK NOT CONFIRMED`: catalog only.
- `UNVERIFIED LOCAL WEB/RSS SIGNAL`: geographically relevant lead, not confirmed stock.
- Confirmed/store-specific evidence should only be emitted by a connector that actually provides store-specific availability.

## Economics
Default BUY candidate: estimated net profit >= $15 AND ROI >= 25%.
Current conservative model reserves 13.25% for marketplace fees plus $5 fulfillment/shipping overhead. This is an estimate, not guaranteed realized profit.

## Endpoints
- `/health`
- `/diagnostics`
- `/stores`
- `/scan`
- `/api/opportunities`
- `/api/signals`
- `/api/stores`

## Important hosting limitation
On a free Render web service, local filesystem storage can be ephemeral. The app recovers by rebuilding market/catalog data, but history/signals may reset after a restart or redeploy unless `DB_PATH` points to persistent storage. This does not create false stock; it only affects retained history/deduplication across restarts.
