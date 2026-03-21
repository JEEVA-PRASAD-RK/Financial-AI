#Hello Jeeva

import mysql.connector
from datetime import datetime
from collections import Counter
import re

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

cursor.execute("""
CREATE TABLE IF NOT EXISTS metal_rates(
id INT AUTO_INCREMENT PRIMARY KEY,
date DATE,
metal VARCHAR(20),
karat VARCHAR(20),
price INT,
UNIQUE(date, metal, karat)
)
""")

conn.commit()


# -------------------------
# BROWSER
# -------------------------

def start_browser():
    options = Options()

    # 🔥 IMPORTANT FIXES
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    return driver


# -------------------------
# TEXT PARSER
# -------------------------

def extract_prices(text):

    rates = {}

    for karat in ["24", "22", "18", "14"]:
        match = re.search(rf"{karat}.*?₹\s?([\d,]+)", text, re.I)
        if match:
            rates[f"{karat}KT"] = int(match.group(1).replace(",", ""))

    silver = re.search(r"Silver.*?₹\s?([\d,]+)", text, re.I)
    if silver:
        rates["Silver"] = int(silver.group(1).replace(",", ""))

    return rates


# -------------------------
# HELPER
# -------------------------

def get_page_text(driver):
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    return driver.find_element(By.TAG_NAME, "body").text


# -------------------------
# SCRAPERS
# -------------------------
def scrape_grt(driver):
    driver.get("https://www.grtjewels.com")
    rates = {}

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        html = driver.page_source

        patterns = {
            "24KT": r"24\s*KT.*?₹\s?([\d,]+)",
            "22KT": r"22\s*KT.*?₹\s?([\d,]+)",
            "18KT": r"18\s*KT.*?₹\s?([\d,]+)",
            "14KT": r"14\s*KT.*?₹\s?([\d,]+)",
            "Silver": r"Silver.*?₹\s?([\d,]+)"
        }

        for k, p in patterns.items():
            match = re.search(p, html, re.I)
            if match:
                rates[k] = int(match.group(1).replace(",", ""))

    except Exception as e:
        print("GRT ERROR:", e)

    return rates


def scrape_thangamayil(driver):
    driver.get("https://www.thangamayil.com")
    text = get_page_text(driver)
    return extract_prices(text)

def scrape_lalitha(driver):
    driver.get("https://www.lalithajewellery.com")
    rates = {}

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        html = driver.page_source

        # Lalitha mainly shows 22KT + silver
        gold22 = re.search(r"Gold.*?22.*?₹\s?([\d,]+)", html, re.I)
        silver = re.search(r"Silver.*?₹\s?([\d,]+)", html, re.I)

        if gold22:
            rates["22KT"] = int(gold22.group(1).replace(",", ""))

        if silver:
            rates["Silver"] = int(silver.group(1).replace(",", ""))

    except Exception as e:
        print("LALITHA ERROR:", e)

    return rates

def majority_price(values, priority):

    values = [v for v in values if v]

    if not values:
        return None

    counter = Counter(values)
    most_common = counter.most_common()

    # ✅ Majority exists
    if most_common[0][1] > 1:
        return most_common[0][0]

    # ❗ No majority → return GRT (priority)
    return priority


# -------------------------
# SAVE TO MYSQL
# -------------------------

def save_rates(final_rates):

    today = datetime.now().date()

    for karat, price in final_rates.items():

        metal = "Gold" if karat != "Silver" else "Silver"

        cursor.execute("""
        INSERT INTO metal_rates(date, metal, karat, price)
        VALUES(%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE price=VALUES(price)
        """, (today, metal, karat, price))

    conn.commit()


# -------------------------
# MAIN
# -------------------------

def scrape_all():

    print("Fetching jewellery rates...")

    driver = start_browser()

    grt = scrape_grt(driver)
    thang = scrape_thangamayil(driver)
    lal = scrape_lalitha(driver)

    driver.quit()

    print("GRT:", grt)
    print("Thangamayil:", thang)
    print("Lalitha:", lal)

    final_rates = {}

    for karat in ["24KT", "22KT", "18KT", "14KT", "Silver"]:

        prices = [
            grt.get(karat),
            thang.get(karat),
            lal.get(karat)
        ]

        priority = grt.get(karat) or thang.get(karat) or lal.get(karat)

        final = majority_price(prices, priority)

        if final is not None:
            final_rates[karat] = final

    print("Final Market Rates:", final_rates)

    save_rates(final_rates)

    print("Saved to MySQL database.")


# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    scrape_all()
    conn.close()
