import os
import re
import shutil
import subprocess
import json
import requests
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlparse
from telethon import TelegramClient, events, Button
from mutagen import File

# API Config
api_id = '10074048'
api_hash = 'a08b1ed3365fa3b04bcf2bcbf71aff4d'
session_name = 'beatport_downloader'

GOFILE_API_KEY = "7eZiUPRZ5nHoFQLctHFm8PVNcsDTGzi9"
SHORTXLINKS_API_KEY = "1e06248a3c46dfea85a923d7131af97b52a72ce5"

beatport_track_pattern = r'^https:\/\/www\.beatport\.com\/track\/[\w\-]+\/\d+$'
beatport_album_pattern = r'^https:\/\/www\.beatport\.com\/release\/[\w\-]+\/\d+$'

state = {}
ADMIN_IDS = [616584208, 731116951, 769363217]
PAYMENT_URL = "https://ko-fi.com/zackant"
USERS_FILE = 'users.json'

# ---------------- Utility Functions ----------------
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

# -------- File Upload Functions --------
def zip_folder(folder_path, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for file in files:
                zipf.write(os.path.join(root, file),
                           os.path.relpath(os.path.join(root, file), folder_path))

def upload_to_gofile(file_path):
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                "https://api.gofile.io/uploadFile",
                headers={"Authorization": f"Bearer {GOFILE_API_KEY}"},
                files={"file": f}
            )
        res = response.json()
        if res["status"] == "ok":
            return res["data"]["downloadPage"]
        else:
            print("Gofile upload failed:", res)
            return None
    except Exception as e:
        print("Gofile upload error:", e)
        return None

def shorten_with_shortxlinks(url):
    try:
        res = requests.get(
            f"https://shortxlinks.com/api?api={SHORTXLINKS_API_KEY}&url={url}"
        )
        data = res.json()
        return data.get("shortenedUrl") or data.get("short")
    except Exception as e:
        print("Shortxlinks error:", e)
        return url

# ---------------- Telegram Bot ----------------
client = TelegramClient(session_name, api_id, api_hash)

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    caption = (
        "🎧 Welcome to Beatport Downloader Bot\n\n"
        "Paste a Beatport link and choose format.\n"
        "You'll get a single short link to download your music."
    )
    await event.reply(caption)

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
                await event.reply("🚫 Daily limit reached.")
                return
            state[event.chat_id] = {"url": input_text, "type": content_type}
            await event.reply("Choose format:", buttons=[
                [Button.inline("MP3 (320 kbps)", b"mp3"),
                 Button.inline("FLAC (16 Bit)", b"flac")]
            ])
        else:
            await event.reply("Invalid Beatport link.")
    except Exception as e:
        await event.reply(f"Error: {e}")

@client.on(events.CallbackQuery)
async def callback_query_handler(event):
    try:
        format_choice = event.data.decode()
        url_info = state.get(event.chat_id)
        if not url_info:
            await event.edit("No URL found. Start again with /download.")
            return

        input_text = url_info["url"]
        content_type = url_info["type"]
        await event.edit(f"Downloading in {format_choice.upper()}...")

        url = urlparse(input_text)
        release_id = url.path.split('/')[-1]
        os.system(f'python orpheus.py {input_text}')

        if content_type == "album":
            root_path = f'downloads/{release_id}'
            flac_files = [f for f in os.listdir(root_path) if f.lower().endswith('.flac')]
            album_path = root_path if flac_files else os.path.join(root_path, os.listdir(root_path)[0])
            files = os.listdir(album_path)

            all_artists = set()
            sample_file = next((f for f in files if f.lower().endswith('.flac')), None)
            sample_path = os.path.join(album_path, sample_file) if sample_file else None
            metadata = File(sample_path, easy=True) if sample_path else {}
            album = metadata.get('album', ['Unknown Album'])[0]
            genre = metadata.get('genre', ['Unknown Genre'])[0]
            bpm = metadata.get('bpm', ['--'])[0]
            label = metadata.get('label', ['--'])[0]
            date = metadata.get('date', ['--'])[0]
            for f in files:
                if f.lower().endswith('.flac'):
                    audio = File(os.path.join(album_path, f), easy=True)
                    if audio:
                        for key in ('artist', 'performer', 'albumartist'):
                            if key in audio:
                                all_artists.update(audio[key])
            artists_str = ", ".join(sorted(all_artists))

            # Convert all files to chosen format
            for filename in files:
                if filename.lower().endswith('.flac'):
                    input_path = os.path.join(album_path, filename)
                    output_path = os.path.splitext(input_path)[0] + f".{format_choice}"
                    if format_choice == 'mp3':
                        subprocess.run(['ffmpeg', '-y', '-i', input_path, '-b:a', '320k', output_path])
                    else:
                        subprocess.run(['ffmpeg', '-y', '-i', input_path, output_path])
                    if output_path != input_path:
                        os.remove(input_path)

            # Zip and upload
            zip_path = f"{album_path}.zip"
            zip_folder(album_path, zip_path)
            gofile_link = upload_to_gofile(zip_path)
            short_link = shorten_with_shortxlinks(gofile_link) if gofile_link else "Upload failed"

            caption = (
                f"<b>🎶 Album:</b> {album}\n"
                f"<b>🎤 Artists:</b> {artists_str}\n"
                f"<b>🎼 Genre:</b> {genre}\n"
                f"<b>🏷 Label:</b> {label}\n"
                f"<b>📅 Release Date:</b> {date}\n"
                f"<b>🎚 BPM:</b> {bpm}\n\n"
                f"⬇️ <b>Download:</b> {short_link}"
            )

            cover_file = next((os.path.join(album_path, f) for f in files if f.lower().startswith('cover')), None)
            if cover_file:
                await client.send_file(event.chat_id, cover_file, caption=caption, parse_mode='html')
            else:
                await event.reply(caption, parse_mode='html')

            shutil.rmtree(root_path)
            os.remove(zip_path)
            increment_download(event.chat_id, content_type)
            del state[event.chat_id]

        else:  # track
            track_path = f'downloads/{release_id}'
            filename = os.listdir(track_path)[0]
            input_path = os.path.join(track_path, filename)
            output_path = os.path.splitext(input_path)[0] + f".{format_choice}"
            if format_choice == 'mp3':
                subprocess.run(['ffmpeg', '-y', '-i', input_path, '-b:a', '320k', output_path])
            else:
                subprocess.run(['ffmpeg', '-y', '-i', input_path, output_path])
            if output_path != input_path:
                os.remove(input_path)

            zip_path = f"{track_path}.zip"
            zip_folder(track_path, zip_path)
            gofile_link = upload_to_gofile(zip_path)
            short_link = shorten_with_shortxlinks(gofile_link) if gofile_link else "Upload failed"

            caption = f"⬇️ <b>Download:</b> {short_link}"
            await event.reply(caption, parse_mode='html')

            shutil.rmtree(track_path)
            os.remove(zip_path)
            increment_download(event.chat_id, content_type)
            del state[event.chat_id]

    except Exception as e:
        await event.reply(f"Error: {e}")

# Run Bot
async def main():
    async with client:
        print("Bot running...")
        await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
