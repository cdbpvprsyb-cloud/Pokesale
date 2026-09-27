# Pokesale v5

Commit `app.py` and `render.yaml` to the repository root. No third-party Python packages are required.

## What changed
- ETB-first product matching (`Elite Trainer Box` and `ETB` normalize to the same product type).
- Retail collectors for Target, Walmart, GameStop, Five Below and Dollar General.
- Structured JSON/JSON-LD extraction before conservative visible-HTML fallback.
- Retail price sightings remain explicitly **catalog/online** unless store-specific evidence exists.
- TCGCSV/TCGplayer market matching, projected resale, profit and ROI.
- Per-source diagnostics so blocked/client-rendered retailers are visible instead of silently producing fake inventory.
- Existing HOME / WORK / ANNAPOLIS hunt zones retained.

## Important
Retailer websites can block cloud datacenter requests or change page structures. v5 reports those failures in `/diagnostics`; it does not fabricate local inventory. Catalog price is not local stock.
