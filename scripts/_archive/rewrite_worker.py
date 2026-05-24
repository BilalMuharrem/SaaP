import re

with open("worker.py", "r") as f:
    content = f.read()

# 1. Replace imports and add celery tasks
content = content.replace("import undetected_chromedriver as uc", "from playwright.sync_api import sync_playwright\nimport cloudscraper\nfrom bs4 import BeautifulSoup\nfrom extensions import celery\nimport undetected_chromedriver as uc")

# 2. Add @celery.task to jobs
content = content.replace("def process_next_job(app):", "@celery.task\ndef process_job_task(job_id):\n    from app import app\n    process_next_job(app, job_id)")

content = content.replace("def check_tracked_products(app):", "def check_tracked_products(app):")

# 3. Add task wrapper for radar
task_wrapper = """
@celery.task
def check_tracked_products_task():
    from app import app
    with app.app_context():
        check_tracked_products(app)
"""
content += "\n" + task_wrapper

with open("worker_new.py", "w") as f:
    f.write(content)

print("Rewriter script finished.")
