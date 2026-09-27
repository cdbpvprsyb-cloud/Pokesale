# Pokesale v5.1 hotfix

This hotfix addresses the failures visible in production diagnostics.

- Dollar General: replaces the dead 404 category URL with retailer search.
- Five Below: replaces the obsolete category path with the current trading-card path.
- Walmart: uses the dedicated Pokemon Trainer Box browse page.
- Target: uses an ETB-focused search page.
- Retail parser: supports price-before-title and title-before-price page layouts.
- Diagnostics: explicitly distinguishes Render 403 blocking from parser-zero and 404 URL failures.
- Safety: catalog observations remain catalog-only; no local inventory is inferred.

Known limitation: GameStop/Five Below may still return 403 to Render. This patch reports that accurately rather than attempting to bypass retailer access controls. Store-specific inventory requires a legitimate store-specific retailer response/source.
