import mysql.connector
from datetime import datetime
from collections import Counter
import re
import schedule
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

if __package__:
    from .config import get_database_config
else:
    from config import get_database_config


# -------------------------
# MYSQL CONNECTION
# -------------------------

conn = mysql.connector.connect(**get_database_config(autocommit=False))

cursor = conn.cursor()

# Recreate table with UNIQUE constraint on (date, metal, karat)
# This guarantees no duplicate rows at the database level
cursor.execute("DROP TABLE IF EXISTS metal_rates")

cursor.execute("""
    CREATE TABLE metal_rates (
        id    INT AUTO_INCREMENT PRIMARY KEY,
        date  DATE        NOT NULL,
        metal VARCHAR(20) NOT NULL,
        karat VARCHAR(20) NOT NULL,
        price INT         NOT NULL,
        UNIQUE KEY uq_date_metal_karat (date, metal, karat)
    )
""")

conn.commit()
print("Table metal_rates ready.")


# -------------------------
# BROWSER
# -------------------------

def start_browser():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


# -------------------------
# SCRAPERS
# -------------------------

def scrape_grt(driver):
    """
    GRT dropdown shows:
      GOLD 24 KT/1g - Rs 15104
      GOLD 22 KT/1g - Rs 13835
      GOLD 18 KT/1g - Rs 11328
      GOLD 14 KT/1g - Rs  8810
      SILVER 1g     - Rs   260
    """
    driver.get("https://www.grtjewels.com")
    rates = {}

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Wait a bit for any dynamic content to load
        time.sleep(3)

        page_text = driver.find_element(By.TAG_NAME, "body").text
        html      = driver.page_source

        print("  GRT page content length:", len(page_text))

        # Try multiple strategies for each karat
        karat_map = {"24": "24KT", "22": "22KT", "18": "18KT", "14": "14KT"}
        for num, key in karat_map.items():
            found = False

            # Strategy 1: Original pattern
            m = re.search(
                rf"GOLD\s*{num}\s*KT\s*/\s*1g\s*[-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  GRT {key} (method 1): Rs{rates[key]}")
                found = True
                continue

            # Strategy 2: More flexible pattern
            m = re.search(
                rf"GOLD\s*{num}\s*KT[^:]*[:\-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  GRT {key} (method 2): Rs{rates[key]}")
                found = True
                continue

            # Strategy 3: HTML parsing
            m = re.search(
                rf"GOLD[^<]*{num}\s*KT[^<]*?(?:Rs\.?|₹)\s*([\d,]+)",
                html, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  GRT {key} (method 3): Rs{rates[key]}")
                found = True
                continue

            # Strategy 4: Very loose pattern
            m = re.search(
                rf"{num}\s*KT.*?(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  GRT {key} (method 4): Rs{rates[key]}")
                found = True
                continue

            if not found:
                print(f"  GRT {key}: NOT FOUND")

        # Try multiple strategies for silver
        silver_found = False

        # Strategy 1: Original pattern
        silver_m = re.search(
            r"SILVER\s*1g\s*[-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
            page_text, re.I
        )
        if silver_m:
            rates["Silver"] = int(silver_m.group(1).replace(",", ""))
            print(f"  GRT Silver (method 1): Rs{rates['Silver']}")
            silver_found = True

        # Strategy 2: More flexible pattern
        if not silver_found:
            silver_m = re.search(
                r"SILVER[^:]*[:\-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  GRT Silver (method 2): Rs{rates['Silver']}")
                silver_found = True

        # Strategy 3: HTML parsing
        if not silver_found:
            silver_m = re.search(
                r"SILVER[^<]*?(?:Rs\.?|₹)\s*([\d,]+)",
                html, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  GRT Silver (method 3): Rs{rates['Silver']}")
                silver_found = True

        if not silver_found:
            print("  GRT Silver: NOT FOUND")

    except Exception as e:
        print("GRT ERROR:", e)

    return rates


def scrape_thangamayil(driver):
    """
    Thangamayil red header bar should show:
      GOLD RATE 22k (1gm): Rs13,835
      GOLD RATE 24k (1gm): Rs15,093
      GOLD RATE 18k (1gm): Rs11,320
      GOLD RATE 14k (1gm): Rs8,810
      SILVER RATE (1gm):   Rs260
    """
    driver.get("https://www.thangamayil.com")
    rates = {}

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Wait a bit for any dynamic content to load
        time.sleep(3)

        page_text = driver.find_element(By.TAG_NAME, "body").text
        html = driver.page_source

        print("  Thangamayil page content length:", len(page_text))

        # Try to scrape all karats directly
        karat_map = {"22": "22KT", "24": "24KT", "18": "18KT", "14": "14KT"}
        for num, key in karat_map.items():
            found = False

            # Strategy 1: Original pattern
            m = re.search(
                rf"GOLD\s*RATE\s*{num}[kK]\s*\(1gm\)\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  Thangamayil {key} (method 1): Rs{rates[key]}")
                found = True
                continue

            # Strategy 2: More flexible pattern
            m = re.search(
                rf"GOLD\s*RATE\s*{num}[kK]\s*\(1gm\)\s*[-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  Thangamayil {key} (method 2): Rs{rates[key]}")
                found = True
                continue

            # Strategy 3: HTML parsing
            m = re.search(
                rf"GOLD[^<]*{num}[kK][^<]*?(?:Rs\.?|₹)\s*([\d,]+)",
                html, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  Thangamayil {key} (method 3): Rs{rates[key]}")
                found = True
                continue

            # Strategy 4: Very loose pattern
            m = re.search(
                rf"{num}[kK].*?(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  Thangamayil {key} (method 4): Rs{rates[key]}")
                found = True
                continue

            if not found:
                print(f"  Thangamayil {key}: NOT FOUND")

        # Try multiple strategies for silver
        silver_found = False

        # Strategy 1: Original pattern
        silver_m = re.search(
            r"SILVER\s*RATE\s*\(1gm\)\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)",
            page_text
        )
        if silver_m:
            rates["Silver"] = int(silver_m.group(1).replace(",", ""))
            print(f"  Thangamayil Silver (method 1): Rs{rates['Silver']}")
            silver_found = True

        # Strategy 2: More flexible pattern
        if not silver_found:
            silver_m = re.search(
                r"SILVER\s*RATE\s*\(1gm\)\s*[-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  Thangamayil Silver (method 2): Rs{rates['Silver']}")
                silver_found = True

        # Strategy 3: HTML parsing
        if not silver_found:
            silver_m = re.search(
                r"SILVER[^<]*?(?:Rs\.?|₹)\s*([\d,]+)",
                html, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  Thangamayil Silver (method 3): Rs{rates['Silver']}")
                silver_found = True

        # Strategy 4: Very loose pattern
        if not silver_found:
            silver_m = re.search(
                r"SILVER.*?(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  Thangamayil Silver (method 4): Rs{rates['Silver']}")
                silver_found = True

        if not silver_found:
            print("  Thangamayil Silver: NOT FOUND")

    except Exception as e:
        print("THANGAMAYIL ERROR:", e)

    return rates


def scrape_lalitha(driver):
    """
    Lalitha modal should show all karats:
      Gold (24KT / 1g)   Rs15,093
      Gold (22KT / 1g)   Rs13,835
      Gold (18KT / 1g)   Rs11,320
      Gold (14KT / 1g)   Rs8,810
      Silver (1g)        Rs260
    """
    driver.get("https://www.lalithaajewellery.com")
    rates = {}

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(3)

        html      = driver.page_source
        page_text = driver.find_element(By.TAG_NAME, "body").text

        print("  Lalitha page content length:", len(page_text))

        # Try to scrape all karats directly
        karat_map = {"24": "24KT", "22": "22KT", "18": "18KT", "14": "14KT"}
        for num, key in karat_map.items():
            found = False

            # Strategy 1: HTML parsing with specific karat
            gold_m = re.search(
                rf"Gold\s*\(\s*{num}\s*KT\s*/\s*1g\s*\)[^₹Rs]{0,50}(?:₹|Rs\.?)\s*([\d,]+)",
                html, re.I | re.DOTALL
            )
            if gold_m:
                rates[key] = int(gold_m.group(1).replace(",", ""))
                print(f"  Lalitha {key} (HTML): Rs{rates[key]}")
                found = True
                continue

            # Strategy 2: Page text parsing
            gold_m = re.search(
                rf"Gold\s*\(\s*{num}\s*KT\s*/\s*1g\s*\)\s*(?:₹|Rs\.?)\s*([\d,]+)",
                page_text, re.I
            )
            if gold_m:
                rates[key] = int(gold_m.group(1).replace(",", ""))
                print(f"  Lalitha {key} (text): Rs{rates[key]}")
                found = True
                continue

            # Strategy 3: More flexible HTML pattern
            gold_m = re.search(
                rf"{num}\s*KT[^₹Rs]*?(?:₹|Rs\.?)\s*([\d,]+)",
                html, re.I
            )
            if gold_m:
                rates[key] = int(gold_m.group(1).replace(",", ""))
                print(f"  Lalitha {key} (flex HTML): Rs{rates[key]}")
                found = True
                continue

            # Strategy 4: More flexible text pattern
            gold_m = re.search(
                rf"{num}\s*KT.*?(?:₹|Rs\.?)\s*([\d,]+)",
                page_text, re.I
            )
            if gold_m:
                rates[key] = int(gold_m.group(1).replace(",", ""))
                print(f"  Lalitha {key} (flex text): Rs{rates[key]}")
                found = True
                continue

            if not found:
                print(f"  Lalitha {key}: NOT FOUND")

        # Try multiple strategies for silver
        silver_found = False

        # Strategy 1: HTML parsing
        silver_m = re.search(
            r"Silver\s*\(\s*1g\s*\)[^₹Rs]{0,50}(?:₹|Rs\.?)\s*([\d,]+)",
            html, re.I | re.DOTALL
        )
        if silver_m:
            rates["Silver"] = int(silver_m.group(1).replace(",", ""))
            print(f"  Lalitha Silver (HTML): Rs{rates['Silver']}")
            silver_found = True

        # Strategy 2: Page text parsing
        if not silver_found:
            silver_m = re.search(
                r"Silver\s*\(\s*1g\s*\)\s*(?:₹|Rs\.?)\s*([\d,]+)",
                page_text, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  Lalitha Silver (text): Rs{rates['Silver']}")
                silver_found = True

        # Strategy 3: Flexible HTML pattern
        if not silver_found:
            silver_m = re.search(
                r"Silver[^₹Rs]*?(?:₹|Rs\.?)\s*([\d,]+)",
                html, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  Lalitha Silver (flex HTML): Rs{rates['Silver']}")
                silver_found = True

        # Strategy 4: Flexible text pattern
        if not silver_found:
            silver_m = re.search(
                r"Silver.*?(?:₹|Rs\.?)\s*([\d,]+)",
                page_text, re.I
            )
            if silver_m:
                rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                print(f"  Lalitha Silver (flex text): Rs{rates['Silver']}")
                silver_found = True

        if not silver_found:
            print("  Lalitha Silver: NOT FOUND")

        # Strategy 5: Try clicking on gold rate elements if still missing data
        if len(rates) < 3:  # If we didn't find most of the rates
            print("  Lalitha: Trying to click gold rate elements...")
            try:
                # Try to find and click on elements that might contain gold rates
                driver.execute_script("""
                    var els = document.querySelectorAll('*');
                    for (var i = 0; i < els.length; i++) {
                        var text = els[i].textContent.trim();
                        if (text.includes("Gold Rate") || text.includes("Today's Gold") || text.includes("Gold")) {
                            els[i].click();
                            break;
                        }
                    }
                """)
                time.sleep(2)

                # Re-parse after clicking
                page_text = driver.find_element(By.TAG_NAME, "body").text
                html = driver.page_source

                # Try parsing again with the new content
                for num, key in karat_map.items():
                    if key not in rates:
                        gold_m = re.search(
                            rf"{num}\s*KT.*?(?:₹|Rs\.?)\s*([\d,]+)",
                            page_text, re.I
                        )
                        if gold_m:
                            rates[key] = int(gold_m.group(1).replace(",", ""))
                            print(f"  Lalitha {key} (after click): Rs{rates[key]}")

                if "Silver" not in rates:
                    silver_m = re.search(
                        r"Silver.*?(?:₹|Rs\.?)\s*([\d,]+)",
                        page_text, re.I
                    )
                    if silver_m:
                        rates["Silver"] = int(silver_m.group(1).replace(",", ""))
                        print(f"  Lalitha Silver (after click): Rs{rates['Silver']}")

            except Exception as js_err:
                print(f"  Lalitha click attempt failed: {js_err}")

        # Fallback: Derive missing karats from 22KT if we have it
        if "22KT" in rates:
            p22 = rates["22KT"]
            if "24KT" not in rates:
                rates.setdefault("24KT", round(p22 * 24 / 22 / 10) * 10)
                print(f"  Lalitha 24KT (derived): Rs{rates['24KT']}")
            if "18KT" not in rates:
                rates.setdefault("18KT", round(p22 * 18 / 22 / 10) * 10)
                print(f"  Lalitha 18KT (derived): Rs{rates['18KT']}")
            if "14KT" not in rates:
                rates.setdefault("14KT", round(p22 * 14 / 22 / 10) * 10)
                print(f"  Lalitha 14KT (derived): Rs{rates['14KT']}")

    except Exception as e:
        print("LALITHA ERROR:", e)

    return rates


# -------------------------
# MAJORITY VOTE
# -------------------------

def majority_price(values, priority):
    values = [v for v in values if v is not None]

    if not values:
        return None

    counter     = Counter(values)
    most_common = counter.most_common()

    if most_common[0][1] > 1:
        return most_common[0][0]

    return priority


# -------------------------
# SAVE TO MYSQL — UPSERT (no duplicates, update if exists)
# -------------------------


def save_rates(final_rates):
    today = datetime.now().date()

    for karat, price in final_rates.items():
        metal = "Silver" if karat == "Silver" else "Gold"

        # Check if record already exists for today
        cursor.execute("""
            SELECT id, price FROM metal_rates
            WHERE date = %s AND metal = %s AND karat = %s
        """, (today, metal, karat))

        existing = cursor.fetchone()

        if existing:
            # Record exists — update price only if it changed
            existing_id    = existing[0]
            existing_price = existing[1]

            if existing_price != price:
                cursor.execute("""
                    UPDATE metal_rates
                    SET price = %s
                    WHERE id = %s
                """, (price, existing_id))
                print(f"  UPDATED  {metal} {karat}: Rs{existing_price} → Rs{price}")
            else:
                print(f"  UNCHANGED {metal} {karat}: Rs{price} (no update needed)")
        else:
            # No record for today — insert new row
            cursor.execute("""
                INSERT INTO metal_rates (date, metal, karat, price)
                VALUES (%s, %s, %s, %s)
            """, (today, metal, karat, price))
            print(f"  INSERTED  {metal} {karat}: Rs{price}")

    conn.commit()
    print(f"Done. Processed {len(final_rates)} rates for {today}.")


# -------------------------
# MAIN
# -------------------------

def scrape_all():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Fetching jewellery rates...")

    driver = start_browser()

    try:
        print("Scraping GRT...")
        grt = scrape_grt(driver)
        print("GRT:", grt)

        print("Scraping Thangamayil...")
        thang = scrape_thangamayil(driver)
        print("Thangamayil:", thang)

        print("Scraping Lalitha...")
        lal = scrape_lalitha(driver)
        print("Lalitha:", lal)
    finally:
        driver.quit()

    final_rates = {}

    for karat in ["24KT", "22KT", "18KT", "14KT", "Silver"]:
        prices   = [grt.get(karat), thang.get(karat), lal.get(karat)]
        priority = grt.get(karat) or thang.get(karat) or lal.get(karat)
        final    = majority_price(prices, priority)

        if final is not None:
            final_rates[karat] = final

    print("\nFinal Market Rates:", final_rates)
    save_rates(final_rates)


# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    scrape_all()  # run immediately on start

    schedule.every(6).hours.do(scrape_all)

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    finally:
        conn.close()
