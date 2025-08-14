"""
Telegram -> PDF -> TIDAL playlist importer (patched & improved pairing)
"""

import os
import re
import logging
import tempfile
from typing import List, Tuple, Optional

import pdfplumber
import tidalapi
from rapidfuzz import fuzz

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- CONFIG ----------
TELEGRAM_BOT_TOKEN = "5930894396:AAEsEaySUrh37CRf15pTZ5qUpyL02ki5oog"  # <-- replace
TIDAL_SESSION_FILE = "tidal_session.json"
PLAYLIST_TITLE = "Imported from PDF"
PLAYLIST_DESCRIPTION = "Playlist created by Telegram -> PDF importer"
TRACK_ADD_BATCH = 50
FUZZ_THRESHOLD = 70
MAX_SEARCH_RESULTS = 8
# ----------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- TIDAL LOGIN ----------
def tidal_login() -> tidalapi.Session:
    session = tidalapi.Session()
    if os.path.exists(TIDAL_SESSION_FILE):
        try:
            with open(TIDAL_SESSION_FILE, "r", encoding="utf-8") as f:
                data = f.read().strip()
            session.load_oauth_session(data)
            logger.info("Loaded TIDAL session from file.")
            return session
        except Exception as e:
            logger.warning("Failed to load existing TIDAL session: %s", e)

    logger.info("No saved TIDAL session found — starting OAuth login.")
    session.login_oauth_simple()
    with open(TIDAL_SESSION_FILE, "w", encoding="utf-8") as f:
        f.write(session.session_id)
    logger.info("Saved TIDAL session to file.")
    return session

tidal_session = None

# ---------- PDF parsing ----------
def extract_text_lines_from_pdf(pdf_path: str) -> List[str]:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for raw in text.splitlines():
                s = raw.strip()
                if not s or re.search(r"https?://", s):
                    continue
                if len(s) <= 1:
                    continue
                lines.append(s)
    seen = set()
    result = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            result.append(l)
    return result

def parse_album_artist_pairs(lines: List[str]) -> List[Tuple[str, Optional[str]]]:
    """
    Pair album + artist lines:
    If a line has no artist but next line looks like an artist list, merge them.
    """
    pairs = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Try split by dash if both present on one line
        if " - " in line or " — " in line or " – " in line:
            parts = re.split(r"\s[-—–]\s", line)
            if len(parts) >= 2:
                pairs.append((parts[0].strip(), parts[1].strip()))
                i += 1
                continue
        # Look ahead for artist-like next line
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            if ("," in next_line) or (1 <= len(next_line.split()) <= 4 and any(w[0].isupper() for w in next_line.split())):
                pairs.append((line, next_line))
                i += 2
                continue
        # Default: album only
        pairs.append((line, None))
        i += 1
    return pairs

# ---------- TIDAL search ----------
def best_tidal_album_match(session: tidalapi.Session, query_title: str, query_artist: Optional[str]):
    queries = []
    if query_title and query_artist:
        queries.append(f"{query_title} {query_artist}")
        queries.append(f"{query_title} - {query_artist}")
    if query_title:
        queries.append(query_title)

    candidates = []
    for q in queries:
        try:
            res = session.search(q)  # fixed: removed max_results
        except Exception as e:
            logger.warning("TIDAL search error for '%s': %s", q, e)
            continue

        albums = []
        try:
            if isinstance(res, dict):
                albums = res.get("albums", [])
                if hasattr(albums, "items"):
                    albums = list(albums.items())
            elif hasattr(res, "albums"):
                albums = res.albums
        except Exception:
            pass

        albums = albums[:MAX_SEARCH_RESULTS]

        for a in albums:
            try:
                title = getattr(a, "title", "") or getattr(a, "name", "")
                artist_name = ""
                if hasattr(a, "artist"):
                    artist_name = getattr(a, "artist") or ""
                elif hasattr(a, "artists"):
                    artist_name = ", ".join([str(x) for x in getattr(a, "artists")])
            except Exception:
                title, artist_name = "", ""

            if not title:
                continue

            title_score = fuzz.token_set_ratio(query_title, title) if query_title else 0
            artist_score = fuzz.token_set_ratio(query_artist, artist_name) if query_artist else 0
            combined = int(title_score * 0.7 + artist_score * 0.3)

            candidates.append((combined, title_score, artist_score, a))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    best = candidates[0]
    if best[0] >= FUZZ_THRESHOLD:
        return best[3]
    return None

def get_album_tracks(session: tidalapi.Session, album_obj) -> List[int]:
    tracks = []
    try:
        if hasattr(album_obj, "tracks"):
            for t in album_obj.tracks():
                tracks.append(t.id)
    except Exception as e:
        logger.warning("Failed to fetch tracks for album: %s", e)
    return tracks

# ---------- Telegram handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me a PDF with album titles. I’ll match them in TIDAL and make a playlist.")

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global tidal_session
    if tidal_session is None:
        await update.message.reply_text("Logging in to TIDAL...")
        tidal_session = tidal_login()

    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please send a PDF file.")
        return

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    await doc.get_file().download_to_drive(tmp.name)

    lines = extract_text_lines_from_pdf(tmp.name)
    if not lines:
        await update.message.reply_text("No album names found in PDF.")
        os.unlink(tmp.name)
        return

    pairs = parse_album_artist_pairs(lines)

    playlist = tidal_session.user.create_playlist(PLAYLIST_TITLE, PLAYLIST_DESCRIPTION)
    total_added = 0
    unmatched = []

    for album_title, album_artist in pairs:
        best = best_tidal_album_match(tidal_session, album_title, album_artist)
        if not best:
            unmatched.append((album_title, album_artist))
            continue
        track_ids = get_album_tracks(tidal_session, best)
        for start in range(0, len(track_ids), TRACK_ADD_BATCH):
            tidal_session.playlist_add(playlist.id, track_ids[start:start+TRACK_ADD_BATCH])
        total_added += len(track_ids)

    playlist_url = f"https://tidal.com/playlist/{playlist.id}"
    msg = f"Playlist created: {playlist_url}\nTracks added: {total_added}"
    if unmatched:
        msg += f"\nUnmatched: {len(unmatched)}"
    await update.message.reply_text(msg)

    os.unlink(tmp.name)

# ---------- Main ----------
def main():
    global tidal_session
    try:
        tidal_session = tidal_login()
    except Exception:
        logger.info("TIDAL login will run on first PDF upload.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.FileExtension("pdf"), handle_pdf))
    app.run_polling()

if __name__ == "__main__":
    main()
