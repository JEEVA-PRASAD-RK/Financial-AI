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


# -------------------------
# MYSQL CONNECTION
# -------------------------

conn = mysql.connector.connect(
    host="localhost",
    user="root",
    password="0000",
    database="finance_ai"
)

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

        page_text = driver.find_element(By.TAG_NAME, "body").text
        html      = driver.page_source

        karat_map = {"24": "24KT", "22": "22KT", "18": "18KT", "14": "14KT"}
        for num, key in karat_map.items():
            m = re.search(
                rf"GOLD\s*{num}\s*KT\s*/\s*1g\s*[-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text, re.I
            )
            if not m:
                m = re.search(
                    rf"GOLD[^<]{{0,10}}{num}\s*KT[^<]{{0,30}}(?:Rs\.?|₹)\s*([\d,]+)",
                    html, re.I
                )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  GRT {key}: Rs{rates[key]}")

        silver_m = re.search(
            r"SILVER\s*1g\s*[-\u2013]\s*(?:Rs\.?|₹)\s*([\d,]+)",
            page_text, re.I
        )
        if not silver_m:
            silver_m = re.search(r"SILVER[^<]{0,20}(?:Rs\.?|₹)\s*([\d,]+)", html, re.I)
        if silver_m:
            rates["Silver"] = int(silver_m.group(1).replace(",", ""))
            print(f"  GRT Silver: Rs{rates['Silver']}")

    except Exception as e:
        print("GRT ERROR:", e)

    return rates


def scrape_thangamayil(driver):
    """
    Thangamayil red header bar:
      GOLD RATE 22k (1gm): Rs13,835
      GOLD RATE 24k (1gm): Rs15,093
      GOLD RATE 18k (1gm): Rs11,320
      SILVER RATE (1gm):   Rs260
    """
    driver.get("https://www.thangamayil.com")
    rates = {}

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        page_text = driver.find_element(By.TAG_NAME, "body").text

        karat_map = {"22": "22KT", "24": "24KT", "18": "18KT", "14": "14KT"}
        for num, key in karat_map.items():
            m = re.search(
                rf"GOLD\s*RATE\s*{num}[kK]\s*\(1gm\)\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)",
                page_text
            )
            if m:
                rates[key] = int(m.group(1).replace(",", ""))
                print(f"  Thangamayil {key}: Rs{rates[key]}")

        silver_m = re.search(
            r"SILVER\s*RATE\s*\(1gm\)\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)",
            page_text
        )
        if silver_m:
            rates["Silver"] = int(silver_m.group(1).replace(",", ""))
            print(f"  Thangamayil Silver: Rs{rates['Silver']}")

    except Exception as e:
        print("THANGAMAYIL ERROR:", e)

    return rates


def scrape_lalitha(driver):
    """
    Lalitha modal is already in the DOM on page load:
      Gold (22KT / 1g)   Rs13,835
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

        # Strategy 1: parse from HTML source (modal in DOM even if hidden)
        gold_m = re.search(
            r"Gold\s*\(\s*22\s*KT\s*/\s*1g\s*\)[^₹Rs]{0,50}(?:₹|Rs\.?)\s*([\d,]+)",
            html, re.I | re.DOTALL
        )
        silver_m = re.search(
            r"Silver\s*\(\s*1g\s*\)[^₹Rs]{0,50}(?:₹|Rs\.?)\s*([\d,]+)",
            html, re.I | re.DOTALL
        )

        if gold_m:
            rates["22KT"] = int(gold_m.group(1).replace(",", ""))
            print(f"  Lalitha 22KT (HTML): Rs{rates['22KT']}")
        if silver_m:
            rates["Silver"] = int(silver_m.group(1).replace(",", ""))
            print(f"  Lalitha Silver (HTML): Rs{rates['Silver']}")

        # Strategy 2: visible body text fallback
        if not rates:
            g = re.search(r"Gold\s*\(\s*22KT\s*/\s*1g\s*\)\s*(?:₹|Rs\.?)\s*([\d,]+)", page_text, re.I)
            s = re.search(r"Silver\s*\(\s*1g\s*\)\s*(?:₹|Rs\.?)\s*([\d,]+)", page_text, re.I)
            if g:
                rates["22KT"] = int(g.group(1).replace(",", ""))
            if s:
                rates["Silver"] = int(s.group(1).replace(",", ""))

        # Strategy 3: JS click fallback
        if not rates:
            print("  Lalitha: Trying JS click...")
            try:
                driver.execute_script("""
                    var els = document.querySelectorAll('*');
                    for (var i = 0; i < els.length; i++) {
                        if (els[i].textContent.trim().includes("Today's Gold Rate")) {
                            els[i].click(); break;
                        }
                    }
                """)
                time.sleep(2)
                page_text = driver.find_element(By.TAG_NAME, "body").text
                g = re.search(r"Gold.*?22.*?(?:₹|Rs\.?)\s*([\d,]+)", page_text, re.I)
                s = re.search(r"Silver.*?(?:₹|Rs\.?)\s*([\d,]+)", page_text, re.I)
                if g:
                    rates["22KT"] = int(g.group(1).replace(",", ""))
                if s:
                    rates["Silver"] = int(s.group(1).replace(",", ""))
            except Exception as js_err:
                print(f"  Lalitha JS click failed: {js_err}")

        # Derive other karats from 22KT
        if "22KT" in rates:
            p22 = rates["22KT"]
            rates.setdefault("24KT", round(p22 * 24 / 22 / 10) * 10)
            rates.setdefault("18KT", round(p22 * 18 / 22 / 10) * 10)
            rates.setdefault("14KT", round(p22 * 14 / 22 / 10) * 10)

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
    