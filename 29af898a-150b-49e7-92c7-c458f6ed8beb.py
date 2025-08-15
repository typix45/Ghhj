import requests
import time

url = input("Enter the URL: ").strip()
if not url.startswith("http"):
    url = "https://" + url

delay = 5  # seconds between refreshes
count = 0

try:
    while True:
        r = requests.get(url)
        count += 1
        print(f"Refresh #{count} | Status: {r.status_code} | {len(r.content)} bytes")
        time.sleep(delay)
except KeyboardInterrupt:
    print("\nStopped by user.")
