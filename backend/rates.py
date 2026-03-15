import mysql.connector
from datetime import datetime
from collections import Counter
import time
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
price INT
)
""")

conn.commit()


# -------------------------
# BROWSER
# -------------------------

def start_browser():

    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
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

    patterns = {
        "24KT": r"24\s?KT.*?₹\s?([\d,]+)",
        "22KT": r"22\s?KT.*?₹\s?([\d,]+)",
        "18KT": r"18\s?KT.*?₹\s?([\d,]+)",
        "14KT": r"14\s?KT.*?₹\s?([\d,]+)",
        "Silver": r"SILVER.*?₹\s?([\d,]+)"
    }

    for karat, pattern in patterns.items():

        match = re.search(pattern, text, re.I)

        if match:
            rates[karat] = int(match.group(1).replace(",", ""))

    return rates


# -------------------------
# GRT SCRAPER
# -------------------------

def scrape_grt(driver):

    driver.get("https://www.grtjewels.com")

    time.sleep(5)

    text = driver.find_element(By.TAG_NAME, "body").text

    return extract_prices(text)


# -------------------------
# THANGAMAYIL SCRAPER
# -------------------------

def scrape_thangamayil(driver):

    driver.get("https://www.thangamayil.com")

    time.sleep(5)

    text = driver.find_element(By.TAG_NAME, "body").text

    return extract_prices(text)


# -------------------------
# LALITHA SCRAPER
# -------------------------

def scrape_lalitha(driver):

    driver.get("https://www.lalithajewellery.com")

    time.sleep(5)

    text = driver.find_element(By.TAG_NAME, "body").text

    return extract_prices(text)


# -------------------------
# MAJORITY PRICE
# -------------------------

def majority_price(values, priority):

    values = [v for v in values if v]

    if not values:
        return priority

    counter = Counter(values)

    most = counter.most_common()

    if most[0][1] > 1:
        return most[0][0]

    return priority


# -------------------------
# SAVE TO MYSQL
# -------------------------

def save_rates(final_rates):

    today = datetime.now().date()

    for karat, price in final_rates.items():

        metal = "Gold" if karat != "Silver" else "Silver"

        cursor.execute("""
        INSERT INTO metal_rates(date,metal,karat,price)
        VALUES(%s,%s,%s,%s)
        """,(today, metal, karat, price))

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

    for karat in ["24KT","22KT","18KT","14KT","Silver"]:

        prices = [
            grt.get(karat),
            thang.get(karat),
            lal.get(karat)
        ]

        final = majority_price(prices, grt.get(karat))

        if final:
            final_rates[karat] = final

    print("Final Market Rates:", final_rates)

    save_rates(final_rates)

    print("Saved to MySQL database.")


# -------------------------
# RUN
# -------------------------

scrape_all()

conn.close()