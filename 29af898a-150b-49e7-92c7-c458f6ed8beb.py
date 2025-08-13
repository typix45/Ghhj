import requests
from telegram.ext import Updater, MessageHandler, Filters
import logging

# Your bot token from BotFather
BOT_TOKEN = "5930894396:AAEsEaySUrh37CRf15pTZ5qUpyL02ki5oog"

logging.basicConfig(level=logging.INFO)

def get_first_redirect(url):
    try:
        response = requests.get(url, allow_redirects=False)
        if 'Location' in response.headers:
            first_redirect = response.headers['Location']
            modified_link = first_redirect.replace("apyo.shop", "yxih.shop")
            return modified_link
    except Exception as e:
        logging.error(f"Error: {e}")
    return None

def handle_message(update, context):
    text = update.message.text.strip()
    if text.startswith("http"):
        result = get_first_redirect(text)
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
