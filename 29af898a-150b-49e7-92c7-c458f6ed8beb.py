import asyncio
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from playwright.async_api import async_playwright
import logging

BOT_TOKEN = "5930894396:AAEsEaySUrh37CRf15pTZ5qUpyL02ki5oog"

logging.basicConfig(level=logging.INFO)

async def get_first_redirect(url):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            first_redirect_url = None

            def handle_request(request):
                nonlocal first_redirect_url
                if first_redirect_url is None and "adlinkfly=" in request.url:
                    first_redirect_url = request.url

            page.on("request", handle_request)

            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)  # wait for potential JS redirects

            await browser.close()

            if first_redirect_url:
                return first_redirect_url.replace("apyo.shop", "yxih.shop")
    except Exception as e:
        logging.error(f"Error: {e}")
    return None

async def handle_message(update, context):
    text = update.message.text.strip()
    if text.startswith("http"):
        result = await get_first_redirect(text)
        if result:
            await update.message.reply_text(result)
        else:
            await update.message.reply_text("Couldn't fetch first redirect link.")
    else:
        await update.message.reply_text("Send me a valid URL.")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
