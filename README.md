# StoreIntel B2B & E-Commerce Intelligence API (v2.0.0)

Browserless FastAPI REST microservice deployed on **Render** (`https://storeintel-b2b-api.onrender.com`) and published on **RapidAPI Hub** (`https://rapidapi.com/mikrozar-1NoawVerl/api/storeintel-b2b-e-commerce-intelligence`).

## All 8 B2B Endpoints (+ Health Check)

1. `GET /v1/shopify/inspect?domain=tentree.com&limit=50` — Shopify theme, installed marketing/review apps, emails, social links, pricing stats, and `/products.json` catalog.
2. `GET /v1/shopify/app-reviews?app_slug=klaviyo-email-marketing&rating=1&limit=20` — Competitor Shopify App Store 1-star to 5-star reviews & frustrated merchant leads.
3. `GET /v1/skool/inspect?slug_or_url=ai-automation-agency` — Skool community MRR/ARR estimation, owner metadata & creator socials (supports single groups and `discovery`).
4. `GET /v1/domain/contacts?url=resend.com` — Visible contact emails, LinkedIn/X/GitHub/Instagram/YouTube links, and frontend tech stack signals.
5. `GET /v1/ats/jobs?board_url=https://boards.greenhouse.io/stripe&limit=20` — Open roles from Greenhouse, Lever, or AshbyHQ with salary ranges and B2B tech stack signals.
6. `GET /v1/yc/company?slug=supabase` — Y Combinator startup profile, batch, headcount, and founders' LinkedIn/X URLs.
7. `GET /v1/wordpress/plugin?slug=woocommerce` — WordPress.org active installs, downloads, rating, and support thread resolution rate.
8. `GET /v1/substack/posts?subdomain=pragmaticengineer&limit=10` — Substack author bio, X handle, average reactions/comments, and recent posts.
9. `GET /health` — Health check (`{"status": "ok"}`).

## Related StoreIntel Ecosystem

- **RapidAPI Hub (Free / $19 Pro / $49 Ultra):** https://rapidapi.com/mikrozar-1NoawVerl/api/storeintel-b2b-e-commerce-intelligence
- **Live Chrome Extensions:**
  - [StoreIntel: Shopify Spy & Product CSV Exporter](https://chromewebstore.google.com/detail/oemdcnkhnpjnpcbalcbfppeblfepnfdi)
  - [Skool & Creator Intel: MRR Calculator & CSV Exporter](https://chromewebstore.google.com/detail/emcboifchcknlchghjbafmiolpkklemb)
- **32 Apify B2B Cloud Scrapers:** https://apify.com/storeintel
- **GitHub Ecosystem Hub & Free CSV Samples:** https://mikrozar-cell.github.io/b2b-ecommerce-saas-scrapers-n8n-clay/samples.html
