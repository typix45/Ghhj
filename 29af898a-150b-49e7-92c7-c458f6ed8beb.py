import requests
import time

url = input("Enter the URL: ").strip()
delay = 5  # seconds

try:
    while True:
        r = requests.get(url)
        print(f"Visited {url} | Status: {r.status_code} | Size: {len(r.content)} bytes")
        time.sleep(delay)
except KeyboardInterrupt:
    print("\nStopped.")
