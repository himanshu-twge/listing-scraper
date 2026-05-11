# Listing Scraper (TWGE Solutions) - Development Plan

## 1. Objectives
- Prove the Amazon India scrape core works with real pincode impact using Decodo 3-step cookie + pincode injection flow.
- Build an MVP full-stack app (FastAPI + SQLite + static HTML/CSS/JS) to manage brands, ASINs, pincodes, run scrapes, track progress, and export CSV.
- Ensure scraping is resilient (retries, captcha detection, fallback mode) and results are stored/exported consistently.

## 2. Implementation Steps

### Phase 1: Core Scraping POC (Isolation) (must pass before app work)
User stories:
1. As a developer, I want a single command script that scrapes one ASIN for one pincode so I can validate Decodo connectivity.
2. As a developer, I want to see which cookies were extracted so I can confirm session continuity.
3. As a developer, I want to verify pincode injection succeeded so I can trust location-sensitive fields.
4. As a developer, I want the script to detect captcha/robot-check and retry once so I can measure stability.
5. As a developer, I want the parsed fields printed as JSON so I can validate selector accuracy quickly.

Steps:
1. Websearch quick reference: Decodo Scraper API request format for universal target, cookies/headers behavior, and POST body handling.
2. Create `backend/poc_decodo_amazon.py`:
   - Inputs: ASIN, pincode, optional run-without-injection comparison.
   - Step 1: request product page, capture `Set-Cookie` values.
   - Step 2: POST address-change with cookie header.
   - Step 3: request product page again with combined cookies.
   - Retry logic: if HTML < 500 chars or contains Robot Check/captcha -> wait 3s and retry once.
   - Delay: 2000ms between scrapes.
   - Parse with BeautifulSoup using required selectors; normalize price as `Rs. N`.
   - Output: JSON including `pincode_verified` and evidence fields (cookie keys present, injection status code).
3. Run POC on 1 ASIN and pincode 400064; then run without step 2 and compare key fields (delivery/stock/price) to validate pincode effect.
4. Iterate until: stable 200 responses, valid HTML, selectors consistently extract data.

### Phase 2: V1 App Development (FastAPI + SQLite + static frontend)
User stories:
1. As a user, I want to add/delete brands so I can manage multiple vendor portfolios.
2. As a user, I want to add/remove ASINs and pincodes per brand so I can control what gets monitored.
3. As a user, I want to scrape a brand for selected pincodes so I can monitor location-specific listings.
4. As a user, I want to see scraping progress and logs live so I know the job is running.
5. As a user, I want to export results to CSV so I can share and analyze in Excel.

Backend steps (FastAPI on :8001):
1. Replace current backend with FastAPI-only implementation (keep supervisor constraints):
   - `server.py` defines app, mounts `/health` and `/api/*`.
2. SQLite setup at `/app/backend/data.db`:
   - Create tables: brands, pincodes, asins, scrape_results.
   - Seed on first run: Brand `Chheda`, ASINs provided, pincode 400064 Mumbai.
3. Implement Decodo client module reused from POC:
   - Shared scraping function `scrape_asin_pincode(asin, pincode)` returning parsed fields + `pincode_verified`.
   - Enforce 2000ms delay between ASIN scrapes; fallback to step 3 only if step 1/2 fails.
4. Implement background scraping jobs:
   - In-memory job registry per brand: `isScraping`, `progress`, `total`, `current`, `logs`.
   - Use `asyncio.create_task` / thread executor to avoid blocking.
   - Endpoints: start scrape, poll status every 3s, store results as they complete.
5. Implement all required API endpoints:
   - CRUD for brands/pincodes/asins.
   - Upload replace list (`/upload`) and download ASIN CSV (`/download`).
   - Scrape brand with selected pincodes, scrape all brands.
   - Export results CSV with UTF-8 BOM and required headers.

Frontend steps (static site on :3000 via yarn start):
1. Replace React frontend with static `public/` app served by `http-server` (or `serve`) wired through `yarn start`.
2. Build 3 screens with plain HTML/CSS/JS:
   - Home (brands grid, scrape-all, add brand).
   - Brand detail (stats, scrape, export, pincode manager, ASIN manager, results table).
   - Pincode selection modal (checkboxes + select all).
3. Follow frontend rules:
   - Plain ASCII only.
   - DOM via `createElement` + `textContent`.
   - `addEventListener` only.
   - Use `XMLHttpRequest` for API calls.
   - SheetJS via CDN for Excel upload parsing; server stays CSV only.
4. Progress UX:
   - Poll `/api/brands/:name/status` every 3s while scraping.
   - Update progress bar, current label, and append logs to a scrollable log box.

Phase 2 conclude:
- Run one end-to-end manual flow: seed brand visible -> scrape -> results table populates -> export CSV downloads.
- Run testing agent for one full E2E pass; fix blocking issues.

### Phase 3: Hardening + E2E Testing
User stories:
1. As a user, I want consistent error messages when scraping fails so I can act quickly.
2. As a user, I want retries to reduce transient failures so results are reliable.
3. As a user, I want pagination or basic filtering so large result sets remain usable.
4. As a user, I want pincode-verified to be visible so I trust the data.
5. As a user, I want CSV exports to always open cleanly in Excel.

Steps:
1. Add robust validation (ASIN format, pincode format), and clear API error payloads.
2. Improve scraping resilience: centralize captcha detection, retry once after 3s, log reason.
3. Ensure CSV export formatting: UTF-8 BOM, stable header order, safe newlines.
4. Add basic client-side filtering (by pincode / stock / seller) if needed.
5. Run testing agent again; fix UI regressions and backend edge cases.

## 3. Next Actions
- Implement and run Phase 1 POC script against ASIN `B08WJ12R6N` with pincode `400064` using `DECODO_KEY` from env.
- Confirm pincode injection changes at least one field (delivery/stock/price) vs non-injected run.
- Once POC passes, begin Phase 2: backend schema + seed + API scaffolding, then static frontend shell, then scraping jobs + progress + exports.

## 4. Success Criteria
- POC:
  - Step 1 returns cookies; step 2 returns success; step 3 returns valid HTML.
  - Parsers extract: title, price, seller, rating, reviews, stock, delivery.
  - Difference observed between injected vs non-injected scrape (or `pincode_verified=false` when injection fails).
- V1 App:
  - Seed brand loads on Home.
  - Brand detail supports managing ASINs/pincodes, scraping selected pincodes, and shows progress.
  - Results persist in SQLite and export to CSV (UTF-8 BOM) with required columns.
- Stability:
  - Captcha/robot-check detection triggers single retry and logs outcome.
  - No blocking UI during scraping; status polling works reliably.
