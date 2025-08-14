"""
Telegram -> PDF -> TIDAL playlist importer (improved matching)

Features:
- Accepts PDF uploads in Telegram
- Extracts lines and attempts to parse "Album" and "Artist" from each line
- Uses album+artist search first on TIDAL, falls back to album-only search
- Uses fuzzy matching (rapidfuzz) to choose the best result
- Creates a playlist and adds tracks in batches
- Persists TIDAL OAuth session to disk so you only log in once

Requirements:
pip install python-telegram-bot==20.3 pdfplumber tidalapi rapidfuzz
(If you prefer a newer python-telegram-bot version, adapt accordingly.)

Notes:
- Do NOT put your TIDAL credentials in chat. The script will open a browser for OAuth on first run.
- Replace TELEGRAM_BOT_TOKEN below with your BotFather token.
"""

import os
import re
import logging
import tempfile
from typing import List, Tuple, Optional

import pdfplumber
import tidalapi
from rapidfuzz import fuzz, process

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- CONFIG ----------
TELEGRAM_BOT_TOKEN = "5930894396:AAEsEaySUrh37CRf15pTZ5qUpyL02ki5oog"  # <-- replace this
TIDAL_SESSION_FILE = "tidal_session.json"
PLAYLIST_TITLE = "Imported from PDF"
PLAYLIST_DESCRIPTION = "Playlist created by Telegram -> PDF importer"
TRACK_ADD_BATCH = 50  # add tracks in batches to avoid rate issues
FUZZ_THRESHOLD = 70  # minimum fuzzy score to accept a match (0-100)
MAX_SEARCH_RESULTS = 8
# ----------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------- TIDAL LOGIN ----------
def tidal_login() -> tidalapi.Session:
    """
    Login to TIDAL (OAuth). Saves session to TIDAL_SESSION_FILE.
    """
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

    # If we reach here, perform interactive oauth login once
    logger.info("No saved TIDAL session found — starting OAuth login (browser will open).")
    session.login_oauth_simple()  # opens browser and guides login
    # save session id string
    try:
        with open(TIDAL_SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(session.session_id)
        logger.info("Saved TIDAL session to file.")
    except Exception as e:
        logger.warning("Unable to save tidal session: %s", e)
    return session


# Initialize tidal session globally (login when script starts)
tidal_session = None


# ---------- PDF parsing ----------
def extract_text_lines_from_pdf(pdf_path: str) -> List[str]:
    """
    Extracts lines of text from PDF, ignoring obvious URLs.
    Returns cleaned, non-empty lines in original order.
    """
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for raw in text.splitlines():
                s = raw.strip()
                if not s:
                    continue
                # skip lines that are clearly URLs (spotify links etc.)
                if re.search(r"https?://", s, flags=re.IGNORECASE):
                    continue
                # sometimes lines have UI junk; skip very short single characters
                if len(s) <= 1:
                    continue
                lines.append(s)
    # deduplicate while preserving order
    seen = set()
    result = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            result.append(l)
    return result


def parse_album_and_artist(line: str) -> Tuple[str, Optional[str]]:
    """
    Try to split a line into (album_title, artist) heuristically.
    Common patterns in your example PDF:
      "See You Again\n&ME, Rampa, Adam Port"  -> sometimes album and artist in separate lines
      "Lux & Layer EP\nAdrian Lux, Layer J"    -> sometimes album then artists on next lines
      "LONELY\nAlex LeMirage"                 -> album then artist on next line
      Other lines might be "Album - Artist" or "Artist — Album" etc.
    We attempt a few heuristics:
    1) If the line contains " - " or " — " treat as "Album - Artist" or "Artist - Album" (try to guess)
    2) If the line contains comma-separated names and many caps, treat as artists (return artist as part)
    3) Otherwise return the whole line as album and None for artist (we'll still search)
    """
    # If contains a separator like " - " or " – " or "—"
    if " - " in line or " — " in line or " – " in line:
        # prefer left as album (common in lists), but we'll decide via heuristics:
        parts = re.split(r"\s[-—–]\s", line)
        if len(parts) >= 2:
            left, right = parts[0].strip(), parts[1].strip()
            # Heuristic: if right contains a comma or many capitalized words -> it's likely artist
            if "," in right or len(right.split()) <= 3:
                return left, right
            else:
                return right, left

    # If line has a comma and several capitalized tokens, assume "Artist1, Artist2" or "Album, Artist"
    if "," in line:
        # if the left-most token has lowercase or is long, treat as album
        tokens = [t.strip() for t in line.split(",")]
        # if first token has more than 4 words -> probably album
        if len(tokens[0].split()) > 4:
            return tokens[0], ", ".join(tokens[1:])
        # many lists in your PDF use "Album" on line N and "Artist1, Artist2" on next line.
        # So when in doubt, treat the whole line as album (artist = None) and let fallback searches work.
        # But if tokens look like people names (one or two words each), treat as artist.
        person_like = sum(1 for t in tokens if 1 <= len(t.split()) <= 3)
        if person_like >= 1:
            # If the first token is short (1-3 words) and contains capitalization, treat it as artist
            if 1 <= len(tokens[0].split()) <= 3 and tokens[0].istitle():
                return "", line  # empty album, artist-only line -> caller can combine with previous
    # Default: return line as album title, None for artist
    return line, None


# ---------- TIDAL search & matching ----------
def best_tidal_album_match(session: tidalapi.Session, query_title: str, query_artist: Optional[str]) -> Optional[dict]:
    """
    Search TIDAL and return the best-matching album object (dictionary-like), or None.
    Uses fuzzy matching on title and artist.
    """
    # Prepare search queries (try album + artist first where possible)
    queries = []
    if query_title and query_artist:
        queries.append(f"{query_title} {query_artist}")
        queries.append(f"{query_title} - {query_artist}")
    if query_title:
        queries.append(query_title)

    tried = set()
    candidates = []  # list of (score, tidal_album_obj)
    for q in queries:
        if q in tried:
            continue
        tried.add(q)
        try:
            # tidalapi's search returns something like dict with 'albums' key.
            res = session.search(q, max_results=MAX_SEARCH_RESULTS)
        except Exception as e:
            logger.warning("TIDAL search error for '%s': %s", q, e)
            continue

        albums = []
        # tidalapi returns different shapes; try to extract album entries robustly
        # The library often returns an object where res['albums']['items'] contains results.
        try:
            if isinstance(res, dict):
                # common layout
                albums = res.get("albums") or res.get("albums", {}).get("items", [])
                # if nested structure like {'albums': {'items': [..]}}:
                if isinstance(albums, dict) and "items" in albums:
                    albums = albums["items"]
            else:
                # sometimes the library returns object with .albums or .albums.items()
                if hasattr(res, "albums"):
                    albums = getattr(res, "albums")
        except Exception:
            albums = []

        # Normalize: if albums is an object with .items() etc., attempt to convert to list
        if hasattr(albums, "items") and not isinstance(albums, list):
            try:
                albums = list(albums.items())
            except Exception:
                pass

        # iterate albums and compute fuzzy score
        for a in albums:
            # Extract searchable strings
            # The album object might be a dict with 'title' and 'artist' or an album model instance.
            try:
                if isinstance(a, dict):
                    title = a.get("title") or a.get("name") or ""
                    artists_field = a.get("artist") or a.get("artists") or ""
                    if isinstance(artists_field, list):
                        artist_name = ", ".join([x.get("name", "") if isinstance(x, dict) else str(x) for x in artists_field])
                    elif isinstance(artists_field, dict):
                        artist_name = artists_field.get("name", "")
                    else:
                        artist_name = str(artists_field)
                else:
                    # album model instance -> try attributes
                    title = getattr(a, "title", "") or getattr(a, "name", "")
                    # album artists may be accessible via .artist or .artists
                    artist_name = ""
                    if hasattr(a, "artist"):
                        artist_name = getattr(a, "artist") or ""
                    elif hasattr(a, "artists"):
                        try:
                            artist_name = ", ".join([str(x) for x in getattr(a, "artists")])
                        except Exception:
                            artist_name = str(getattr(a, "artists"))
                title = (title or "").strip()
                artist_name = (artist_name or "").strip()
            except Exception:
                title = ""
                artist_name = ""

            if not title:
                continue

            # Score calculation:
            # - title_score: fuzzy ratio between query_title and title
            # - artist_score: fuzzy ratio between query_artist and artist_name (if available)
            title_score = fuzz.token_set_ratio(query_title, title) if query_title else 0
            artist_score = fuzz.token_set_ratio(query_artist, artist_name) if query_artist and artist_name else 0

            # Weighted combined score (title more important)
            combined = int(title_score * 0.7 + artist_score * 0.3)

            # Keep candidate as tuple
            candidates.append((combined, title_score, artist_score, a))

    # Choose best candidate above threshold
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    best = candidates[0]
    logger.info("Best match combined=%s title_score=%s artist_score=%s", best[0], best[1], best[2])
    if best[0] >= FUZZ_THRESHOLD:
        return best[3]
    return None


def get_album_tracks(session: tidalapi.Session, album_obj) -> List[int]:
    """
    Given a tidal album object (dict or model), return a list of track IDs.
    """
    tracks = []
    try:
        # If it's a dict with 'id' available:
        if isinstance(album_obj, dict):
            album_id = album_obj.get("id")
            if album_id:
                alb = tidalapi.Album(session, album_id)
                for t in alb.tracks():
                    tracks.append(t.id if hasattr(t, "id") else int(t.get("id")))
        else:
            # album model with .id or .tracks()
            if hasattr(album_obj, "tracks"):
                for t in album_obj.tracks():
                    tracks.append(t.id)
            elif hasattr(album_obj, "id"):
                alb = tidalapi.Album(session, album_obj.id)
                for t in alb.tracks():
                    tracks.append(t.id)
    except Exception as e:
        logger.warning("Failed to fetch tracks for album: %s", e)
    return tracks


# ---------- Telegram handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a PDF file that contains album titles (optionally with artist names). "
        "I'll try to match them on your TIDAL account and create a playlist for you."
    )


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global tidal_session
    if tidal_session is None:
        await update.message.reply_text("Initializing TIDAL session... please wait (you may need to auth in a browser).")
        tidal_session = tidal_login()

    # download the PDF sent
    doc = update.message.document
    if not doc:
        await update.message.reply_text("Please send a PDF file (as a document).")
        return

    # Accept only PDFs
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please send a PDF file (file name must end with .pdf).")
        return

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    try:
        file = await doc.get_file()
        await file.download_to_drive(custom_path=tmp_path)
    except Exception as e:
        await update.message.reply_text(f"Failed to download PDF: {e}")
        return

    await update.message.reply_text("Extracting text from PDF...")
    lines = extract_text_lines_from_pdf(tmp_path)
    if not lines:
        await update.message.reply_text("Couldn't find any usable text lines in the PDF.")
        os.unlink(tmp_path)
        return

    # Heuristic: many PDFs have album on one line and artists on the next line.
    # We'll iterate through lines and attempt to pair album+artist.
    album_artist_pairs = []
    i = 0
    while i < len(lines):
        l = lines[i]
        album, artist = parse_album_and_artist(l)
        # If parse returned empty album but artist-only line (as signaled by returning "", artist)
        if album == "" and artist:
            # combine with previous if exists
            if album_artist_pairs:
                prev_album, _ = album_artist_pairs[-1]
                album_artist_pairs[-1] = (prev_album, artist)
            else:
                # no previous album; treat as artist-only (skip)
                pass
            i += 1
            continue

        # If artist is None, maybe next line is an artist line
        if artist is None and (i + 1) < len(lines):
            # lookahead, if next line looks like an artist (contains commas or few words and TitleCase),
            # attach it as artist
            candidate_next = lines[i + 1]
            # basic checks for artist-like:
            if ("," in candidate_next) or (1 <= len(candidate_next.split()) <= 4 and any(w.istitle() for w in candidate_next.split())):
                album_artist_pairs.append((album, candidate_next))
                i += 2
                continue

        album_artist_pairs.append((album, artist))
        i += 1

    # As a fallback, if parsing produced pairs that look like pure artists (album None or empty), filter them out
    filtered_pairs = [(a.strip(), (b.strip() if b else None)) for a, b in album_artist_pairs if (a and a.strip())]

    if not filtered_pairs:
        await update.message.reply_text("Couldn't parse album names from the PDF. You may need to send a cleaner PDF or one with each album on its own line.")
        os.unlink(tmp_path)
        return

    # Show the extracted album list (first up to 15) for user awareness
    preview = "\n".join([f"{idx+1}. {a} — {b}" if b else f"{idx+1}. {a}" for idx, (a, b) in enumerate(filtered_pairs[:15])])
    more = f"\n...and {len(filtered_pairs)-15} more" if len(filtered_pairs) > 15 else ""
    await update.message.reply_text(f"I found {len(filtered_pairs)} candidate album lines. Preview:\n\n{preview}{more}\n\nStarting TIDAL matching now...")

    # Create playlist
    try:
        # Create playlist under the currently logged-in user
        user = tidal_session.user
        playlist = tidal_session.user.create_playlist(title=PLAYLIST_TITLE, description=PLAYLIST_DESCRIPTION)
        playlist_id = playlist.id if hasattr(playlist, "id") else playlist.get("id")
        await update.message.reply_text(f"Created playlist '{PLAYLIST_TITLE}'. I'll add matched album tracks now.")
    except Exception as e:
        logger.exception("Failed to create playlist: %s", e)
        await update.message.reply_text(f"Failed to create playlist on TIDAL: {e}")
        os.unlink(tmp_path)
        return

    total_added = 0
    unmatched = []

    for idx, (album_title, album_artist) in enumerate(filtered_pairs, start=1):
        try:
            # Search & find best album match
            best = best_tidal_album_match(tidal_session, album_title, album_artist)
            if not best:
                # try again with album_title only (already done inside, but try explicit)
                best = best_tidal_album_match(tidal_session, album_title, None)

            if not best:
                unmatched.append((album_title, album_artist))
                logger.info("No good TIDAL match for: %s — %s", album_title, album_artist)
                # optional: notify every N
                if idx % 10 == 0:
                    await update.message.reply_text(f"Processed {idx}/{len(filtered_pairs)} — {len(unmatched)} unmatched so far.")
                continue

            # get track ids for this album
            track_ids = get_album_tracks(tidal_session, best)
            if not track_ids:
                unmatched.append((album_title, album_artist))
                continue

            # add tracks in batches
            for start in range(0, len(track_ids), TRACK_ADD_BATCH):
                batch = track_ids[start:start + TRACK_ADD_BATCH]
                try:
                    tidal_session.playlist_add(playlist_id, batch)
                except Exception as e:
                    # some versions require playlist.add or playlist.add_items; try a couple of options
                    try:
                        # fallback: create a tidalapi.Playlist instance and call add
                        pl = tidalapi.Playlist(tidal_session, playlist_id)
                        pl.add(track_ids= batch)
                    except Exception:
                        logger.warning("Failed adding batch to playlist for album %s: %s", album_title, e)
                        # continue; don't stop whole process
                total_added += len(batch)

            # small progress update every 5 albums
            if idx % 5 == 0:
                await update.message.reply_text(f"Processed {idx}/{len(filtered_pairs)} albums. Tracks added so far: {total_added}")

        except Exception as e:
            logger.exception("Error processing album '%s': %s", album_title, e)
            unmatched.append((album_title, album_artist))
            continue

    # Finalize: construct playlist URL if possible
    playlist_url = f"https://tidal.com/playlist/{playlist_id}"
    reply_msg = f"Done — added approximately {total_added} tracks.\nPlaylist: {playlist_url}"
    if unmatched:
        reply_msg += f"\n\nCouldn't match {len(unmatched)} albums (first 6 shown):\n" + "\n".join(
            [f"- {a} — {b}" if b else f"- {a}" for a, b in unmatched[:6]]
        )
    await update.message.reply_text(reply_msg)

    # cleanup
    try:
        os.unlink(tmp_path)
    except Exception:
        pass


# ---------- Main ----------
def main():
    global tidal_session
    # Pre-login attempt (non-blocking for telegram handlers that will also check)
    try:
        tidal_session = tidal_login()
    except Exception:
        logger.info("TIDAL pre-login failed; will prompt on first PDF upload.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # Accept document uploads with pdf extension
    app.add_handler(MessageHandler(filters.Document.FileExtension("pdf"), handle_pdf))
    # fallback help for other messages
    async def fallback_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Send a PDF (as a document) containing album titles. Use /start for instructions.")
    app.add_handler(MessageHandler(filters.ALL, fallback_msg))

    logger.info("Bot starting polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
