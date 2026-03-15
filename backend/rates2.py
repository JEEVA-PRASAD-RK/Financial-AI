import requests
import re

def scrape_goodreturns():

    url = "https://www.goodreturns.in/gold-rates/chennai.html"

    headers = {
        "User-Agent":"Mozilla/5.0"
    }

    r = requests.get(url,headers=headers)

    text = r.text

    rates = {}

    patterns = {
        "24KT": r"24 Carat Gold Rate.*?₹\s?([\d,]+)",
        "22KT": r"22 Carat Gold Rate.*?₹\s?([\d,]+)",
        "Silver": r"Silver Rate.*?₹\s?([\d,]+)"
    }

    for k,p in patterns.items():

        m = re.search(p,text,re.S)

        if m:
            rates[k] = int(m.group(1).replace(",",""))

    return rates


print(scrape_goodreturns())