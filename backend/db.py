"""SQLite database setup and connection helpers."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("SQLITE_PATH", "/app/backend/data.db")

_lock = threading.Lock()


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def cursor():
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with cursor() as cur:
        # Step 1: Create tables (IF NOT EXISTS so legacy table is preserved)
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS brands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pincodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                city TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(brand_id, code),
                FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS asins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_id INTEGER NOT NULL,
                asin TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(brand_id, asin),
                FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                pincode_codes TEXT DEFAULT '[]',
                asin_count INTEGER DEFAULT 0,
                pincode_count INTEGER DEFAULT 0,
                total_expected INTEGER DEFAULT 0,
                total_results INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scrape_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asin_id INTEGER NOT NULL,
                brand_id INTEGER NOT NULL,
                run_id INTEGER,
                pincode_code TEXT NOT NULL,
                pincode_city TEXT NOT NULL,
                title TEXT DEFAULT '',
                price TEXT DEFAULT '',
                seller TEXT DEFAULT '',
                rating TEXT DEFAULT '',
                reviews TEXT DEFAULT '',
                stock TEXT DEFAULT '',
                delivery TEXT DEFAULT '',
                pincode_verified INTEGER DEFAULT 0,
                scraped_at TEXT NOT NULL,
                FOREIGN KEY (asin_id) REFERENCES asins(id) ON DELETE CASCADE,
                FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES scrape_runs(id) ON DELETE SET NULL
            );
            """
        )
        # Step 2: Migration - add run_id column to old scrape_results table if missing
        cur.execute("PRAGMA table_info(scrape_results)")
        cols = [r["name"] for r in cur.fetchall()]
        if "run_id" not in cols:
            try:
                cur.execute("ALTER TABLE scrape_results ADD COLUMN run_id INTEGER")
            except Exception:
                pass
        # Step 3: Create indexes (now that all columns exist)
        cur.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_pincodes_brand ON pincodes(brand_id);
            CREATE INDEX IF NOT EXISTS idx_asins_brand ON asins(brand_id);
            CREATE INDEX IF NOT EXISTS idx_results_brand ON scrape_results(brand_id);
            CREATE INDEX IF NOT EXISTS idx_results_asin ON scrape_results(asin_id);
            CREATE INDEX IF NOT EXISTS idx_results_run ON scrape_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_runs_brand ON scrape_runs(brand_id);
            """
        )


def seed_if_empty():
    """Create the Chheda brand on first run if no brand exists."""
    now = datetime.utcnow().isoformat()
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM brands")
        c = cur.fetchone()["c"]
        if c > 0:
            return
        cur.execute(
            "INSERT INTO brands(name, created_at) VALUES (?, ?)",
            ("Chheda", now),
        )
        bid = cur.lastrowid
        cur.execute(
            "INSERT INTO pincodes(brand_id, code, city, created_at) VALUES (?, ?, ?, ?)",
            (bid, "400064", "Mumbai", now),
        )
        seed_asins = [
            ("B08WJ12R6N", "Banana Chips"),
            ("B08WHGWJH4", ""),
            ("B08WHKT89Q", ""),
            ("B08WJ4C33Z", ""),
        ]
        for asin, notes in seed_asins:
            cur.execute(
                "INSERT INTO asins(brand_id, asin, notes, created_at) VALUES (?, ?, ?, ?)",
                (bid, asin, notes, now),
            )
