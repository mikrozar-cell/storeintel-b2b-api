# StoreIntel B2B & E-Commerce Intelligence API

Browserless FastAPI REST microservice built for **Render** deployment and **RapidAPI Hub** monetization.

## Endpoints

- `GET /health` — Health check (`{"status": "ok"}`)
- `GET /v1/shopify/inspect?domain=tentree.com&limit=50` — Detects active Shopify theme, installed marketing/review apps, emails, social links, pricing stats, and public products (`/products.json`).
- `GET /v1/skool/inspect?slug_or_url=ai-automation-agency` — Calculates estimated Skool community MRR/ARR and extracts owner metadata & creator socials (supports single communities and `/discovery`).
- `GET /v1/domain/contacts?url=resend.com` — Extracts visible contact emails, LinkedIn/X/GitHub/Instagram/YouTube URLs, and frontend tech stack signals.

## Deploy on Render

1. Connect this repository in Render (`New +` -> `Web Service` or `Blueprint` via `render.yaml`).
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Optional security: set `RAPIDAPI_PROXY_SECRET` in Render Environment Variables to match your RapidAPI Studio `X-RapidAPI-Proxy-Secret` header.

## Import into RapidAPI Studio

Import [`openapi_rapidapi.json`](openapi_rapidapi.json) in RapidAPI Studio after replacing the server URL with your live Render URL.

## Related StoreIntel Ecosystem

- **Live Chrome Extensions:**
  - [StoreIntel: Shopify Spy & Product CSV Exporter](https://chromewebstore.google.com/detail/oemdcnkhnpjnpcbalcbfppeblfepnfdi)
  - [Skool & Creator Intel: MRR Calculator & CSV Exporter](https://chromewebstore.google.com/detail/emcboifchcknlchghjbafmiolpkklemb)
- **32 Apify B2B Cloud Scrapers:** https://apify.com/storeintel
- **GitHub Ecosystem Hub & Free CSV Samples:** https://mikrozar-cell.github.io/b2b-ecommerce-saas-scrapers-n8n-clay/samples.html
