import csv
import os
import re
import time

import pandas as pd
import requests

INPUT_FILE = os.environ.get("INPUT_FILE", "Ausgabe Spotify.xlsx")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "songs_mit_genre.tsv")
USER_AGENT = os.environ.get("USER_AGENT", "GenreLookupStudentProject/1.0 (contact@example.com)")

HEADER = ["Künstler", "Titel", "Genre", "Quelle"]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

cache = {}


def log(msg):
    print(msg, flush=True)


def clean_text(text):
    text = str(text).replace('"', "").strip()
    return re.sub(r"\s+", " ", text)


def simplify_title(title):
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", title)
    t = re.split(r"\s+-\s+", t, maxsplit=1)[0]
    return clean_text(t)


def main_artist_name(artist):
    first = artist.split(",")[0]
    first = re.split(r"\s+(?:feat\.?|ft\.?|x|&)\s+", first, maxsplit=1, flags=re.IGNORECASE)[0]
    return clean_text(first)


def get_json(url, params=None, delay=0.25):
    time.sleep(delay)
    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=20)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code in (429, 503):
            wait = int(r.headers.get("Retry-After", 3 * (attempt + 1))) + 1
            if wait > 60:
                return None
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return None
    return None


def lookup_itunes(artist, title):
    """Apple / iTunes Search API: kostenlos, kein Key, hohe Trefferquote weltweit."""
    data = get_json(
        "https://itunes.apple.com/search",
        params={"term": f"{artist} {title}", "media": "music", "entity": "song", "limit": 3},
        delay=0.35,
    )
    if not data or not data.get("results"):
        return None

    artist_low = artist.lower()
    for item in data["results"]:
        genre = clean_text(item.get("primaryGenreName", ""))
        item_artist = clean_text(item.get("artistName", "")).lower()
        if genre and genre.lower() != "music":
            if artist_low in item_artist or item_artist in artist_low:
                return genre

    # Fallback auf ersten Treffer, falls Artist-Schreibweise leicht abweicht (z. B. Arabisch/Latein)
    first_genre = clean_text(data["results"][0].get("primaryGenreName", ""))
    if first_genre and first_genre.lower() != "music":
        return first_genre
    return None


def lookup_deezer(artist, title):
    """Deezer Search + Album/Artist Genre."""
    q = f'artist:"{artist}" track:"{title}"'
    data = get_json("https://api.deezer.com/search", params={"q": q, "limit": 1}, delay=0.25)
    if not data or not data.get("data"):
        data = get_json("https://api.deezer.com/search", params={"q": f"{artist} {title}", "limit": 1}, delay=0.25)
    if not data or not data.get("data"):
        return None

    track = data["data"][0]
    album_id = track.get("album", {}).get("id")
    if album_id:
        album_data = get_json(f"https://api.deezer.com/album/{album_id}", delay=0.2)
        if album_data and isinstance(album_data.get("genres"), dict):
            genres = [g.get("name") for g in album_data["genres"].get("data", []) if g.get("name")]
            if genres:
                return "; ".join(dict.fromkeys(genres))
    return None


def lookup_musicbrainz(artist, title):
    """MusicBrainz Recording & Artist Tags/Genres."""
    query = f'recording:"{title}" AND artist:"{artist}"'
    data = get_json(
        "https://musicbrainz.org/ws/2/recording/",
        params={"query": query, "fmt": "json", "limit": 1},
        delay=1.1,
    )
    if not data or not data.get("recordings"):
        return None

    rec = data["recordings"][0]
    tags = []
    for g in rec.get("genres", []) or []:
        if g.get("name"):
            tags.append(g["name"].lower())
    for t in rec.get("tags", []) or []:
        if t.get("name"):
            tags.append(t["name"].lower())
    for ac in rec.get("artist-credit", []) or []:
        art = ac.get("artist") if isinstance(ac, dict) else None
        if isinstance(art, dict):
            for g in art.get("genres", []) or []:
                if g.get("name"):
                    tags.append(g["name"].lower())
            for t in art.get("tags", []) or []:
                if t.get("name"):
                    tags.append(t["name"].lower())

    unique_tags = list(dict.fromkeys(tags))[:5]
    return "; ".join(unique_tags) if unique_tags else None


def lookup_genre(artist, title):
    m_artist = main_artist_name(artist)
    c_title = clean_text(title)
    s_title = simplify_title(title)
    key = (m_artist.lower(), s_title.lower())

    if key in cache:
        return cache[key]

    candidates = [c_title] if c_title == s_title else [c_title, s_title]

    for t in candidates:
        g = lookup_itunes(m_artist, t)
        if g:
            cache[key] = (g, "itunes")
            return cache[key]

    for t in candidates:
        g = lookup_deezer(m_artist, t)
        if g:
            cache[key] = (g, "deezer")
            return cache[key]

    for t in candidates:
        g = lookup_musicbrainz(m_artist, t)
        if g:
            cache[key] = (g, "musicbrainz")
            return cache[key]

    cache[key] = ("keine Angabe", "keine Angabe")
    return cache[key]


def load_songs(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Eingabedatei nicht gefunden: {path}")
    df = pd.read_excel(path, header=None, dtype=str).fillna("")
    if df.empty:
        return []
    if str(df.iloc[0, 0]).strip().lower() in ("artist_names", "artist", "künstler"):
        df = df.iloc[1:]
    songs = []
    for row in df.itertuples(index=False):
        artist, title = str(row[0]).strip(), str(row[1]).strip()
        if artist and title:
            songs.append((artist, title))
    return songs


def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.reader(f, delimiter="\t"):
                if len(row) >= 3 and row[0] != "Künstler" and row[2] != "keine Angabe":
                    done.add((row[0], row[1]))
    return done


def main():
    log(f"Starte Multi-Source Genre-Zuordnung: '{INPUT_FILE}' -> '{OUTPUT_FILE}'")
    songs = load_songs(INPUT_FILE)
    done = load_done(OUTPUT_FILE)
    file_exists = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    log(f"{len(songs)} Titel gefunden, {len(done)} bereits erfolgreich erledigt.")

    written = 0
    with open(OUTPUT_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        if not file_exists:
            writer.writerow(HEADER)
            f.flush()

        for i, (artist, title) in enumerate(songs, 1):
            if (artist, title) in done:
                continue
            genre, source = lookup_genre(artist, title)
            writer.writerow([artist, title, genre, source])
            f.flush()
            written += 1
            if written % 25 == 0 or i % 100 == 0:
                log(f"Fortschritt: {i}/{len(songs)} geprüft ({written} neu geschrieben)")

    log(f"Fertig! {written} Titel in '{OUTPUT_FILE}' gespeichert.")


if __name__ == "__main__":
    main()
