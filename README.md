# Pokesale v4 — consolidated refined replacement

Upload **all three deployment files** (`app.py`, `render.yaml`, `requirements.txt`) to the repository root, replacing the old versions, then commit/push. Render should redeploy automatically. The Blueprint start command is `python -u app.py`; the build step compiles `app.py` first so syntax errors fail the build instead of reaching production. Health check: `/health`.

## Included
- Mobile-first deal dashboard with product images when supplied by the market feed.
- Retailer/location, projected resale, conservative net profit/ROI, and confidence status.
- Best Deals, Closest Deals, New Restocks, Pokemon, Sports Cards, and Other Flips sections.
- Home, Work, and Annapolis hunt zones.
- Inventory-history table for state changes and future restock-pattern learning.
- `/api/opportunities`, `/api/stores`, `/stores`, `/diagnostics`, `/scan`, and `/health`.
- Database migration logic so an older Pokesale SQLite schema can start without manual migration.
- Catalog observations remain explicitly unconfirmed until store-specific evidence exists.

## Important inventory rule
Pokesale intentionally does **not** convert retailer catalog visibility into a claim that a local shelf has stock. The confidence field reflects evidence quality. Store-specific adapters can later raise confidence and write stock transitions to `inventory_history`.
