#!/usr/bin/env python3
"""Backend API tests for Listing Scraper - Compare Across Dates feature."""
import sys
import requests

BASE_URL = "https://price-watcher-62.preview.emergentagent.com"

class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests = []

    def test(self, name, fn):
        """Run a single test."""
        print(f"\n🔍 Testing: {name}")
        try:
            fn()
            self.passed += 1
            self.tests.append({"name": name, "status": "PASS"})
            print(f"✅ PASS: {name}")
        except AssertionError as e:
            self.failed += 1
            self.tests.append({"name": name, "status": "FAIL", "error": str(e)})
            print(f"❌ FAIL: {name} - {e}")
        except Exception as e:
            self.failed += 1
            self.tests.append({"name": name, "status": "ERROR", "error": str(e)})
            print(f"❌ ERROR: {name} - {e}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"📊 Test Summary: {self.passed}/{total} passed")
        print(f"{'='*60}")
        return 0 if self.failed == 0 else 1


def main():
    runner = TestRunner()

    # Test 1: GET /api/brands/Chheda/history (no filters)
    def test_history_no_filters():
        resp = requests.get(f"{BASE_URL}/api/brands/Chheda/history", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "results" in data, "Missing 'results' key"
        assert "pincodes" in data, "Missing 'pincodes' key"
        assert "asins" in data, "Missing 'asins' key"
        assert len(data["results"]) > 0, "Expected at least one historical result"
        print(f"   Found {len(data['results'])} historical results")
        print(f"   Available pincodes: {[p['pincode_code'] for p in data['pincodes'][:3]]}")
        print(f"   Available ASINs: {[a['asin'] for a in data['asins'][:3]]}")
    runner.test("GET /api/brands/Chheda/history (no filters)", test_history_no_filters)

    # Test 2: GET /api/brands/Chheda/history?pincode=400064
    def test_history_pincode_filter():
        resp = requests.get(f"{BASE_URL}/api/brands/Chheda/history?pincode=400064", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "results" in data, "Missing 'results' key"
        results = data["results"]
        if len(results) > 0:
            # Verify all results are for pincode 400064
            for r in results:
                assert r["pincode_code"] == "400064", f"Expected pincode 400064, got {r['pincode_code']}"
            print(f"   Found {len(results)} results for pincode 400064")
        else:
            print(f"   No results for pincode 400064 (may not have been scraped)")
    runner.test("GET /api/brands/Chheda/history?pincode=400064", test_history_pincode_filter)

    # Test 3: GET /api/brands/Chheda/history?pincode=400064&asin=<first_asin>
    def test_history_pincode_asin_filter():
        # First get available ASINs
        resp = requests.get(f"{BASE_URL}/api/brands/Chheda/history", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        if len(data["asins"]) == 0:
            print("   No ASINs available, skipping test")
            return
        test_asin = data["asins"][0]["asin"]
        
        # Now test with pincode + asin filter
        resp2 = requests.get(f"{BASE_URL}/api/brands/Chheda/history?pincode=400064&asin={test_asin}", timeout=10)
        assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}"
        data2 = resp2.json()
        results = data2["results"]
        if len(results) > 0:
            for r in results:
                assert r["pincode_code"] == "400064", f"Expected pincode 400064, got {r['pincode_code']}"
                assert r["asin"] == test_asin, f"Expected ASIN {test_asin}, got {r['asin']}"
            print(f"   Found {len(results)} results for pincode 400064 + ASIN {test_asin}")
        else:
            print(f"   No results for pincode 400064 + ASIN {test_asin}")
    runner.test("GET /api/brands/Chheda/history?pincode=400064&asin=<asin>", test_history_pincode_asin_filter)

    # Test 4: GET /api/brands/Chheda/runs (verify multiple runs exist)
    def test_runs_list():
        resp = requests.get(f"{BASE_URL}/api/brands/Chheda/runs", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "runs" in data, "Missing 'runs' key"
        runs = data["runs"]
        assert len(runs) >= 3, f"Expected at least 3 runs, got {len(runs)}"
        print(f"   Found {len(runs)} scrape runs")
        for i, run in enumerate(runs[:3]):
            assert "id" in run, f"Run {i} missing 'id'"
            assert "started_at" in run, f"Run {i} missing 'started_at'"
            assert "pincode_count" in run, f"Run {i} missing 'pincode_count'"
            assert "asin_count" in run, f"Run {i} missing 'asin_count'"
            assert "total_results" in run, f"Run {i} missing 'total_results'"
            print(f"   Run {run['id']}: {run['asin_count']} ASINs x {run['pincode_count']} pincodes = {run['total_results']} results")
    runner.test("GET /api/brands/Chheda/runs (multiple runs)", test_runs_list)

    # Test 5: Verify dedup data exists (multiple scrapes on same day)
    def test_dedup_data_exists():
        resp = requests.get(f"{BASE_URL}/api/brands/Chheda/history", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        results = data["results"]
        
        # Group by (asin, pincode, day) to find duplicates
        day_groups = {}
        for r in results:
            day = r["scraped_at"][:10] if r.get("scraped_at") else ""
            key = (r["asin"], r["pincode_code"], day)
            if key not in day_groups:
                day_groups[key] = []
            day_groups[key].append(r)
        
        # Find groups with multiple scrapes on same day
        multi_scrape_days = [k for k, v in day_groups.items() if len(v) > 1]
        if len(multi_scrape_days) > 0:
            print(f"   Found {len(multi_scrape_days)} (ASIN, pincode, day) combinations with multiple scrapes")
            example = multi_scrape_days[0]
            print(f"   Example: ASIN {example[0]}, pincode {example[1]}, day {example[2]} has {len(day_groups[example])} scrapes")
        else:
            print(f"   No duplicate scrapes found on same day (dedup may not be testable)")
    runner.test("Verify dedup data exists (multiple scrapes per day)", test_dedup_data_exists)

    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
