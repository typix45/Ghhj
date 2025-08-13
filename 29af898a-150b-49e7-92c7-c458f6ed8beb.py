import asyncio
from telegram.ext import Updater, MessageHandler, Filters
from playwright.async_api import async_playwright
import logging

BOT_TOKEN = "5930894396:AAEsEaySUrh37CRf15pTZ5qUpyL02ki5oog"

logging.basicConfig(level=logging.INFO)

async def get_first_redirect(url):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Intercept and capture first navigation request
            first_redirect_url = None

            def handle_request(request):
                nonlocal first_redirect_url
                if first_redirect_url is None and "adlinkfly=" in request.url:
                    first_redirect_url = request.url

            page.on("request", handle_request)

            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)  # Give time for any JS redirects

            await browser.close()

            if first_redirect_url:
                return first_redirect_url.replace("apyo.shop", "yxih.shop")
    except Exception as e:
        logging.error(f"Error: {e}")
    return None

def handle_message(update, context):
    text = update.message.text.strip()
    if text.startswith("http"):
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(get_first_redirect(text))
        if result:
            update.message.reply_text(result)
        else:
            update.message.reply_text("Couldn't fetch first redirect link.")
    else:
        update.message.reply_text("Send me a valid URL.")

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
