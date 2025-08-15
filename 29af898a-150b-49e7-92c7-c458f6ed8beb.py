from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

url = input("Enter the URL: ").strip()

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
driver.get(url)

delay = 5  # seconds

try:
    while True:
        time.sleep(delay)
        driver.refresh()
        print(f"Refreshed {url}")
except KeyboardInterrupt:
    driver.quit()
    print("\nStopped by user.")
