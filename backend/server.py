"""Listing Scraper - FastAPI backend with SQLite + Decodo scraping."""
import asyncio
import csv
import io
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, validator

load_dotenv("/app/backend/.env")

from db import init_db, seed_if_empty, cursor  # noqa: E402
from scraper import scrape_asin_pincode  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("scraper-app")

# Concurrency for scrape job (configurable via env)
SCRAPE_CONCURRENCY = int(os.environ.get("SCRAPE_CONCURRENCY", "8"))
# Per-worker delay between successive requests (ms). 0 = no extra delay
# (Decodo rate-limits internally based on plan).
SCRAPE_PER_REQUEST_DELAY_MS = int(os.environ.get("SCRAPE_PER_REQUEST_DELAY_MS", "0"))

app = FastAPI(title="Listing Scraper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job registry per brand name
_jobs_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


# ========== Pydantic Models ==========
class BrandIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class PincodeIn(BaseModel):
    code: str
    city: str = Field(..., min_length=1, max_length=80)

    @validator("code")
    def must_be_6_digits(cls, v):
        v = (v or "").strip()
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("Pincode must be exactly 6 digits")
        return v


class AsinIn(BaseModel):
    asin: str
    notes: str = ""

    @validator("asin")
    def must_be_10_chars(cls, v):
        v = (v or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", v):
            raise ValueError("ASIN must be 10 alphanumeric characters")
        return v


class UploadAsinsIn(BaseModel):
    asins: List[AsinIn]


class ScrapeIn(BaseModel):
    pincodes: List[PincodeIn]


# ========== DB helpers ==========
def now_iso() -> str:
    return datetime.utcnow().isoformat()


def get_brand_row(name: str) -> Optional[Dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM brands WHERE name = ?", (name,))
        row = cur.fetchone()
        return dict(row) if row else None


def require_brand(name: str) -> Dict:
    b = get_brand_row(name)
    if not b:
        raise HTTPException(status_code=404, detail="Brand not found")
    return b


# ========== Startup ==========
@app.on_event("startup")
def on_startup():
    init_db()
    seed_if_empty()
    log.info("DB initialized + seeded if needed")


# ========== Health ==========
@app.get("/health")
def health():
    return {"status": "ok", "service": "listing-scraper", "time": now_iso()}


@app.get("/api/health")
def api_health():
    return health()


# ========== Brands ==========
@app.get("/api/brands")
def list_brands():
    out = []
    with cursor() as cur:
        cur.execute("SELECT * FROM brands ORDER BY name ASC")
        brands = [dict(r) for r in cur.fetchall()]
        for b in brands:
            cur.execute("SELECT COUNT(*) AS c FROM asins WHERE brand_id = ?", (b["id"],))
            asin_count = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM pincodes WHERE brand_id = ?", (b["id"],))
            pc_count = cur.fetchone()["c"]
            cur.execute("SELECT MAX(scraped_at) AS m FROM scrape_results WHERE brand_id = ?", (b["id"],))
            last = cur.fetchone()["m"]
            cur.execute(
                "SELECT COUNT(DISTINCT asin_id) AS c FROM scrape_results WHERE brand_id = ? AND stock = 'In Stock' AND id IN (SELECT MAX(id) FROM scrape_results WHERE brand_id = ? GROUP BY asin_id, pincode_code)",
                (b["id"], b["id"]),
            )
            in_stock = cur.fetchone()["c"]
            out.append({
                "name": b["name"],
                "asin_count": asin_count,
                "pincode_count": pc_count,
                "last_scraped": last,
                "in_stock_count": in_stock,
                "is_scraping": _job_status(b["name"]).get("isScraping", False),
            })
    return out


@app.post("/api/brands")
def create_brand(body: BrandIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    with cursor() as cur:
        cur.execute("SELECT id FROM brands WHERE name = ?", (name,))
        if cur.fetchone():
            raise HTTPException(400, "Brand already exists")
        cur.execute("INSERT INTO brands(name, created_at) VALUES (?, ?)", (name, now_iso()))
        bid = cur.lastrowid
        # Add default pincode 400064 Mumbai for new brand
        cur.execute(
            "INSERT INTO pincodes(brand_id, code, city, created_at) VALUES (?, ?, ?, ?)",
            (bid, "400064", "Mumbai", now_iso()),
        )
    return {"name": name, "created": True}


@app.delete("/api/brands/{name}")
def delete_brand(name: str):
    require_brand(name)
    with cursor() as cur:
        cur.execute("DELETE FROM brands WHERE name = ?", (name,))
    with _jobs_lock:
        _jobs.pop(name, None)
    return {"deleted": True}


@app.get("/api/brands/{name}")
def brand_detail(name: str):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("SELECT id, asin, notes, created_at FROM asins WHERE brand_id = ? ORDER BY id", (b["id"],))
        asins = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, code, city, created_at FROM pincodes WHERE brand_id = ? ORDER BY id", (b["id"],))
        pincodes = [dict(r) for r in cur.fetchall()]
        # Latest result per (asin, pincode)
        cur.execute(
            """
            SELECT sr.* , a.asin AS asin, a.notes AS notes
            FROM scrape_results sr
            JOIN asins a ON a.id = sr.asin_id
            WHERE sr.brand_id = ?
              AND sr.id IN (SELECT MAX(id) FROM scrape_results WHERE brand_id = ? GROUP BY asin_id, pincode_code)
            ORDER BY a.asin ASC, sr.pincode_code ASC
            """,
            (b["id"], b["id"]),
        )
        results = [dict(r) for r in cur.fetchall()]
    return {
        "name": b["name"],
        "created_at": b["created_at"],
        "asins": asins,
        "pincodes": pincodes,
        "results": results,
        "job": _job_status(b["name"]),
    }


# ========== Pincodes ==========
@app.post("/api/brands/{name}/pincodes")
def add_pincode(name: str, body: PincodeIn):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("SELECT id FROM pincodes WHERE brand_id = ? AND code = ?", (b["id"], body.code))
        if cur.fetchone():
            raise HTTPException(400, "Pincode already exists for this brand")
        cur.execute(
            "INSERT INTO pincodes(brand_id, code, city, created_at) VALUES (?, ?, ?, ?)",
            (b["id"], body.code, body.city, now_iso()),
        )
    return {"added": True}


@app.delete("/api/brands/{name}/pincodes/{code}")
def delete_pincode(name: str, code: str):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("DELETE FROM pincodes WHERE brand_id = ? AND code = ?", (b["id"], code))
    return {"deleted": True}


# ========== ASINs ==========
@app.post("/api/brands/{name}/asins")
def add_asin(name: str, body: AsinIn):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("SELECT id FROM asins WHERE brand_id = ? AND asin = ?", (b["id"], body.asin))
        if cur.fetchone():
            raise HTTPException(400, "ASIN already exists for this brand")
        cur.execute(
            "INSERT INTO asins(brand_id, asin, notes, created_at) VALUES (?, ?, ?, ?)",
            (b["id"], body.asin, body.notes or "", now_iso()),
        )
    return {"added": True}


@app.delete("/api/brands/{name}/asins/{asin}")
def delete_asin(name: str, asin: str):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("DELETE FROM asins WHERE brand_id = ? AND asin = ?", (b["id"], asin.upper()))
    return {"deleted": True}


@app.post("/api/brands/{name}/upload")
def upload_asins(name: str, body: UploadAsinsIn):
    b = require_brand(name)
    added = 0
    skipped = 0
    with cursor() as cur:
        # Replace entire list
        cur.execute("DELETE FROM asins WHERE brand_id = ?", (b["id"],))
        seen = set()
        for a in body.asins:
            if a.asin in seen:
                skipped += 1
                continue
            seen.add(a.asin)
            try:
                cur.execute(
                    "INSERT INTO asins(brand_id, asin, notes, created_at) VALUES (?, ?, ?, ?)",
                    (b["id"], a.asin, a.notes or "", now_iso()),
                )
                added += 1
            except Exception:
                skipped += 1
    return {"added": added, "skipped": skipped, "total_in_request": len(body.asins)}


@app.get("/api/brands/{name}/download")
def download_asins(name: str):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("SELECT asin, notes FROM asins WHERE brand_id = ? ORDER BY id", (b["id"],))
        rows = [dict(r) for r in cur.fetchall()]
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["ASIN", "Notes"])
    for r in rows:
        w.writerow([r["asin"], r["notes"] or ""])
    csv_bytes = buf.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{name}_asins.csv\""},
    )


# ========== Results CSV Export ==========
@app.get("/api/brands/{name}/csv")
def export_results_csv(name: str):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute(
            """
            SELECT sr.*, a.asin AS asin, a.notes AS notes
            FROM scrape_results sr
            JOIN asins a ON a.id = sr.asin_id
            WHERE sr.brand_id = ?
              AND sr.id IN (SELECT MAX(id) FROM scrape_results WHERE brand_id = ? GROUP BY asin_id, pincode_code)
            ORDER BY a.asin ASC, sr.pincode_code ASC
            """,
            (b["id"], b["id"]),
        )
        rows = [dict(r) for r in cur.fetchall()]
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow([
        "Brand", "ASIN", "Notes", "Pincode", "City",
        "Title", "Price", "Seller", "Rating", "Reviews",
        "Stock", "Delivery", "Pincode Verified", "Scraped At",
    ])
    for r in rows:
        w.writerow([
            name, r.get("asin", ""), r.get("notes", "") or "",
            r.get("pincode_code", ""), r.get("pincode_city", ""),
            r.get("title", "") or "", r.get("price", "") or "",
            r.get("seller", "") or "", r.get("rating", "") or "",
            r.get("reviews", "") or "", r.get("stock", "") or "",
            r.get("delivery", "") or "",
            "Yes" if r.get("pincode_verified") else "No",
            r.get("scraped_at", "") or "",
        ])
    csv_bytes = buf.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{name}_results.csv\""},
    )


# ========== Scraping Jobs ==========
def _job_status(brand_name: str) -> Dict:
    with _jobs_lock:
        j = _jobs.get(brand_name)
        if not j:
            return {"isScraping": False, "progress": {"current": 0, "total": 0, "label": ""}, "logs": []}
        return {
            "isScraping": j["isScraping"],
            "progress": j["progress"],
            "logs": j["logs"][-200:],
        }


def _set_job(brand_name: str, **kwargs):
    with _jobs_lock:
        if brand_name not in _jobs:
            _jobs[brand_name] = {"isScraping": False, "progress": {"current": 0, "total": 0, "label": ""}, "logs": []}
        _jobs[brand_name].update(kwargs)


def _job_log(brand_name: str, msg: str):
    with _jobs_lock:
        if brand_name not in _jobs:
            _jobs[brand_name] = {"isScraping": False, "progress": {"current": 0, "total": 0, "label": ""}, "logs": []}
        _jobs[brand_name]["logs"].append(f"{datetime.utcnow().strftime('%H:%M:%S')} - {msg}")
        if len(_jobs[brand_name]["logs"]) > 500:
            _jobs[brand_name]["logs"] = _jobs[brand_name]["logs"][-500:]


def _run_brand_scrape(brand_name: str, pincodes: List[Dict[str, str]]):
    try:
        b = get_brand_row(brand_name)
        if not b:
            _set_job(brand_name, isScraping=False)
            return
        with cursor() as cur:
            cur.execute("SELECT id, asin, notes FROM asins WHERE brand_id = ? ORDER BY id", (b["id"],))
            asins = [dict(r) for r in cur.fetchall()]
        total = len(asins) * len(pincodes)

        # Create scrape_runs record
        run_id = None
        if total > 0:
            with cursor() as cur:
                cur.execute(
                    """INSERT INTO scrape_runs (brand_id, started_at, pincode_codes, asin_count, pincode_count, total_expected, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'running')""",
                    (b["id"], now_iso(), json.dumps(pincodes), len(asins), len(pincodes), total),
                )
                run_id = cur.lastrowid

        _set_job(brand_name, isScraping=True, progress={"current": 0, "total": total, "label": "Starting..."}, logs=[f"Starting scrape: {len(asins)} ASINs x {len(pincodes)} pincodes = {total} (concurrency={SCRAPE_CONCURRENCY})"])
        if total == 0:
            _job_log(brand_name, "Nothing to scrape (no ASINs or no pincodes)")
            _set_job(brand_name, isScraping=False, progress={"current": 0, "total": 0, "label": "Done"})
            return

        # Build task list
        tasks = []
        for a in asins:
            for pc in pincodes:
                tasks.append((a, pc))

        progress_lock = threading.Lock()
        completed = {"n": 0, "ok": 0, "err": 0}

        def worker(task):
            a, pc = task
            code = pc["code"]
            city = pc["city"]
            label = f"Scraping {a['asin']} for pincode {code} {city}"
            try:
                parsed = scrape_asin_pincode(a["asin"], code)
                with cursor() as cur:
                    cur.execute(
                        """INSERT INTO scrape_results (asin_id, brand_id, run_id, pincode_code, pincode_city, title, price, seller, rating, reviews, stock, delivery, pincode_verified, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            a["id"], b["id"], run_id, code, city,
                            parsed.get("title", ""),
                            parsed.get("price", ""),
                            parsed.get("seller", ""),
                            parsed.get("rating", ""),
                            parsed.get("reviews", ""),
                            parsed.get("stock", ""),
                            parsed.get("delivery", ""),
                            1 if parsed.get("pincode_verified") else 0,
                            now_iso(),
                        ),
                    )
                with progress_lock:
                    completed["n"] += 1
                    completed["ok"] += 1
                    cur_n = completed["n"]
                _job_log(brand_name, f"OK {a['asin']} @ {code} - price={parsed.get('price','-') or '-'} stock={parsed.get('stock','-')} verified={parsed.get('pincode_verified')}")
                _set_job(brand_name, progress={"current": cur_n, "total": total, "label": f"{label} ({cur_n} of {total})"})
                # Per-worker pacing
                if SCRAPE_PER_REQUEST_DELAY_MS > 0:
                    time.sleep(SCRAPE_PER_REQUEST_DELAY_MS / 1000.0)
                return True
            except Exception as e:
                with progress_lock:
                    completed["n"] += 1
                    completed["err"] += 1
                    cur_n = completed["n"]
                _job_log(brand_name, f"ERROR {a['asin']} @ {code}: {e}")
                try:
                    with cursor() as cur:
                        cur.execute(
                            """INSERT INTO scrape_results (asin_id, brand_id, run_id, pincode_code, pincode_city, title, price, seller, rating, reviews, stock, delivery, pincode_verified, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                a["id"], b["id"], run_id, code, city,
                                "", "", "", "", "", "Unknown", "", 0, now_iso(),
                            ),
                        )
                except Exception as e2:
                    _job_log(brand_name, f"DB error inserting failure row: {e2}")
                _set_job(brand_name, progress={"current": cur_n, "total": total, "label": f"{label} ({cur_n} of {total})"})
                return False

        with ThreadPoolExecutor(max_workers=SCRAPE_CONCURRENCY) as ex:
            futures = [ex.submit(worker, t) for t in tasks]
            for f in as_completed(futures):
                # exceptions are caught inside worker
                try:
                    f.result()
                except Exception:
                    pass

        # Finalize run
        if run_id is not None:
            with cursor() as cur:
                cur.execute(
                    "UPDATE scrape_runs SET finished_at = ?, total_results = ?, status = 'completed' WHERE id = ?",
                    (now_iso(), completed["n"], run_id),
                )
        _job_log(brand_name, f"Scrape complete. OK={completed['ok']} ERR={completed['err']}")
        _set_job(brand_name, isScraping=False, progress={"current": total, "total": total, "label": "Done"})
    except Exception as e:
        log.exception("Job failed")
        _job_log(brand_name, f"FATAL: {e}")
        _set_job(brand_name, isScraping=False)


@app.post("/api/brands/{name}/scrape")
def scrape_brand(name: str, body: ScrapeIn):
    b = require_brand(name)
    if _job_status(name).get("isScraping"):
        raise HTTPException(409, "Brand is already being scraped")
    pincodes = [{"code": p.code, "city": p.city} for p in body.pincodes]
    if not pincodes:
        raise HTTPException(400, "At least one pincode is required")
    t = threading.Thread(target=_run_brand_scrape, args=(name, pincodes), daemon=True)
    t.start()
    return {"started": True, "brand": name, "pincode_count": len(pincodes)}


@app.get("/api/brands/{name}/status")
def get_status(name: str):
    require_brand(name)
    return _job_status(name)


@app.post("/api/scrape-all")
def scrape_all():
    started = []
    skipped = []
    with cursor() as cur:
        cur.execute("SELECT id, name FROM brands ORDER BY name")
        brands = [dict(r) for r in cur.fetchall()]
        for b in brands:
            cur.execute("SELECT code, city FROM pincodes WHERE brand_id = ? ORDER BY id", (b["id"],))
            pcs = [dict(r) for r in cur.fetchall()]
            if not pcs:
                skipped.append(b["name"])
                continue
            if _job_status(b["name"]).get("isScraping"):
                skipped.append(b["name"])
                continue
            t = threading.Thread(target=_run_brand_scrape, args=(b["name"], pcs), daemon=True)
            t.start()
            started.append(b["name"])
            time.sleep(0.05)
    return {"started": started, "skipped": skipped}


# ========== Scrape History ==========
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _friendly_filename(brand_name: str, iso_dt: str) -> str:
    """Produce filename like 'Chheda_5-May-2026_15-30-12.csv' from ISO datetime."""
    try:
        dt = datetime.fromisoformat(iso_dt.replace("Z", "")) if iso_dt else datetime.utcnow()
    except Exception:
        dt = datetime.utcnow()
    safe_brand = re.sub(r"[^A-Za-z0-9_-]+", "_", brand_name).strip("_") or "Brand"
    return f"{safe_brand}_{dt.day}-{_MONTHS[dt.month-1]}-{dt.year}_{dt.hour:02d}-{dt.minute:02d}-{dt.second:02d}.csv"


@app.get("/api/brands/{name}/runs")
def list_runs(name: str):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute(
            """SELECT id, started_at, finished_at, pincode_codes, asin_count, pincode_count,
                      total_expected, total_results, status
               FROM scrape_runs WHERE brand_id = ? ORDER BY id DESC""",
            (b["id"],),
        )
        runs = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["pincodes"] = json.loads(d.pop("pincode_codes") or "[]")
            except Exception:
                d["pincodes"] = []
            runs.append(d)
        # Backfill: include legacy results without run_id as a synthetic group per scraped_at minute
        cur.execute(
            "SELECT COUNT(*) AS c FROM scrape_results WHERE brand_id = ? AND run_id IS NULL",
            (b["id"],),
        )
        legacy_count = cur.fetchone()["c"]
    return {"runs": runs, "legacy_results_without_run": legacy_count}


@app.get("/api/brands/{name}/runs/{run_id}")
def get_run(name: str, run_id: int):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM scrape_runs WHERE id = ? AND brand_id = ?",
            (run_id, b["id"]),
        )
        run = cur.fetchone()
        if not run:
            raise HTTPException(404, "Run not found")
        run = dict(run)
        try:
            run["pincodes"] = json.loads(run.pop("pincode_codes") or "[]")
        except Exception:
            run["pincodes"] = []
        cur.execute(
            """SELECT sr.*, a.asin AS asin, a.notes AS notes
               FROM scrape_results sr JOIN asins a ON a.id = sr.asin_id
               WHERE sr.run_id = ? AND sr.brand_id = ?
               ORDER BY a.asin ASC, sr.pincode_code ASC""",
            (run_id, b["id"]),
        )
        results = [dict(r) for r in cur.fetchall()]
    return {"run": run, "results": results}


@app.get("/api/brands/{name}/runs/{run_id}/csv")
def download_run_csv(name: str, run_id: int):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM scrape_runs WHERE id = ? AND brand_id = ?",
            (run_id, b["id"]),
        )
        run = cur.fetchone()
        if not run:
            raise HTTPException(404, "Run not found")
        run = dict(run)
        cur.execute(
            """SELECT sr.*, a.asin AS asin, a.notes AS notes
               FROM scrape_results sr JOIN asins a ON a.id = sr.asin_id
               WHERE sr.run_id = ? AND sr.brand_id = ?
               ORDER BY a.asin ASC, sr.pincode_code ASC""",
            (run_id, b["id"]),
        )
        rows = [dict(r) for r in cur.fetchall()]

    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow([
        "Brand", "ASIN", "Notes", "Pincode", "City",
        "Title", "Price", "Seller", "Rating", "Reviews",
        "Stock", "Delivery", "Pincode Verified", "Scraped At",
    ])
    for r in rows:
        w.writerow([
            name, r.get("asin", ""), r.get("notes", "") or "",
            r.get("pincode_code", ""), r.get("pincode_city", ""),
            r.get("title", "") or "", r.get("price", "") or "",
            r.get("seller", "") or "", r.get("rating", "") or "",
            r.get("reviews", "") or "", r.get("stock", "") or "",
            r.get("delivery", "") or "",
            "Yes" if r.get("pincode_verified") else "No",
            r.get("scraped_at", "") or "",
        ])
    csv_bytes = buf.getvalue().encode("utf-8")
    fname = _friendly_filename(name, run.get("started_at") or "")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{fname}\""},
    )


@app.delete("/api/brands/{name}/runs/{run_id}")
def delete_run(name: str, run_id: int):
    b = require_brand(name)
    with cursor() as cur:
        cur.execute("DELETE FROM scrape_results WHERE run_id = ? AND brand_id = ?", (run_id, b["id"]))
        cur.execute("DELETE FROM scrape_runs WHERE id = ? AND brand_id = ?", (run_id, b["id"]))
    return {"deleted": True}


@app.get("/api/brands/{name}/history")
def history(
    name: str,
    pincode: Optional[str] = Query(None),
    asin: Optional[str] = Query(None),
):
    """Return historical scrape results for the brand, optionally filtered by pincode/asin.
    Useful for both deep-dive (single ASIN over time) and matrix (multi-ASIN over time) views.
    """
    b = require_brand(name)
    where = ["sr.brand_id = ?"]
    params: List[Any] = [b["id"]]
    if pincode:
        where.append("sr.pincode_code = ?")
        params.append(pincode)
    if asin:
        where.append("a.asin = ?")
        params.append(asin.upper())
    where_clause = " AND ".join(where)

    with cursor() as cur:
        cur.execute(
            f"""SELECT sr.id, sr.run_id, sr.scraped_at, sr.pincode_code, sr.pincode_city,
                       sr.title, sr.price, sr.seller, sr.rating, sr.reviews, sr.stock,
                       sr.delivery, sr.pincode_verified,
                       a.asin AS asin, a.notes AS notes
                FROM scrape_results sr
                JOIN asins a ON a.id = sr.asin_id
                WHERE {where_clause}
                ORDER BY a.asin ASC, sr.pincode_code ASC, sr.scraped_at DESC""",
            tuple(params),
        )
        results = [dict(r) for r in cur.fetchall()]
        # Distinct lists for UI dropdowns
        cur.execute("SELECT DISTINCT pincode_code, pincode_city FROM scrape_results WHERE brand_id = ? ORDER BY pincode_code", (b["id"],))
        pincodes = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT DISTINCT a.asin, a.notes FROM scrape_results sr
               JOIN asins a ON a.id = sr.asin_id WHERE sr.brand_id = ? ORDER BY a.asin""",
            (b["id"],),
        )
        asins = [dict(r) for r in cur.fetchall()]
    return {"results": results, "pincodes": pincodes, "asins": asins}
