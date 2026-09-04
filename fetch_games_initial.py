#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - Steam-game-catalogus via OFFICIELE Steam-bronnen
====================================================================

Twee officiele Steam-bronnen (geen derde partijen zoals SteamSpy):

  1. Appid-lijst: de officiele Steam Web API (api.steampowered.com) met je
     eigen API key -> IStoreService/GetAppList/v1, GEPAGINEERD via
     max_results + last_appid. Zonder/ongeldige key: HTTP 403. Als die
     endpoint niet beschikbaar is (HTTP 404), valt het script terug op
     ISteamApps/GetAppList/v2.
  2. Per-game data: de officiele Steam Storefront API
     (store.steampowered.com/api/appdetails) - ZONDER API key. Heeft EEN
     appid als input nodig; meerdere appids per request worden NIET
     ondersteund (herhaalde appids -> alleen de laatste in de response,
     100 appids -> HTTP 400; geverifieerd 2026-09-04). Er gaat dus 1
     request per appid; Steam throttlet per IP met HTTP 429 (backoff), dus
     grote aantallen lopen over meerdere runs.

'Nieuwe games' zijn appids die wel in de officiele app-lijst zitten maar nog
NIET in de JSON-output (data/games.jsonl). Van elke nieuwe game worden de
relevante velden opgehaald en toegevoegd. Appids die er al in staan worden
overgeslagen. Stop je het script (Ctrl+C) of is het budget op, dan pakt de
volgende run gewoon weer op waar games.jsonl gebleven was - er is GEEN apart
checkpoint-bestand; het aantal regels in games.jsonl is de waarheid.

Prijzen: elke game wordt opgevraagd met cc=us + l=english, dus price_overview
is altijd USD en de tekst (genres/categorieen) altijd Engels - er wordt niets
omgerekend, alleen bewaard wat de API teruggeeft. Bij geen korting laat
Steam initial_formatted leeg; dan wordt hij gelijkgezet aan final_formatted
(hetzelfde bedrag).

Filter: alleen type == "game" wordt opgeslagen. Apps die geen game zijn
(dlc, demo, muziek, software, ...) of geen storepagina hebben (verwijderd/
geblokkeerd) worden NIET opgeslagen maar automatisch in de blacklist gezet
(data/blacklist.json): die appids worden bij een volgende run niet meer
opgehaald. De blacklist blijft beheersbaar - een appid eruit halen (via
--blacklist-remove of het bestand) maakt hem weer 'nieuw'; zelf toevoegen
kan via --blacklist-add of handmatig in het bestand. Alleen tijdelijke
fouten (netwerk/429) worden NIET geblacklist - die probeert een volgende
run gewoon opnieuw. Een titel wordt nooit twee keer toegevoegd: komt
dezelfde naam nog eens voor (China/regio-editie, heruitgave), dan wordt die
bewaard in duplicates.jsonl met duplicate_of = het behouden (laagste) appid.

Output (in data/):
    games.jsonl       1 JSON-object per regel, alleen games (type == "game")
    blacklist.json    appids die NIET opnieuw worden geprobeerd (niet-game,
                      geen storepagina, geblokkeerd of handmatig). Zelf
                      beheerbaar: eruit halen = weer 'nieuw'
    duplicates.jsonl  dubbele titels, per appid 1x, met duplicate_of

Velden per game (slank formaat): appid, type, name, is_free, price_overview,
publishers, genres (namen), categories (namen), release_date (zoals de API
hem geeft), release_date_format (zelfde datum in yyyy-mm-dd),
recommendations_total, last_seen_player_count (momentopname van het huidige
spelersaantal via GetNumberOfCurrentPlayers, keyless; None bij geen
publieke data) en appid_amount ("<appid>_1" - elke game staat in DIT bestand
maar 1x). De TIJDLIJN (herhaald pollen) doet het zusterscript
fetch_new_game_info.py: dat nummert verder met "<appid>_2", "_3", ... in
data/player_history.jsonl.

Gebruik:
    python fetch_games_initial.py            # popup voor API key, dan verwerken
    python fetch_games_initial.py --save-key # key bewaren in data/api_key.txt
    python fetch_games_initial.py --forget-key   # bewaarde key wissen
    python fetch_games_initial.py --limit 500    # max. 500 nieuwe appids deze run
    python fetch_games_initial.py --report-only  # alleen tonen welke nieuw zijn
    python fetch_games_initial.py --reset        # games/blacklist/duplicates wissen
    python fetch_games_initial.py --blacklist-show           # blacklist tonen
    python fetch_games_initial.py --blacklist-add 100 200    # handmatig toevoegen
    python fetch_games_initial.py --blacklist-remove 100     # eruit halen -> weer 'nieuw'

    Dit is de EENMALIGE volledige doorgang: elke game komt precies 1x in
    data/games.jsonl (appid_amount "<appid>_1"). Het zusterscript
    fetch_new_game_info.py voegt daar herhaalde regels aan toe in
    data/player_history.jsonl ("<appid>_2", "_3", ...) voor een tijdlijn
    van last_seen_player_count.
"""

import argparse
import json
import os
import random
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

ISTORE_APP_LIST_URL = ("https://api.steampowered.com/IStoreService/"
                       "GetAppList/v1/")
LEGACY_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
PLAYER_COUNT_URL = ("https://api.steampowered.com/ISteamUserStats/"
                    "GetNumberOfCurrentPlayers/v1/")
STORE_BATCH = 1     # appids per storefront-request. appdetails accepteert
                    # maar EEN appid per request (geverifieerd 2026-09-04:
                    # meerdere herhaalde appids gaven alleen de laatste
                    # terug, 100 gaf HTTP 400) -> dus 1 request per appid.

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DEFAULT_MAX_REQUESTS = 10000   # veiligheidslimiet op store-requests per run
DEFAULT_DELAY = 0.4            # seconden rust tussen twee store-requests
DEFAULT_JITTER = 0.1           # extra willekeurige spreiding op de pauze
DEFAULT_TIMEOUT = 30           # timeout per HTTP-call (sec)
DEFAULT_RETRIES = 6            # pogingen per appid bij throttling/fouten

stop_requested = False


def on_signal(signum, frame):  # pragma: no cover - alleen interactief
    global stop_requested
    stop_requested = True
    print("\n> Stop aangevraagd (Ctrl+C). Voortgang wordt netjes bewaard...")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# --------------------------------------------------------------------------- #
#  API key: popup eerst, dan console, optioneel bewaren                        #
# --------------------------------------------------------------------------- #
def ask_key_popup():
    """GUI-popup (tkinter, standaard meegeleverd met Python op Windows) om de
    API key in te vullen. Retourneert None als er geen GUI beschikbaar is."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        key = simpledialog.askstring(
            "Steam Web API key",
            "Vul je Steam Web API key in\n(te verkrijgen op "
            "steamcommunity.com/dev/apikey):",
            show="*", parent=root)
        root.destroy()
        return key.strip() if key else None
    except Exception:  # noqa: BLE001 - geen GUI beschikbaar
        return None


def resolve_api_key(args, data_dir):
    """Bepaal de key: --key > bewaarde key > popup > console-invoer."""
    if args.key:
        return args.key

    keyfile = os.path.join(data_dir, "api_key.txt")
    if os.path.isfile(keyfile):
        with open(keyfile, encoding="utf-8") as f:
            saved = f.read().strip()
        if saved:
            return saved

    key = ask_key_popup()
    if key is None:                       # geen GUI of geannuleerd
        try:
            key = input("Steam Web API key: ").strip()
        except EOFError:
            key = ""
    if not key:
        print("! Geen API key opgegeven (gebruik --key of --save-key).")
        sys.exit(1)
    if args.save_key:
        with open(keyfile, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        print(f"> API key bewaard in {keyfile}")
    return key


# --------------------------------------------------------------------------- #
#  Officiele app-lijst (Web API, keyed)                                        #
# --------------------------------------------------------------------------- #
def fetch_app_list_istore(key):
    """IStoreService/GetAppList/v1 - officieel en GEPAGINEERD via
    max_results + last_appid (geeft alleen iets terug met een geldige key).
    Retourneert [{"appid":..,"name":..}, ...] tot have_more_results
    false is."""
    apps = []
    last_appid = 0
    while True:
        url = ISTORE_APP_LIST_URL + "?" + urllib.parse.urlencode({
            "key": key, "max_results": 50000, "last_appid": last_appid})
        data = http_get_json(url)
        resp = data.get("response") or {}
        batch = resp.get("apps") or []
        apps.extend(batch)
        if not resp.get("have_more_results") or not batch:
            break
        nxt = resp.get("last_appid")
        if nxt is None or nxt == last_appid:    # geen vooruitgang -> stoppen
            break
        last_appid = nxt
    return apps


def fetch_app_list_legacy(key):
    """ISteamApps/GetAppList/v2 - 1 call (was op 2026-09-04 uit: HTTP 404
    'Method not found')."""
    url = LEGACY_APP_LIST_URL + "?" + urllib.parse.urlencode(
        {"key": key, "format": "json"})
    data = http_get_json(url)
    return data["applist"]["apps"]


def fetch_app_list(key):
    """Officiele app-lijst: eerst IStoreService (gepagineerd), bij een 404
    een fallback op ISteamApps. Retourneert (apps, bronnaam)."""
    try:
        return fetch_app_list_istore(key), "IStoreService/GetAppList"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("! IStoreService/GetAppList niet beschikbaar (404) - "
                  "fallback op ISteamApps/GetAppList...")
        else:
            raise                  # 403 (key), 5xx, ... -> laat de caller melden
    return fetch_app_list_legacy(key), "ISteamApps/GetAppList"


# --------------------------------------------------------------------------- #
#  Dataset (bron van de waarheid: games.jsonl zelf)                            #
# --------------------------------------------------------------------------- #
def load_existing(path):
    """Lees de al opgeslagen games uit de JSON-output (jsonl met records of
    losse appids per regel). Retourneert (appids, titels, titel_appid):
    bekende appids, bestaande titels (lowercase) en per titel het eerste
    (laagste) appid."""
    appids, titles, title_appid = set(), set(), {}
    if not os.path.isfile(path):
        return appids, titles, title_appid
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            r = None
        if isinstance(r, dict):
            try:
                aid = int(r.get("appid"))
            except (TypeError, ValueError):
                continue
            title = (r.get("name") or "").strip().lower()
        else:                                  # los appid op de regel
            try:
                aid = int(line)
            except ValueError:
                continue
            title = ""
        appids.add(aid)
        if title:
            titles.add(title)
            title_appid.setdefault(title, aid)   # eerste (laagste) appid wint
    return appids, titles, title_appid


def load_duplicate_appids(data_dir):
    """Appids die al in duplicates.jsonl staan (per appid maar 1x bewaren)."""
    path = os.path.join(data_dir, "duplicates.jsonl")
    known = set()
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                known.add(int(json.loads(line).get("appid")))
            except (TypeError, ValueError):
                continue
    return known


def append_duplicates(data_dir, records, seen):
    """Schrijf records weg naar duplicates.jsonl (alleen nieuwe appids).
    Retourneert hoeveel er nieuw zijn toegevoegd."""
    written = 0
    if not records:
        return written
    with open(os.path.join(data_dir, "duplicates.jsonl"),
              "a", encoding="utf-8") as f:
        for r in records:
            try:
                aid = int(r.get("appid"))
            except (TypeError, ValueError):
                continue
            if aid in seen:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            seen.add(aid)
            written += 1
    return written


def load_blacklist(path):
    """Lees de blacklist: JSON-object {appid: {name, reason, added}}. De
    sleutels worden ints. Ontbrekend/beschadigd bestand -> lege blacklist."""
    blacklist = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for k, v in obj.items():
                try:
                    blacklist[int(k)] = v
                except (TypeError, ValueError):
                    pass
    return blacklist


def save_blacklist(path, blacklist):
    """Schrijf de blacklist (gesorteerd op appid) als leesbaar JSON-object.
    Records: {"name":..,"reason":..,"added":..}."""
    ordered = {str(aid): blacklist[aid] for aid in sorted(blacklist)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1)


def blacklist_add(blacklist, aid, name, reason):
    """Voeg een appid toe aan de (in-memory) blacklist; retourneert True als
    hij nieuw was."""
    if aid in blacklist:
        return False
    blacklist[aid] = {"name": name, "reason": reason, "added": now_iso()}
    return True


# --------------------------------------------------------------------------- #
#  Records (slank formaat)                                                     #
# --------------------------------------------------------------------------- #
# Maandnamen (volledig + 3-letter afkorting) voor het omzetten van releasedatums.
_MONTH_NUMS = {}
for _i, _m in enumerate(
        ("January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"),
        start=1):
    _MONTH_NUMS[_m.lower()] = _i
    _MONTH_NUMS[_m.lower()[:3]] = _i


def iso_release_date(date_str):
    """Zet een releasedatum om naar 'yyyy-mm-dd'. Herkent o.a. "1 Nov, 2000",
    "Apr 18, 2011" en "2011 年 5 月 16 日". Retourneert '' als de datum
    ontbreekt of niet herkend wordt."""
    if not date_str:
        return ""
    s = date_str.strip()
    m = re.fullmatch(r"(\d{4})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):     # al ISO
        return s
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", s)   # "1 Nov, 2000"
    if m and m.group(2).lower() in _MONTH_NUMS:
        return f"{int(m.group(3)):04d}-{_MONTH_NUMS[m.group(2).lower()]:02d}-" \
               f"{int(m.group(1)):02d}"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)    # "Apr 18, 2011"
    if m and m.group(1).lower() in _MONTH_NUMS:
        return f"{int(m.group(3)):04d}-{_MONTH_NUMS[m.group(1).lower()]:02d}-" \
               f"{int(m.group(2)):02d}"
    return ""


def slim_record(appid, data):
    """Kies de relevante velden uit de (grote) appdetails-payload. De prijs
    is de price_overview zoals de API hem teruggeeft (altijd USD door cc=us);
    er wordt niets omgerekend."""
    price = data.get("price_overview")
    if isinstance(price, dict):
        price = dict(price)      # kopie: de payload wordt niet gemuteerd
        # Bij GEEN korting laat Steam initial_formatted leeg ("") terwijl
        # final_formatted het bedrag toont. Zet initial_formatted dan gelijk
        # aan final_formatted (zelfde bedrag, geen korting).
        if not price.get("discount_percent") and price.get("final_formatted"):
            price["initial_formatted"] = price["final_formatted"]
    else:
        price = {}
    release = data.get("release_date") or {}
    rel_str = release.get("date") or ""
    return {
        "appid": appid,
        "type": data.get("type"),
        "name": data.get("name"),
        "is_free": data.get("is_free"),
        "price_overview": price or None,
        "publishers": data.get("publishers"),
        "genres": [g.get("description") for g in data.get("genres") or []],
        "categories": [c.get("description")
                       for c in data.get("categories") or []],
        "release_date": rel_str,               # zoals de API hem geeft
        "release_date_format": iso_release_date(rel_str),   # "2000-11-01"
        "recommendations_total": (data.get("recommendations") or {}).get("total"),
    }


def fetch_player_count(appid, timeout=30):
    """Huidig aantal spelers via ISteamUserStats/GetNumberOfCurrentPlayers
    (officieel, keyless). Retourneert int of None (geen publieke stats of
    fout). Wordt NOOIT gebruikt om te blacklisten of de run te stoppen."""
    url = PLAYER_COUNT_URL + "?" + urllib.parse.urlencode(
        {"appid": appid, "format": "json"})
    for attempt in (1, 2):
        if stop_requested:
            return None
        try:
            req = urllib.request.Request(url,
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(
                    resp.read().decode("utf-8", errors="replace"))
            pc = ((payload.get("response") or {}).get("player_count"))
            return pc if isinstance(pc, int) else None
        except Exception:  # noqa: BLE001 - netwerk/parse/throttle
            if attempt == 1:
                wait_chunked(2.0)
    return None


# --------------------------------------------------------------------------- #
#  Storefront ophalen (batches, USD)                                           #
# --------------------------------------------------------------------------- #
def fetch_store_batch(appids, args, stats):
    """Haal een batch appids op in EEN storefront-request (STORE_BATCH per
    request, cc=us + l=english -> USD/Engels) met retry/backoff op het hele
    request. Retourneert voor elk appid een (appid, outcome, info)-tuple:
      'game'    -> info = het slanke record
      'other'   -> info = {"name":..,"type":..}
      'skipped' -> info = {"name":None,"reason":..} (geen storepagina)
      'failed'  -> info = None (request definitief mislukt -> volgende run)
    Retourneert None als de run is onderbroken (Ctrl+C). Het schrijven/
    blacklisten doet de caller."""
    for attempt in range(1, args.max_retries + 1):
        if stop_requested:
            return None
        stats["requests"] += 1
        try:
            url = APP_DETAILS_URL + "?" + urllib.parse.urlencode(
                # cc=us + l=english: voorkomt willekeurige valuta/taal per
                # request (de store-API kiest anders zelf een regio/edge).
                {"appids": [str(a) for a in appids],
                 "cc": "us", "l": "english"}, doseq=True)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                body = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
            body = b""
        except (urllib.error.URLError, OSError, ValueError):
            # netwerk-/timeout-fout of ongeldige response: opnieuw proberen
            if attempt < args.max_retries:
                wait_chunked(backoff_seconds(attempt, 0))
                continue
            return [(aid, "failed", None) for aid in appids]

        if code == 200:
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except ValueError:
                if attempt < args.max_retries:
                    wait_chunked(backoff_seconds(attempt, 0))
                    continue
                return [(aid, "failed", None) for aid in appids]
            # Veiligheid: een 200-response waarin GEEN van de aangevraagde
            # appids zit, is geen normale response (bv. throttling die 200
            # geeft met een leeg/afwijkend object) -> als request-fout
            # behandelen en opnieuw proberen; NOOIT als 'geen storepagina'
            # blacklisten (dat was de massale blacklist-bug van 2026-09-04).
            if not isinstance(payload, dict) or not any(
                    str(a) in payload for a in appids):
                if attempt < args.max_retries:
                    wait_chunked(backoff_seconds(attempt, 0))
                    continue
                return [(aid, "failed", None) for aid in appids]
            results = []
            for aid in appids:
                entry = payload.get(str(aid))
                if entry is None:
                    # appid ontbreekt in de response -> onzeker, dus 'failed'
                    # (volgende run opnieuw), NIET blacklisten.
                    results.append((aid, "failed", None))
                    continue
                if not entry.get("success"):
                    # Alleen een ECHTE success:false (het appid staat in de
                    # response maar heeft geen storepagina) -> blacklisten.
                    results.append((aid, "skipped",
                                    {"name": None, "reason": "no_store_page"}))
                    continue
                data = entry.get("data") or {}
                if data.get("type") == "game":
                    results.append((aid, "game", slim_record(aid, data)))
                else:
                    # Geen game (dlc/demo/software/...) -> blacklisten (caller)
                    results.append((aid, "other",
                                    {"name": data.get("name"),
                                     "type": data.get("type")}))
            return results
        elif code == 429:
            if attempt >= args.max_retries:
                return [(aid, "failed", None) for aid in appids]
            if attempt == 1:
                print(f"! HTTP 429 (throttled) bij batch appid "
                      f"{appids[0]}..{appids[-1]} - "
                      f"pauze {backoff_seconds(attempt, 429):.0f}s...")
            wait_chunked(backoff_seconds(attempt, 429))
        elif 500 <= code < 600:
            if attempt >= args.max_retries:
                return [(aid, "failed", None) for aid in appids]
            wait_chunked(backoff_seconds(attempt, code))
        else:
            # Het HELE request geeft een andere HTTP-fout (403/404/...): er
            # is geen per-appid info -> alles 'failed' (volgende run opnieuw),
            # NIET blacklisten (kan een tijdelijke blokkade zijn).
            return [(aid, "failed", None) for aid in appids]
    return [(aid, "failed", None) for aid in appids]


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def wait_chunked(seconds):
    """Slaap in kleine stukjes zodat Ctrl+C snel wordt opgepikt."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not stop_requested:
        time.sleep(min(0.5, end - time.monotonic()))


def backoff_seconds(attempt, code):
    base = 30.0 if code == 429 else 5.0
    cap = 300.0 if code == 429 else 120.0
    return min(cap, base * (2 ** (attempt - 1)))


def random_jitter(jitter):
    return random.uniform(0, jitter)


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(
        description="Steam-game-catalogus via OFFICIELE Steam-bronnen: "
                    "appid-lijst uit de keyed Web API, per-game data uit de "
                    "Storefront API (USD). 'Nieuw' = appid in de API-lijst "
                    "dat nog niet in de JSON-output staat.")
    p.add_argument("--key", default=None,
                   help="Steam Web API key (overslaat de popup)")
    p.add_argument("--save-key", action="store_true",
                   help="de ingevulde key bewaren in <data-dir>/api_key.txt")
    p.add_argument("--forget-key", action="store_true",
                   help="bewaarde key wissen en stoppen")
    p.add_argument("--data-dir", default="data",
                   help="map voor key + aux-output (default: data)")
    p.add_argument("--out", default=None,
                   help="JSON-output (jsonl) om tegen te diffen; default: "
                        "<data-dir>/games.jsonl")
    p.add_argument("--report-only", action="store_true",
                   help="alleen tonen welke appids nieuw zijn, niets ophalen "
                        "of toevoegen")
    p.add_argument("--limit", type=int, default=None,
                   help="max. aantal NIEUWE appids dat deze run wordt "
                        "opgehaald/verwerkt (1 request per appid). De rest "
                        "blijft 'nieuw' voor de volgende run "
                        "(default: geen limiet)")
    p.add_argument("--reset", action="store_true",
                   help="games/blacklist/duplicates.jsonl wissen en opnieuw "
                        "beginnen")
    p.add_argument("--blacklist", default=None,
                   help="pad naar de blacklist (default: "
                        "<data-dir>/blacklist.json)")
    p.add_argument("--blacklist-show", action="store_true",
                   help="inhoud van de blacklist tonen en stoppen")
    p.add_argument("--blacklist-add", nargs="+", metavar="APPID",
                   help="appids handmatig aan de blacklist toevoegen en "
                        "stoppen")
    p.add_argument("--blacklist-remove", nargs="+", metavar="APPID",
                   help="appids uit de blacklist halen en stoppen (ze worden "
                        "dan weer 'nieuw')")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"seconden rust tussen twee store-requests "
                        f"(default: {DEFAULT_DELAY})")
    p.add_argument("--jitter", type=float, default=DEFAULT_JITTER,
                   help=f"willekeurige spreiding op de pauze "
                        f"(default: {DEFAULT_JITTER})")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"timeout per HTTP-call in sec (default: "
                        f"{DEFAULT_TIMEOUT})")
    p.add_argument("--max-retries", type=int, default=DEFAULT_RETRIES,
                   help=f"pogingen per appid bij throttling/fouten "
                        f"(default: {DEFAULT_RETRIES})")
    p.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                   help=f"veiligheidslimiet op het aantal store-requests "
                        f"(1 appid per HTTP-call) per run "
                        f"(default: {DEFAULT_MAX_REQUESTS})")
    args = p.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    out_path = (os.path.abspath(args.out) if args.out
                else os.path.join(data_dir, "games.jsonl"))
    blacklist_path = (os.path.abspath(args.blacklist) if args.blacklist
                      else os.path.join(data_dir, "blacklist.json"))

    if args.blacklist_show:
        blacklist = load_blacklist(blacklist_path)
        if not blacklist:
            print("> Blacklist is leeg.")
        for aid in sorted(blacklist):
            r = blacklist[aid]
            print(f"   {aid}  {r.get('name') or '(onbekend)'}  "
                  f"[{r.get('reason')}]  toegevoegd {r.get('added')}")
        return

    if args.blacklist_add:
        blacklist = load_blacklist(blacklist_path)
        changed = False
        for raw in args.blacklist_add:
            try:
                aid = int(raw)
            except ValueError:
                print(f"! Ongeldig appid: {raw}")
                continue
            if blacklist_add(blacklist, aid, None, "manual"):
                print(f"> {aid} aan de blacklist toegevoegd (handmatig).")
                changed = True
            else:
                print(f"> {aid} stond al in de blacklist.")
        if changed:
            save_blacklist(blacklist_path, blacklist)
        return

    if args.blacklist_remove:
        blacklist = load_blacklist(blacklist_path)
        changed = False
        for raw in args.blacklist_remove:
            try:
                aid = int(raw)
            except ValueError:
                print(f"! Ongeldig appid: {raw}")
                continue
            if aid in blacklist:
                del blacklist[aid]
                print(f"> {aid} uit de blacklist gehaald (wordt weer 'nieuw').")
                changed = True
            else:
                print(f"> {aid} stond niet in de blacklist.")
        if changed:
            save_blacklist(blacklist_path, blacklist)
        return

    if args.forget_key:
        keyfile = os.path.join(data_dir, "api_key.txt")
        if os.path.isfile(keyfile):
            os.remove(keyfile)
            print(f"> Bewaarde API key verwijderd ({keyfile})")
        else:
            print("> Geen bewaarde API key aanwezig.")
        return

    if args.reset:
        # Opnieuw beginnen: dataset + output wissen (geen checkpoint meer).
        for name in ("games.jsonl", "blacklist.json", "duplicates.jsonl"):
            path = os.path.join(data_dir, name)
            if os.path.isfile(path):
                os.remove(path)
        if os.path.abspath(out_path) != os.path.join(data_dir, "games.jsonl") \
                and os.path.isfile(out_path):
            os.remove(out_path)
        print("> Reset: games/blacklist/duplicates.jsonl gewist - "
              "begint opnieuw vanaf het begin van de app-lijst")

    key = resolve_api_key(args, data_dir)

    print("\n=== SteamDataOfficial: catalogus via officiele Steam-bronnen ===")
    print("> Officiele app-lijst ophalen (Web API, keyed) ...")
    try:
        apps, bron = fetch_app_list(key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print("! API key geweigerd (HTTP 403/401). IStoreService/"
                  "GetAppList vereist een geldige key van "
                  "https://steamcommunity.com/dev/apikey (zonder/ongeldige "
                  "key gaf 403).")
        elif e.code == 404:
            print("! Beide officiele app-lijst-endpoints geven HTTP 404 "
                  "('Method not found'). Valve heeft ze op dit moment "
                  "uitgeschakeld; probeer het later opnieuw.")
        else:
            print(f"! Fout bij het ophalen van de app-lijst (HTTP {e.code}).")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"! Kan de app-lijst niet ophalen: {e}")
        sys.exit(1)

    api = {}
    for a in apps:
        try:
            api[int(a["appid"])] = a.get("name")
        except (KeyError, TypeError, ValueError):
            pass
    print(f"> App-lijst opgehaald via {bron} ({len(api)} apps).")

    known_appids, known_titles, title_appid = load_existing(out_path)
    blacklist = load_blacklist(blacklist_path)
    new_ids = sorted(aid for aid in api
                     if aid not in known_appids and aid not in blacklist)

    print(f"\nTotaal in de API-call          : {len(api)}")
    print(f"Al bekend (in de output)       : {len(known_appids)}")
    print(f"In de blacklist               : {len(blacklist)}")
    print(f"NIEUW (te verwerken)          : {len(new_ids)}")

    lim = args.limit or None                # 0 wordt behandeld als 'geen limiet'
    selected = new_ids
    if lim is not None and len(new_ids) > lim:
        selected = new_ids[:lim]
        print(f"> Beperkt tot {lim} deze run (--limit); "
              f"{len(new_ids) - lim} blijven 'nieuw' voor de volgende run.")
    remaining_new = len(new_ids) - len(selected)

    if args.report_only:
        if selected:
            for aid in selected[:200]:
                print(f"   + {aid}  {api.get(aid)}")
            if len(selected) > 200:
                print(f"   ... en nog {len(selected) - 200} meer")
        else:
            print("> Geen nieuwe games: alles uit de API-call staat al in "
                  "de output.")
        print("> --report-only: niets opgehaald/toegevoegd.")
        return

    if not selected:
        print("> Geen nieuwe games: alles uit de API-call staat al in de "
              "output.")
        return

    stats = {"requests": 0, "added": 0, "duplicate": 0, "dup_saved": 0,
             "other": 0, "skipped": 0, "failed": 0, "blacklisted": 0}
    added_titles = set()          # deze run toegevoegd (voorkomt intra-run dubbels)
    dup_seen = load_duplicate_appids(data_dir)

    n_req = (len(selected) + STORE_BATCH - 1) // STORE_BATCH
    print(f"\n> Storefront-data ophalen voor {len(selected)} nieuwe appids "
          f"(cc=us -> USD; {STORE_BATCH} appid per request -> "
          f"{n_req} requests) ...")
    print("  Druk op Ctrl+C om netjes te stoppen.\n")

    games_file = open(out_path, "a", encoding="utf-8")
    processed = 0
    try:
        for start in range(0, len(selected), STORE_BATCH):
            if stop_requested:
                break
            if stats["requests"] >= args.max_requests:
                print(f"\n> Veiligheidslimiet van {args.max_requests} requests "
                      "bereikt. Draai het script opnieuw om verder te gaan.")
                break

            batch = selected[start:start + STORE_BATCH]
            results = fetch_store_batch(batch, args, stats)
            if results is None:              # onderbroken (Ctrl+C)
                break
            for aid, outcome, info in results:
                if outcome == "game":
                    title = (info.get("name") or "").strip().lower()
                    if title in known_titles or title in added_titles:
                        # zelfde titel al aanwezig (China/regio-editie) ->
                        # niet opslaan; wel in duplicates.jsonl bewaren.
                        stats["duplicate"] += 1
                        dup_record = dict(info)
                        dup_record["duplicate_of"] = title_appid.get(title)
                        if append_duplicates(data_dir, [dup_record], dup_seen):
                            stats["dup_saved"] += 1
                    else:
                        # Momenteel spelersaantal (keyless, robuust) + dit is
                        # de EERSTE (en enige) opname van deze game -> _1.
                        pc = fetch_player_count(aid, args.timeout)
                        stats["requests"] += 1
                        info["last_seen_player_count"] = pc
                        info["appid_amount"] = f"{aid}_1"
                        games_file.write(
                            json.dumps(info, ensure_ascii=False) + "\n")
                        games_file.flush()
                        stats["added"] += 1
                        known_appids.add(aid)
                        known_titles.add(title)
                        added_titles.add(title)
                        title_appid.setdefault(title, aid)
                        print(f"   + {aid}  {info.get('name')}  "
                              f"(players: {pc})")
                elif outcome in ("other", "skipped"):
                    stats[outcome] += 1
                    # Geen game / geen storepagina / geblokkeerd: afgehandeld
                    # -> in de blacklist, zodat een volgende run ze niet
                    # opnieuw ophaalt. De gebruiker kan ze er later uithalen.
                    if outcome == "other":
                        reason = f"not_game:{info.get('type')}"
                        name = info.get("name")
                    else:
                        reason = info.get("reason")
                        name = info.get("name") or api.get(aid)
                    if blacklist_add(blacklist, aid, name, reason):
                        stats["blacklisted"] += 1
                    if stats["blacklisted"] % 50 == 0:
                        save_blacklist(blacklist_path, blacklist)
                else:                          # 'failed': volgende run opnieuw
                    stats["failed"] += 1

            processed += len(batch)
            if all(o == "failed" for _, o, _ in results):
                # hele batch mislukt (throttling/netwerk) -> stoppen; deze
                # appids blijven 'nieuw' voor de volgende run.
                print("> Hele batch mislukt (throttling/netwerk) - stop deze "
                      "run; draai het script later opnieuw.")
                break

            if processed % 25 == 0:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                      f"verwerkt={processed}/{len(selected)}  "
                      f"requests={stats['requests']}  toegevoegd={stats['added']}")

            if not stop_requested:
                wait_chunked(args.delay + random_jitter(args.jitter))
    finally:
        games_file.close()
        if stats["blacklisted"]:
            save_blacklist(blacklist_path, blacklist)

    remaining_new = (len(new_ids) - stats["added"] - stats["blacklisted"])
    print("\n=== Samenvatting ===")
    print(f"Appids verwerkt deze run     : {processed}")
    print(f"Requests (store-API)         : {stats['requests']}")
    print(f"Nieuwe games toegevoegd      : {stats['added']}")
    print(f"Totaal in games.jsonl        : {len(known_appids)}")
    print(f"Dubbele titel overgeslagen   : {stats['duplicate']}   "
          f"(zelfde naam stond er al in; {stats['dup_saved']} bewaard in "
          f"duplicates.jsonl)")
    print(f"In de blacklist gezet        : {stats['blacklisted']}   "
          f"(niet-game {stats['other']} + geen storepagina/geblokkeerd "
          f"{stats['skipped']})")
    print(f"Failed (netwerk/429)         : {stats['failed']}   "
          "(volgende run probeert ze vanzelf opnieuw)")
    if remaining_new > 0:
        print(f"\n> {remaining_new} appids blijven 'nieuw'; draai het script "
              "opnieuw voor de volgende.")


if __name__ == "__main__":
    main()
