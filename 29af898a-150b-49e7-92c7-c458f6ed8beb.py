import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager

URL = "https://mtc1.apyo.shop/?adlinkfly=uOEF6DLw?dc1ea5c5b87b92300d66a602c5993e28612d628da693c56c256c985afe42ff74fd2b97874fb1fb844518e344ecf4989b"  # your target link

chrome_options = Options()
chrome_options.add_argument("--disable-notifications")
chrome_options.add_argument("--start-maximized")

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def try_click_image():
    """Try clicking the first <img> on the page."""
    try:
        first_img = driver.find_element(By.TAG_NAME, "img")
        ActionChains(driver).move_to_element(first_img).click().perform()
        print("✅ Clicked first image.")
        return True
    except Exception:
        return False

def try_click_adsense():
    """Try clicking a Google AdSense ad (iframe)."""
    try:
        ad_iframe = driver.find_element(By.CSS_SELECTOR, "iframe[id^='google_ads_iframe']")
        driver.switch_to.frame(ad_iframe)
        body = driver.find_element(By.TAG_NAME, "body")
        ActionChains(driver).move_to_element(body).click().perform()
        driver.switch_to.default_content()
        print("✅ Clicked AdSense ad.")
        return True
    except Exception:
        driver.switch_to.default_content()
        return False

try:
    while True:
        driver.get(URL)
        time.sleep(3)  # wait for initial load

        clicked = False
        start_time = time.time()

        # Keep retrying until clicked or 15 seconds pass
        while not clicked and (time.time() - start_time < 15):
            clicked = try_click_image() or try_click_adsense()
            if not clicked:
                time.sleep(1)  # wait before retry

        if not clicked:
            print("⚠ Nothing found to click in this cycle.")

        # Wait 2 seconds before closing
        time.sleep(2)

        # Close new tab if opened
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

        print("🔄 Loop complete. Restarting...")

except KeyboardInterrupt:
    print("🛑 Stopped by user.")
finally:
    driver.quit()
