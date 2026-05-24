from worker import run_price_headless

def main():
    urls = ["https://www.trendyol.com/awox/orion-tost-ve-izgara-makinesi-180-acilabilir-1800w-kirmizi-p-35222956"]
    res, html = run_price_headless(urls, "")
    print("RESULT DICTS:", res)

if __name__ == "__main__":
    main()
