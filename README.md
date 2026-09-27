# Pokesale

Phone-first local resale radar for the Huntingtown / Prince Frederick and Beltsville areas.

## Deploy on Render
1. Put `app.py`, `requirements.txt`, and `render.yaml` in the root of this GitHub repository.
2. In Render, create a Blueprint and connect this repository.
3. Render reads `render.yaml` and deploys the web service.
4. Open the Render URL in Safari on iPhone.
5. Share > Add to Home Screen.

## Optional SMS
In Render, add these environment variables:
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_FROM
- SMS_TO

Do not put Twilio secrets directly in GitHub.

## Important
The dashboard, database, opportunity math, manual item entry, PWA shell, scheduled page scanning, and SMS trigger framework are implemented.
Retailer-specific exact local inventory adapters are still needed for reliable shelf-level restock alerts; generic page keyword matches are not equivalent to confirmed local inventory.
