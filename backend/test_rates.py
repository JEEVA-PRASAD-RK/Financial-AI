#!/usr/bin/env python3
"""
Test script to verify metal rate scraping functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rates import scrape_grt, scrape_thangamayil, scrape_lalitha, start_browser
import time

def test_scrapers():
    print("🧪 Testing Metal Rate Scrapers...")
    print("=" * 50)

    driver = start_browser()

    try:
        # Test GRT
        print("\n🏢 Testing GRT...")
        grt_rates = scrape_grt(driver)
        print(f"GRT Results: {grt_rates}")

        # Test Thangamayil
        print("\n🏢 Testing Thangamayil...")
        thang_rates = scrape_thangamayil(driver)
        print(f"Thangamayil Results: {thang_rates}")

        # Test Lalitha
        print("\n🏢 Testing Lalitha...")
        lal_rates = scrape_lalitha(driver)
        print(f"Lalitha Results: {lal_rates}")

        # Summary
        print("\n" + "=" * 50)
        print("📊 SUMMARY:")
        print(f"✅ GRT found {len(grt_rates)} rates")
        print(f"✅ Thangamayil found {len(thang_rates)} rates")
        print(f"✅ Lalitha found {len(lal_rates)} rates")

        # Check for missing karats
        all_karats = ["24KT", "22KT", "18KT", "14KT", "Silver"]

        for karat in all_karats:
            grt_has = karat in grt_rates
            thang_has = karat in thang_rates
            lal_has = karat in lal_rates

            status = "✅" if (grt_has or thang_has or lal_has) else "❌"
            sources = []
            if grt_has: sources.append("GRT")
            if thang_has: sources.append("Thangamayil")
            if lal_has: sources.append("Lalitha")

            print(f"{status} {karat}: {', '.join(sources) if sources else 'MISSING'}")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        driver.quit()
        print("\n🏁 Test completed!")

if __name__ == "__main__":
    test_scrapers()
