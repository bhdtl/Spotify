import os
import csv
import time

import pandas as pd
import requests

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
INPUT_FILE = os.environ.get("INPUT_FILE", "Ausgabe Spotify.xlsx")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "songs_mit_genre.tsv")

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
HEADER = ["Künstler", "Titel", "Genre"]

session = requests.Session()
token = None


def get_token():
    r = session.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def api_get(path, params=None):
    """GET mit Retry, Rate-Limit-Behandlung und Token-Erneuerung."""
    global token
    if token is None:
        token = get_token()

    for attempt in range(6):
        try:
            r = session.get(
                f"{API}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue

        if r.status_code == 200:
            return r.json()
        if r.status_code == 401:  # Token abgelaufen
            token = get_token()
            continue
        if r.status_code == 429:  # Rate Limit
            time.sleep(int(r.headers.get("Retry-After", 2)) + 1)
            continue
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return None  # z. B. 403/404: nicht wiederholen
    return None


artist_cache = {}


def genres_for_artist(artist_id):
    if artist_id not in artist_cache:
        data = api_get(f"/artists/{artist_id}")
        artist_cache[artist_id] = data.get("genres", []) if data else []
    return artist_cache[artist_id]


def clean(text):
    return text.replace('"', "").strip()


def lookup_genre(artist, title):
    main_artist = clean(artist.split(",")[0])
    title_clean = clean(title)

    queries = [
        f'track:"{title_clean}" artist:"{main_artist}"',
        f"{title_clean} {main_artist}",  # Fallback ohne Feldfilter
    ]
    for q in queries:
        data = api_get("/search", {"q": q, "type": "track", "limit": 1})
        items = data["tracks"]["items"] if data else []
        if items:
            genres = []
            for a in items[0]["artists"]:
                genres += genres_for_artist(a["id"])
            genres = list(dict.fromkeys(genres))  # Duplikate entfernen
            return "; ".join(genres) if genres else "keine Angabe"
    return "unbekannt"


def load_songs(path):
    df = pd.read_excel(path, header=None, dtype=str).fillna("")
    # Kopfzeile erkennen und überspringen
    if str(df.iloc[0, 0]).strip().lower() in ("artist_names", "artist", "künstler"):
        df = df.iloc[1:]
    songs = []
    for row in df.itertuples(index=False):
        artist, title = str(row[0]).strip(), str(row[1]).strip()
        if artist and title:
            songs.append((artist, title))
    return songs


def load_done(path):
    """Bereits verarbeitete Zeilen lesen, damit ein Neustart fortsetzt."""
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.reader(f, delimiter="\t"):
                if len(row) >= 3 and row[0] != "Künstler":
                    done.add((row[0], row[1]))
    return done


def main():
    songs = load_songs(INPUT_FILE)
    done = load_done(OUTPUT_FILE)
    file_exists = os.path.exists(OUTPUT_FILE)
    print(f"{len(songs)} Titel gefunden, {len(done)} bereits erledigt.")

    with open(OUTPUT_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        if not file_exists:
            writer.writerow(HEADER)

        for i, (artist, title) in enumerate(songs, 1):
            if (artist, title) in done:
                continue
            genre = lookup_genre(artist, title)
            writer.writerow([artist, title, genre])
            f.flush()  # sofort speichern, falls der Lauf abbricht
            if i % 100 == 0:
                print(f"{i}/{len(songs)} verarbeitet")
            time.sleep(0.1)

    print("Fertig:", OUTPUT_FILE)


if __name__ == "__main__":
    main()
