import os
import re
import shutil
import subprocess
import json
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse
from telethon import TelegramClient, events, Button
from mutagen import File

api_id = '10074048'
api_hash = 'a08b1ed3365fa3b04bcf2bcbf71aff4d'
session_name = 'beatport_downloader'

beatport_track_pattern = r'^https:\/\/www\.beatport\.com\/track\/[\w\-]+\/\d+$'
beatport_album_pattern = r'^https:\/\/www\.beatport\.com\/release\/[\w\-]+\/\d+$'

state = {}
ADMIN_IDS = [616584208, 731116951, 769363217]
PAYMENT_URL = "https://ko-fi.com/zackant"
USERS_FILE = 'users.json'

SHORTXLINKS_API_KEY = "YOUR_SHORTXLINKS_API_KEY"  # put your key here

# === Helper: Upload to Gofile.io ===
def upload_to_gofile(file_path):
    try:
        # Get best server
        server_resp = requests.get("https://api.gofile.io/getServer").json()
        server = server_resp["data"]["server"]

        # Upload file
        with open(file_path, 'rb') as f:
            upload_resp = requests.post(
                f"https://{server}.gofile.io/uploadFile",
                files={"file": f}
            ).json()

        if upload_resp["status"] == "ok":
            return upload_resp["data"]["downloadPage"]
        else:
            raise Exception(upload_resp)
    except Exception as e:
        raise Exception(f"Gofile upload failed: {e}")

# === Helper: Shorten with Shortxlinks ===
def shorten_with_shortxlinks(long_url):
    try:
        resp = requests.get(
            f"https://shortxlinks.com/api?api={SHORTXLINKS_API_KEY}&url={long_url}"
        ).json()

        if resp.get("status") == "success":
            return resp["shortenedUrl"]
        else:
            raise Exception(resp)
    except Exception as e:
        raise Exception(f"Shortxlinks shortening failed: {e}")

# === User Data Functions ===
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def reset_if_needed(user):
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    if user.get("last_reset") != today_str:
        user["album_today"] = 0
        user["track_today"] = 0
        user["last_reset"] = today_str

def is_user_allowed(user_id, content_type):
    if user_id in ADMIN_IDS:
        return True
    users = load_users()
    user = users.get(str(user_id), {})
    reset_if_needed(user)
    if user.get('expiry'):
        if datetime.strptime(user['expiry'], '%Y-%m-%d') > datetime.utcnow():
            return True
    if content_type == 'album' and user.get("album_today", 0) >= 2:
        return False
    if content_type == 'track' and user.get("track_today", 0) >= 2:
        return False
    return True

def increment_download(user_id, content_type):
    if user_id in ADMIN_IDS:
        return
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {}
    user = users[uid]
    reset_if_needed(user)
    if content_type == 'album':
        user["album_today"] = user.get("album_today", 0) + 1
    elif content_type == 'track':
        user["track_today"] = user.get("track_today", 0) + 1
    save_users(users)

def remove_user(user_id):
    users = load_users()
    if str(user_id) in users:
        users.pop(str(user_id))
        save_users(users)
        return True
    return False

client = TelegramClient(session_name, api_id, api_hash)

# === START HANDLER ===
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    banner_path = 'banner.gif'
    caption = (
        "🎧 Hey DJ! 🎶\n\n"
        "Welcome to Beatport Downloader Bot – now with external hosting!\n\n"
        "📋 Commands:\n"
        "➤ /download beatport url – Start download\n"
        "➤ /myaccount – Check daily usage\n"
    )
    buttons = [
        [Button.url("💟 Support", PAYMENT_URL), Button.url("📨 Contact", "https://t.me/zackantdev")]
    ]
    if os.path.exists(banner_path):
        await client.send_file(event.chat_id, banner_path, caption=caption, buttons=buttons)
    else:
        await event.reply(caption, buttons=buttons)

@client.on(events.NewMessage(pattern='/download'))
async def download_handler(event):
    try:
        user_id = event.chat_id
        input_text = event.message.text.split(maxsplit=1)[1].strip()
        is_track = re.match(beatport_track_pattern, input_text)
        is_album = re.match(beatport_album_pattern, input_text)

        if is_track or is_album:
            content_type = 'album' if is_album else 'track'

            if not is_user_allowed(user_id, content_type):
                await event.reply(
                    "🚫 You've reached today's free download limit.\n"
                    "To unlock unlimited downloads, please support.",
                    buttons=[Button.url("💳 Pay $5", PAYMENT_URL)]
                )
                return

            state[event.chat_id] = {"url": input_text, "type": content_type}
            await event.reply("Please choose the format:", buttons=[
                [Button.inline("MP3 (320 kbps)", b"mp3"), Button.inline("FLAC (16 Bit)", b"flac")]
            ])
        else:
            await event.reply('Invalid Beatport link.')
    except Exception as e:
        await event.reply(f"An error occurred: {e}")

@client.on(events.CallbackQuery)
async def callback_query_handler(event):
    try:
        format_choice = event.data.decode('utf-8')
        url_info = state.get(event.chat_id)
        if not url_info:
            await event.edit("No URL found. Please start again using /download.")
            return

        input_text = url_info["url"]
        content_type = url_info["type"]
        await event.edit(f"You selected {format_choice.upper()}. Downloading...")

        url = urlparse(input_text)
        components = url.path.split('/')
        release_id = components[-1]

        # Run Orpheus
        os.system(f'python orpheus.py {input_text}')

        if content_type == "album":
            root_path = f'downloads/{release_id}'
            flac_files = [f for f in os.listdir(root_path) if f.lower().endswith('.flac')]
            album_path = root_path if flac_files else os.path.join(root_path, os.listdir(root_path)[0])
            files = os.listdir(album_path)

            for filename in files:
                if filename.lower().endswith('.flac'):
                    input_path = os.path.join(album_path, filename)
                    output_path = f"{input_path}.{format_choice}"
                    if format_choice == 'flac':
                        subprocess.run(['ffmpeg', '-n', '-i', input_path, output_path])
                    elif format_choice == 'mp3':
                        subprocess.run(['ffmpeg', '-n', '-i', input_path, '-b:a', '320k', output_path])

                    try:
                        gofile_link = upload_to_gofile(output_path)
                        short_link = shorten_with_shortxlinks(gofile_link)
                        await event.reply(f"✅ {filename}:\n{short_link}")
                    except Exception as e:
                        await event.reply(f"❌ Upload failed for {filename}: {e}")
                    finally:
                        os.remove(output_path)

            shutil.rmtree(root_path)
            increment_download(event.chat_id, content_type)
            del state[event.chat_id]

        else:  # track
            download_dir = f'downloads/{components[-1]}'
            filename = os.listdir(download_dir)[0]
            filepath = f'{download_dir}/{filename}'
            converted_filepath = f'{download_dir}/{filename}.{format_choice}'

            if format_choice == 'flac':
                subprocess.run(['ffmpeg', '-n', '-i', filepath, converted_filepath])
            elif format_choice == 'mp3':
                subprocess.run(['ffmpeg', '-n', '-i', filepath, '-b:a', '320k', converted_filepath])

            try:
                gofile_link = upload_to_gofile(converted_filepath)
                short_link = shorten_with_shortxlinks(gofile_link)
                await event.reply(f"✅ Your track is ready:\n{short_link}")
            except Exception as e:
                await event.reply(f"❌ Upload failed: {e}")
            finally:
                os.remove(converted_filepath)
                shutil.rmtree(download_dir)

            increment_download(event.chat_id, content_type)
            del state[event.chat_id]

    except Exception as e:
        await event.reply(f"An error occurred during processing: {e}")

async def main():
    async with client:
        print("Client is running...")
        await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
