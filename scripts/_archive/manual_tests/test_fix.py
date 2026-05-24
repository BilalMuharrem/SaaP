import time
import sys
import os
from worker import create_driver, check_tracked_products
from app import app
from models import db, TrackedProduct

def test_driver_init():
    print("Testing driver initialization (with Fallback)...")
    try:
        # This will now use the fallback logic in worker.py
        driver = create_driver()
        driver.get("https://www.google.com")
        print(f"Success! Title: {driver.title}")
        driver.quit()
        return True
    except Exception as e:
        print(f"Driver Init Failed: {e}")
        return False

def test_metadata_filtering():
    print("\nTesting metadata filtering in models...")
    from models import Job
    with app.app_context():
        job = Job(user_id=1, job_type='price', urls='["https://amazon.com", "__COST__:250"]')
        urls = job.get_urls(filter_metadata=True)
        print(f"Filtered URLs: {urls}")
        raw_urls = job.get_urls(filter_metadata=False)
        print(f"Raw URLs: {raw_urls}")
        
        if len(urls) == 1 and "__COST__:250" not in urls and len(raw_urls) == 2:
            print("Metadata filtering works!")
            return True
        else:
            print("Metadata filtering FAILED!")
            return False

if __name__ == "__main__":
    d_ok = test_driver_init()
    m_ok = test_metadata_filtering()
    
    if d_ok and m_ok:
        print("\nALL TESTS PASSED!")
        sys.exit(0)
    else:
        print("\nSOME TESTS FAILED!")
        sys.exit(1)
