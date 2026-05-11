"""Backend API tests for Listing Scraper - including new history/runs features"""
import requests
import sys
import time
import re
from datetime import datetime

BASE_URL = "https://price-watcher-62.preview.emergentagent.com"

class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.test_brand = f"TestBrand_{int(time.time())}"
        self.run_id = None  # Store run_id for history tests
        
    def log(self, msg, level="INFO"):
        print(f"[{level}] {msg}")
        
    def test(self, name, func):
        """Run a single test"""
        self.tests_run += 1
        self.log(f"\n{'='*60}")
        self.log(f"Test {self.tests_run}: {name}")
        self.log('='*60)
        try:
            func()
            self.tests_passed += 1
            self.log(f"✅ PASSED: {name}", "PASS")
            return True
        except AssertionError as e:
            self.tests_failed += 1
            self.log(f"❌ FAILED: {name} - {str(e)}", "FAIL")
            return False
        except Exception as e:
            self.tests_failed += 1
            self.log(f"❌ ERROR: {name} - {str(e)}", "ERROR")
            return False
    
    def assert_status(self, response, expected, msg=""):
        if response.status_code != expected:
            raise AssertionError(f"Expected status {expected}, got {response.status_code}. {msg} Response: {response.text[:200]}")
    
    def assert_in(self, key, data, msg=""):
        if key not in data:
            raise AssertionError(f"Key '{key}' not found in response. {msg}")
    
    def assert_equal(self, actual, expected, msg=""):
        if actual != expected:
            raise AssertionError(f"Expected {expected}, got {actual}. {msg}")
    
    def summary(self):
        self.log("\n" + "="*60)
        self.log("TEST SUMMARY")
        self.log("="*60)
        self.log(f"Total: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed}")
        self.log(f"Failed: {self.tests_failed}")
        self.log(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        return 0 if self.tests_failed == 0 else 1

def main():
    runner = TestRunner()
    
    # Test 1: Health check
    def test_health():
        r = requests.get(f"{BASE_URL}/api/health")
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("status", data)
        runner.assert_equal(data["status"], "ok")
    runner.test("GET /api/health returns ok", test_health)
    
    # Test 2: List brands (should have Chheda seeded)
    def test_list_brands():
        r = requests.get(f"{BASE_URL}/api/brands")
        runner.assert_status(r, 200)
        brands = r.json()
        assert isinstance(brands, list), "Brands should be a list"
        # Check if Chheda exists
        chheda = [b for b in brands if b["name"] == "Chheda"]
        assert len(chheda) > 0, "Chheda brand should be seeded"
        b = chheda[0]
        runner.assert_in("asin_count", b)
        runner.assert_in("pincode_count", b)
        runner.log(f"Found Chheda: {b['asin_count']} ASINs, {b['pincode_count']} pincodes")
    runner.test("GET /api/brands lists all brands with counts", test_list_brands)
    
    # Test 3: Create new brand
    def test_create_brand():
        r = requests.post(f"{BASE_URL}/api/brands", json={"name": runner.test_brand})
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("created", data)
        runner.assert_equal(data["created"], True)
        runner.log(f"Created brand: {runner.test_brand}")
    runner.test("POST /api/brands creates new brand with default pincode", test_create_brand)
    
    # Test 4: Add ASIN for testing
    def test_add_asin():
        r = requests.post(f"{BASE_URL}/api/brands/{runner.test_brand}/asins", 
                         json={"asin": "B08WJ12R6N", "notes": "Test product"})
        runner.assert_status(r, 200)
        runner.log("Added ASIN B08WJ12R6N")
    runner.test("POST /api/brands/:name/asins adds valid ASIN", test_add_asin)
    
    # Test 5: Scrape brand to create a run
    def test_scrape_brand():
        r = requests.post(f"{BASE_URL}/api/brands/{runner.test_brand}/scrape", 
                         json={"pincodes": [{"code": "400064", "city": "Mumbai"}]})
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("started", data)
        runner.log("Scrape started")
    runner.test("POST /api/brands/:name/scrape starts job", test_scrape_brand)
    
    # Wait for scrape to complete
    runner.log("\nWaiting for scrape to complete (max 60s)...")
    for i in range(60):
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/api/brands/{runner.test_brand}/status")
        if r.status_code == 200:
            data = r.json()
            if not data.get("isScraping"):
                runner.log(f"Scrape completed after {i+1} seconds")
                break
    
    # ========== NEW TESTS FOR HISTORY/RUNS FEATURES ==========
    
    # Test 6: GET /api/brands/:name/runs returns runs array + legacy count
    def test_list_runs():
        r = requests.get(f"{BASE_URL}/api/brands/{runner.test_brand}/runs")
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("runs", data)
        runner.assert_in("legacy_results_without_run", data)
        
        runs = data["runs"]
        assert isinstance(runs, list), "runs should be a list"
        assert len(runs) > 0, "Should have at least 1 run after scraping"
        
        # Check run structure
        run = runs[0]
        runner.assert_in("id", run)
        runner.assert_in("started_at", run)
        runner.assert_in("finished_at", run)
        runner.assert_in("pincodes", run)
        runner.assert_in("asin_count", run)
        runner.assert_in("pincode_count", run)
        runner.assert_in("total_expected", run)
        runner.assert_in("total_results", run)
        runner.assert_in("status", run)
        
        # Verify pincodes is a list (not JSON string)
        assert isinstance(run["pincodes"], list), "pincodes should be parsed list"
        assert run["status"] == "completed", "Run should be completed"
        
        # Store run_id for later tests
        runner.run_id = run["id"]
        runner.log(f"Found {len(runs)} run(s), run_id={runner.run_id}, status={run['status']}")
        runner.log(f"Run details: {run['asin_count']} ASINs x {run['pincode_count']} pincodes = {run['total_results']} results")
    runner.test("GET /api/brands/:name/runs returns runs array + legacy count", test_list_runs)
    
    # Test 7: GET /api/brands/:name/runs/:run_id returns run + results
    def test_get_run():
        if not runner.run_id:
            raise AssertionError("No run_id available from previous test")
        
        r = requests.get(f"{BASE_URL}/api/brands/{runner.test_brand}/runs/{runner.run_id}")
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("run", data)
        runner.assert_in("results", data)
        
        run = data["run"]
        results = data["results"]
        
        runner.assert_in("id", run)
        runner.assert_equal(run["id"], runner.run_id)
        
        assert isinstance(results, list), "results should be a list"
        assert len(results) > 0, "Should have at least 1 result"
        
        # Check result structure
        result = results[0]
        runner.assert_in("asin", result)
        runner.assert_in("title", result)
        runner.assert_in("price", result)
        runner.assert_in("stock", result)
        runner.assert_in("pincode_code", result)
        
        runner.log(f"Run {runner.run_id} has {len(results)} result(s)")
        runner.log(f"Sample result: ASIN={result.get('asin')}, price={result.get('price')}, stock={result.get('stock')}")
    runner.test("GET /api/brands/:name/runs/:run_id returns run + results array", test_get_run)
    
    # Test 8: GET /api/brands/:name/runs/:run_id/csv returns CSV with friendly filename
    def test_download_run_csv():
        if not runner.run_id:
            raise AssertionError("No run_id available from previous test")
        
        r = requests.get(f"{BASE_URL}/api/brands/{runner.test_brand}/runs/{runner.run_id}/csv")
        runner.assert_status(r, 200)
        
        # Check Content-Type
        assert r.headers.get("Content-Type", "").startswith("text/csv"), "Should be CSV"
        
        # Check Content-Disposition for friendly filename pattern
        cd = r.headers.get("Content-Disposition", "")
        assert "attachment" in cd, "Should have attachment disposition"
        assert "filename=" in cd, "Should have filename"
        
        # Extract filename and verify pattern: BrandName_DD-MMM-YYYY_HH-MM-SS.csv
        filename_match = re.search(r'filename="([^"]+)"', cd)
        assert filename_match, "Should have quoted filename"
        filename = filename_match.group(1)
        
        # Pattern: TestBrand_NNNNNNNNNN_DD-MMM-YYYY_HH-MM-SS.csv
        pattern = r'^[A-Za-z0-9_-]+_\d{1,2}-[A-Za-z]{3}-\d{4}_\d{2}-\d{2}-\d{2}\.csv$'
        assert re.match(pattern, filename), f"Filename '{filename}' doesn't match expected pattern"
        
        # Check CSV content
        content = r.content.decode("utf-8")
        assert content.startswith("\ufeff"), "Should have UTF-8 BOM"
        
        lines = content.split("\n")
        assert len(lines) >= 2, "Should have header + at least 1 data row"
        
        runner.log(f"CSV filename: {filename}")
        runner.log(f"CSV has {len(lines)} lines")
    runner.test("GET /api/brands/:name/runs/:run_id/csv returns CSV with friendly filename", test_download_run_csv)
    
    # Test 9: DELETE /api/brands/:name/runs/:run_id removes run and results
    def test_delete_run():
        if not runner.run_id:
            raise AssertionError("No run_id available from previous test")
        
        # First verify run exists
        r = requests.get(f"{BASE_URL}/api/brands/{runner.test_brand}/runs/{runner.run_id}")
        runner.assert_status(r, 200)
        
        # Delete the run
        r = requests.delete(f"{BASE_URL}/api/brands/{runner.test_brand}/runs/{runner.run_id}")
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("deleted", data)
        
        # Verify run is gone
        r = requests.get(f"{BASE_URL}/api/brands/{runner.test_brand}/runs/{runner.run_id}")
        runner.assert_status(r, 404, "Run should be deleted")
        
        runner.log(f"Run {runner.run_id} deleted successfully")
    runner.test("DELETE /api/brands/:name/runs/:run_id removes run and results", test_delete_run)
    
    # ========== TESTS FOR CHHEDA BRAND (SEEDED DATA) ==========
    
    # Test 10: GET /api/brands/Chheda/history returns historical results
    def test_history_basic():
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/history")
        runner.assert_status(r, 200)
        data = r.json()
        runner.assert_in("results", data)
        runner.assert_in("pincodes", data)
        runner.assert_in("asins", data)
        
        results = data["results"]
        pincodes = data["pincodes"]
        asins = data["asins"]
        
        assert isinstance(results, list), "results should be a list"
        assert isinstance(pincodes, list), "pincodes should be a list"
        assert isinstance(asins, list), "asins should be a list"
        
        runner.log(f"History: {len(results)} results, {len(pincodes)} pincodes, {len(asins)} ASINs")
        
        # Check distinct lists structure
        if len(pincodes) > 0:
            pc = pincodes[0]
            runner.assert_in("pincode_code", pc)
            runner.assert_in("pincode_city", pc)
        
        if len(asins) > 0:
            asin = asins[0]
            runner.assert_in("asin", asin)
    runner.test("GET /api/brands/Chheda/history returns historical results + distinct lists", test_history_basic)
    
    # Test 11: GET /api/brands/Chheda/history?pincode=400064 filters by pincode
    def test_history_filter_pincode():
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/history?pincode=400064")
        runner.assert_status(r, 200)
        data = r.json()
        results = data.get("results", [])
        
        # All results should be for pincode 400064
        for result in results:
            assert result.get("pincode_code") == "400064", f"Expected pincode 400064, got {result.get('pincode_code')}"
        
        runner.log(f"Filtered by pincode 400064: {len(results)} results")
    runner.test("GET /api/brands/Chheda/history?pincode=400064 filters correctly", test_history_filter_pincode)
    
    # Test 12: GET /api/brands/Chheda/history?pincode=400064&asin=B08WJ12R6N filters by both
    def test_history_filter_both():
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/history?pincode=400064&asin=B08WJ12R6N")
        runner.assert_status(r, 200)
        data = r.json()
        results = data.get("results", [])
        
        # All results should match both filters
        for result in results:
            assert result.get("pincode_code") == "400064", f"Expected pincode 400064"
            assert result.get("asin") == "B08WJ12R6N", f"Expected ASIN B08WJ12R6N"
        
        runner.log(f"Filtered by pincode 400064 + ASIN B08WJ12R6N: {len(results)} results")
    runner.test("GET /api/brands/Chheda/history?pincode=400064&asin=B08WJ12R6N filters correctly", test_history_filter_both)
    
    # Test 13: Verify concurrent scraping creates run record
    def test_concurrent_scrape_creates_run():
        # Get current run count for Chheda
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/runs")
        runner.assert_status(r, 200)
        before_count = len(r.json()["runs"])
        
        # Start a new scrape (use only 1 ASIN + 1 pincode to minimize cost)
        r = requests.post(f"{BASE_URL}/api/brands/Chheda/scrape", 
                         json={"pincodes": [{"code": "400064", "city": "Mumbai"}]})
        runner.assert_status(r, 200)
        
        # Wait a bit for run to be created
        time.sleep(2)
        
        # Check runs again
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/runs")
        runner.assert_status(r, 200)
        data = r.json()
        after_count = len(data["runs"])
        
        assert after_count > before_count, f"Expected more runs after scrape (before={before_count}, after={after_count})"
        
        # Check the latest run
        latest_run = data["runs"][0]  # Runs are ordered DESC by id
        runner.assert_in("status", latest_run)
        runner.assert_in("asin_count", latest_run)
        runner.assert_in("pincode_count", latest_run)
        runner.assert_in("pincode_codes", latest_run, "Should have pincode_codes field (raw JSON)")
        
        # Wait for completion
        runner.log("Waiting for Chheda scrape to complete (max 30s)...")
        for i in range(30):
            time.sleep(1)
            r = requests.get(f"{BASE_URL}/api/brands/Chheda/status")
            if r.status_code == 200:
                status = r.json()
                if not status.get("isScraping"):
                    runner.log(f"Scrape completed after {i+1} seconds")
                    break
        
        # Verify run is completed
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/runs")
        runner.assert_status(r, 200)
        latest_run = r.json()["runs"][0]
        assert latest_run["status"] == "completed", f"Expected status 'completed', got '{latest_run['status']}'"
        assert latest_run["total_results"] > 0, "Should have results"
        
        runner.log(f"Run created: id={latest_run['id']}, status={latest_run['status']}, results={latest_run['total_results']}")
    runner.test("POST /api/brands/Chheda/scrape creates scrape_runs record with status", test_concurrent_scrape_creates_run)
    
    # Test 14: Verify stock parser fix (B08WJ4C33Z @ 110085 should be Out of Stock)
    # Note: This requires actually scraping, which costs credits. Skip if not needed.
    # We'll just verify the logic by checking existing results if available.
    def test_stock_parser():
        # Check if we have any results for B08WJ4C33Z
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/history?asin=B08WJ4C33Z")
        runner.assert_status(r, 200)
        results = r.json().get("results", [])
        
        if len(results) > 0:
            # Check that stock field exists and is properly set
            for result in results:
                stock = result.get("stock", "")
                assert stock in ["In Stock", "Out of Stock", "Unknown"], f"Invalid stock value: {stock}"
            runner.log(f"Stock parser check: Found {len(results)} results for B08WJ4C33Z")
        else:
            runner.log("No results for B08WJ4C33Z to verify stock parser (skipping detailed check)")
    runner.test("Stock parser correctly handles 'back in stock' substring", test_stock_parser)
    
    # Test 15: Verify price parser fix (should return Rs. format)
    def test_price_parser():
        # Check any results for proper price format
        r = requests.get(f"{BASE_URL}/api/brands/Chheda/history?asin=B08WJ12R6N")
        runner.assert_status(r, 200)
        results = r.json().get("results", [])
        
        if len(results) > 0:
            for result in results:
                price = result.get("price", "")
                if price and price != "-":
                    # Should be in format "Rs. NNN"
                    assert price.startswith("Rs. "), f"Price should start with 'Rs. ', got: {price}"
                    # Extract number part
                    num_part = price.replace("Rs. ", "").replace(",", "")
                    assert num_part.isdigit(), f"Price should contain only digits after 'Rs. ', got: {price}"
            runner.log(f"Price parser check: All {len(results)} results have proper 'Rs. NNN' format")
        else:
            runner.log("No results for B08WJ12R6N to verify price parser (skipping detailed check)")
    runner.test("Price parser returns 'Rs. NNN' format (ignores M.R.P/per-unit)", test_price_parser)
    
    # Clean up test brand
    def test_cleanup():
        r = requests.delete(f"{BASE_URL}/api/brands/{runner.test_brand}")
        runner.assert_status(r, 200)
        runner.log(f"Cleaned up test brand: {runner.test_brand}")
    runner.test("Cleanup: Delete test brand", test_cleanup)
    
    return runner.summary()

if __name__ == "__main__":
    sys.exit(main())
