#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - Steam-game-catalogus via OFFICIELE Steam-bronnen
====================================================================

Drie officiele Steam-bronnen (geen derde partijen zoals SteamSpy):

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
  3. Review-samenvatting: de officiele Steam Review API
     (store.steampowered.com/appreviews/{appid}) - ZONDER API key, 1
     request per appid. Met language=all + purchase_type=all + filter=all
     krijg je hetzelfde totaalbeeld als op de storepagina: ALLE reviews in
     ALLE talen, inclusief niet-aangeschafte (belangrijk voor F2P-games -
     zonder purchase_type=all toont Warframe bv. 2.871 reviews i.p.v.
     676.084). num_per_page=0 -> alleen de query_summary (review_score +
     review_score_desc zoals 'Very Positive'/'Mixed', en de positive/
     negative/total-tellingen), geen review-teksten.

'Nieuwe games' zijn appids die wel in de officiele app-lijst zitten maar nog
NIET in de JSON-output (data/games.jsonl). Van elke nieuwe game worden de
relevante velden opgehaald en toegevoegd. Appids die er al in staan worden
overgeslagen. Stop je het script (Ctrl+C) of is het budget op, dan pakt de
volgende run gewoon weer op waar games.jsonl gebleven was - er is GEEN apart
checkpoint-bestand; het aantal regels in games.jsonl is de waarheid.

Prijzen: elke game wordt opgevraagd met cc=us + l=english, dus price_overview
is standaard USD en de tekst (genres/categorieen) altijd Engels - er wordt
niets omgerekend, alleen bewaard wat de API teruggeeft. Bij geen korting
laat Steam initial_formatted leeg; dan wordt hij gelijkgezet aan
final_formatted (hetzelfde bedrag). UITZONDERING: us_region_blocked-games
(alleen elders te koop, zie Filter) worden via hun eigen regio opgehaald -
hun price_overview heeft dan de valuta van die regio (bv. EUR voor nl) en
ze staan in us_region_blocked.json.

Filter: alleen type == "game" wordt opgeslagen. Apps die geen game zijn
(dlc, demo, muziek, software, ...) worden automatisch in de blacklist gezet
(data/blacklist.json). Een appid ZONDER US-storepagina (echte success:false
op cc=us) wordt als volgt geclassificeerd:
  1. Bestaat de game in een andere regio (REGION_FALLBACKS, default cc=nl)?
     Dan wordt hij GEWOON OPGENOMEN in games.jsonl, opgehaald via die regio
     (cc=nl -> price_overview in EUR; er wordt niets omgerekend - de valuta
     is wat de API teruggeeft) en bijgehouden in data/us_region_blocked.json
     ({appid: {name, cc, currency, added}}). Records met een niet-USD-prijs
     horen daar dus ook altijd in te staan (startcontrole van de run). Alleen
     als dezelfde titel al in de master staat (regionaal duplicaat van een
     bekende game, bv. IL-2 Dover 63970 -> 63950), wordt hij NIET toegevoegd
     maar als duplicate_of bewaard in duplicates.jsonl.
  2. Bestaat hij nergens en matcht zijn naam (uit de officiele app-lijst)
     een game die al in de master staat? Dan is het een legacy-duplicaat
     (oud/regio-appid zonder eigen pagina, bv. Max Payne 201330 -> 12140)
     -> bewaard in duplicates.jsonl met duplicate_of = het bestaande appid
     (NIET in de blacklist).
  3. Anders (echt verwijderd/verdwenen) -> blacklist-reden 'no_store_page'.
De blacklist blijft beheersbaar - een appid eruit halen (via
--blacklist-remove of het bestand) maakt hem weer 'nieuw'; zelf toevoegen
kan via --blacklist-add of handmatig in het bestand. Alleen tijdelijke
fouten (netwerk/429) worden NIET geblacklist - die probeert een volgende
run gewoon opnieuw. Een titel wordt nooit twee keer toegevoegd: komt
dezelfde naam nog eens voor (China/regio-editie, heruitgave of een
no-store-duplicaat), dan wordt die bewaard in duplicates.jsonl met
duplicate_of = het behouden appid.

Output (in data/):
    games.jsonl            1 JSON-object per regel, alleen games (type == "game")
    blacklist.json         appids die NIET opnieuw worden geprobeerd
                          (niet-game, no_store_page of handmatig). Zelf
                          beheerbaar: eruit halen = weer 'nieuw'
    duplicates.jsonl       duplicaten (zelfde titel als een game in de master;
                          ook no-store-appids zonder eigen pagina, bv. oude
                          appids die naar de echte app redirecten), per
                          appid 1x, met duplicate_of
    us_region_blocked.json games die in de US-store NIET te koop zijn maar
                          elders wel (opgenomen in games.jsonl via hun eigen
                          regio; valuta bv. EUR). {appid: {name, cc,
                          currency, added}}

Velden per game (slank formaat): appid, type, name, is_free, price_overview,
publishers, genres (namen), categories (namen), release_date (zoals de API
hem geeft), release_date_format (zelfde datum in yyyy-mm-dd),
recommendations_total, last_seen_player_count (momentopname van het huidige
spelersaantal via GetNumberOfCurrentPlayers, keyless; None bij geen
publieke data), review_score, review_score_desc (bv. 'Very Positive',
'Mixed', 'No user reviews'), review_positive, review_negative,
review_total (uit de Review API; None als die request mislukte). Elke game
staat in DIT bestand (de basis) maar 1x en heeft GEEN appid_amount: de
doorlopende per-game nummering ("<appid>_1", "_2", ...) leeft in het
zusterscript fetch_new_game_info.py, dat herhaalde momentopnamen per game
schrijft naar data/games_extra_info.jsonl.

Gebruik:
    python fetch_games_initial.py            # popup voor API key, daarna verwerken
    python fetch_games_initial.py --key ABC.. # key meegeven; wordt meteen
                                             # encoded bewaard voor volgende runs
    python fetch_games_initial.py --forget-key   # bewaarde key wissen
    python fetch_games_initial.py --limit 500    # max. 500 nieuwe appids deze run
    python fetch_games_initial.py --max-duration-minutes 300  # netjes stoppen ~5 min vóór
                                             # de step-timeout (GitHub Action)
    python fetch_games_initial.py --report-only  # alleen progressie tonen in te verwerken games

    De API key wordt de eerste keer AUTOMATISCH encoded (XOR+base64) bewaard
    in %APPDATA%/SteamDataOfficial/api_key.txt - BUIEN de repo (data/ wordt
    gecommit en mag geen key bevatten). Een volgende run leest hem daar
    gewoon weer uit en vraagt dus niet opnieuw om de key.

    Dit is de EENMALIGE volledige doorgang: elke game komt precies 1x in
    data/games.jsonl (de basis, ZONDER appid_amount). Het zusterscript
    fetch_new_game_info.py schrijft daarnaast herhaalde momentopnamen per
    game naar data/games_extra_info.jsonl, genummerd "<appid>_1", "_2", ...
"""

import argparse
import base64
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

# Bestandsrotatie: datasets groeien door in delen van max ~90 MB
# (games.jsonl, duplicates.jsonl, ...). Al het lezen gaat via iter_lines
# (delen samengevoegd), schrijven via RotatingAppend/remove_all_parts -
# zie data_rotation.py.
from data_rotation import (iter_lines, remove_all_parts, RotatingAppend)

ISTORE_APP_LIST_URL = ("https://api.steampowered.com/IStoreService/"
                       "GetAppList/v1/")
LEGACY_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
PLAYER_COUNT_URL = ("https://api.steampowered.com/ISteamUserStats/"
                    "GetNumberOfCurrentPlayers/v1/")
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
STORE_BATCH = 1     # appids per storefront-request. appdetails accepteert
                    # maar EEN appid per request (geverifieerd 2026-09-04:
                    # meerdere herhaalde appids gaven alleen de laatste
                    # terug, 100 gaf HTTP 400) -> dus 1 request per appid.

# Regiocheck bij een appid zonder US-storepagina (echte success:false op
# cc=us): in deze regio's wordt de game alsnog gezocht (find_fallback_region).
# Wordt hij daar gevonden, dan wordt hij OPGENOMEN in games.jsonl via die
# regio (de valuta is dan die van die regio, bv. EUR voor nl - er wordt
# niets omgerekend) en bijgehouden in us_region_blocked.json.
REGION_FALLBACKS = ("nl",)
US_BLOCKED_FILE = "us_region_blocked.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DEFAULT_MAX_REQUESTS = 222222222   # veiligheidslimiet op store-requests per run
DEFAULT_DELAY = 0.4            # seconden rust tussen twee store-requests
DEFAULT_JITTER = 0.1           # extra willekeurige spreiding op de pauze
DEFAULT_TIMEOUT = 30           # timeout per HTTP-call (sec)
DEFAULT_RETRIES = 6            # pogingen per appid bij throttling/fouten

# Marge (minuten) die het script aanhoudt vóór het opgegeven duurbudget
# (--max-duration-minutes): zo stopt de run NETJES vóór de GitHub step-
# timeout hem keihard zou afkappen (laatste records netjes weggeschreven).
GRACEFUL_STOP_MARGIN_MINUTES = 5

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
#  API key: popup/console of --key, daarna AUTOMATISCH encoded bewaard.        #
#  De key staat BUIEN de repo (data/ wordt gecommit en mag geen key            #
#  bevatten). Encoden = XOR-masker + base64: bewust alleen obfuscatie,         #
#  geen echte beveiliging - wie de code heeft kan de key teruglezen.           #
# --------------------------------------------------------------------------- #
_KEY_MASK = b"SteamDataOfficial::api-key::2026-09-05"


def _encode_key(key):
    """Key -> XOR-masker + urlsafe-base64 (voor opslag, geen beveiliging)."""
    data = key.encode("utf-8")
    masked = bytes(b ^ _KEY_MASK[i % len(_KEY_MASK)]
                   for i, b in enumerate(data))
    return base64.urlsafe_b64encode(masked).decode("ascii")


def _decode_key(token):
    """Omgekeerde van _encode_key; None bij een kapot/onbekend formaat."""
    try:
        masked = base64.urlsafe_b64decode(token.encode("ascii"))
        data = bytes(b ^ _KEY_MASK[i % len(_KEY_MASK)]
                     for i, b in enumerate(masked))
        return data.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def _looks_like_key(key):
    """Steam Web API keys zijn altijd 32 hex-karakters."""
    return bool(re.fullmatch(r"[0-9A-Fa-f]{32}", (key or "").strip()))


def default_keyfile():
    """Standaardplek van de bewaarde API key: BUIEN de repo, in
    %APPDATA%/SteamDataOfficial (of de home-map als APPDATA ontbreekt).
    Zo kan de key nooit met de repo mee gecommit worden."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "SteamDataOfficial", "api_key.txt")


def save_key_encoded(keyfile, key):
    """Schrijf de key encoded weg (maakt de map aan indien nodig)."""
    folder = os.path.dirname(keyfile)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(keyfile, "w", encoding="utf-8") as f:
        f.write(_encode_key(key) + "\n")


def load_saved_key(keyfile):
    """Lees de bewaarde (encoded) key uit keyfile. Migreert een oud
    plaintext-bestand automatisch naar encoded. Retourneert None als er
    geen geldige key staat."""
    if not os.path.isfile(keyfile):
        return None
    try:
        with open(keyfile, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    if not content:
        return None
    key = _decode_key(content)
    if _looks_like_key(key):
        return key
    if _looks_like_key(content):        # oud formaat (plaintext) -> migreren
        save_key_encoded(keyfile, content)
        return content
    return None


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


def resolve_api_key(args):
    """Bepaal de key: --key > bewaarde (encoded) key > popup > console.
    Een nieuw verkregen key wordt MET EEN encoded bewaard op de
    standaardplek buiten de repo, zodat volgende runs hem niet opnieuw
    vragen (een aparte --save-key is niet meer nodig)."""
    keyfile = args.keyfile or default_keyfile()
    if args.key:
        save_key_encoded(keyfile, args.key)
        print(f"> API key bewaard (encoded) in {keyfile}")
        return args.key

    saved = load_saved_key(keyfile)
    if saved:
        return saved

    key = ask_key_popup()
    if key is None:                       # geen GUI of geannuleerd
        try:
            key = input("Steam Web API key: ").strip()
        except EOFError:
            key = ""
    if not key:
        print("! Geen API key opgegeven (gebruik --key <key> om hem te "
              "bewaren, of vul hem in).")
        sys.exit(1)
    save_key_encoded(keyfile, key)
    print(f"> API key bewaard (encoded) in {keyfile} - volgende runs vragen "
          "hem niet meer op.")
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
    """Lees de al opgeslagen games uit de JSON-output (games.jsonl*; alle
    delen samengevoegd, of losse appids per regel). Retourneert (appids,
    titels, titel_appid): bekende appids, bestaande titels (lowercase) en
    per titel het eerste (laagste) appid."""
    appids, titles, title_appid = set(), set(), {}
    for line in iter_lines(path):
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
    """Appids die al in duplicates.jsonl* staan (per appid maar 1x
    bewaren; alle delen samengevoegd)."""
    path = os.path.join(data_dir, "duplicates.jsonl")
    known = set()
    for line in iter_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            known.add(int(json.loads(line).get("appid")))
        except (TypeError, ValueError):
            continue
    return known


def append_duplicates(data_dir, records, seen):
    """Schrijf records weg naar duplicates.jsonl* (alleen nieuwe appids;
    rotatie bij ~90 MB). Retourneert hoeveel er nieuw zijn toegevoegd."""
    written = 0
    if not records:
        return written
    writer = RotatingAppend(os.path.join(data_dir, "duplicates.jsonl"))
    try:
        for r in records:
            try:
                aid = int(r.get("appid"))
            except (TypeError, ValueError):
                continue
            if aid in seen:
                continue
            writer.write_line(json.dumps(r, ensure_ascii=False))
            seen.add(aid)
            written += 1
    finally:
        writer.close()
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


def load_us_region_blocked(data_dir):
    """Lees us_region_blocked.json: games die in de US-store niet te koop
    zijn maar elders wel (opgenomen in games.jsonl via hun eigen regio).
    Formaat als blacklist: {appid: {name, cc, currency, added}} (ints)."""
    path = os.path.join(data_dir, US_BLOCKED_FILE)
    blocked = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for k, v in obj.items():
                try:
                    blocked[int(k)] = v
                except (TypeError, ValueError):
                    pass
    return blocked


def save_us_region_blocked(data_dir, blocked):
    """Schrijf us_region_blocked.json (gesorteerd op appid) weg."""
    ordered = {str(aid): blocked[aid] for aid in sorted(blocked)}
    path = os.path.join(data_dir, US_BLOCKED_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1)


def us_blocked_add(blocked, aid, name, cc, currency):
    """Voeg een appid toe aan de (in-memory) us_region_blocked-registratie;
    retourneert True als hij nieuw was."""
    if aid in blocked:
        return False
    blocked[aid] = {"name": name, "cc": cc, "currency": currency,
                    "added": now_iso()}
    return True


def check_non_usd_tracking(master_path, us_blocked):
    """Startcontrole: elk games.jsonl*-record (alle delen samengevoegd)
    met een prijs in een NIET-USD-valuta moet in us_region_blocked.json
    staan (dat is de enige manier waarop zo'n record ontstaat). Waarschuwt
    ook over tracking-entries die niet (meer) in de master staan."""
    untracked, known = [], set()
    for line in iter_lines(master_path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            aid = int(r.get("appid"))
        except (TypeError, ValueError):
            continue
        known.add(aid)
        cur = (r.get("price_overview") or {}).get("currency")
        if cur and cur != "USD" and aid not in us_blocked:
            untracked.append((aid, r.get("name"), cur))
    if untracked:
        print(f"! Waarschuwing: niet-USD-records zonder {US_BLOCKED_FILE}-"
              "tracking:")
        for aid, name, cur in untracked:
            print(f"    {aid}  {name}  ({cur})")
    orphans = [a for a in sorted(us_blocked) if a not in known]
    if orphans:
        print(f"! Waarschuwing: {US_BLOCKED_FILE} bevat appids die niet in "
              "games.jsonl staan:")
        for aid in orphans:
            print(f"    {aid}  {us_blocked[aid].get('name')}")


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


# Steam is op 12 september 2003 gelanceerd. Games met een releasedatum van
# voor die datum (de API geeft bv. Half-Life als 1998) zijn pas op Steam
# verschenen bij de lancering: zet hun releasedatum dan op 2003-09-12.
STEAM_LAUNCH_DATE = "2003-09-12"
STEAM_LAUNCH_LABEL = "12 Sep, 2003"


def clamp_steam_launch(iso_fmt):
    """Release clamp: als de releasedatum (yyyy-mm-dd) voor 2003-09-12
    ligt, wordt hij op de Steam-lancering gezet. Retourneert (iso, label);
    label is None als de datum niet aangepast wordt."""
    if iso_fmt and iso_fmt < STEAM_LAUNCH_DATE:
        return STEAM_LAUNCH_DATE, STEAM_LAUNCH_LABEL
    return iso_fmt, None


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
    rel_iso = iso_release_date(rel_str)        # "2000-11-01"
    # Games van voor de Steam-lancering (2003-09-12) tellen vanaf de
    # lancering: de API geeft bv. Half-Life als 1998, maar op Steam
    # verscheen het pas op 2003-09-12.
    rel_iso, rel_launch_label = clamp_steam_launch(rel_iso)
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
        "release_date": rel_launch_label or rel_str,   # zoals de API / lancering
        "release_date_format": rel_iso,                # "2003-09-12"
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


def fetch_review_summary(appid, timeout=30):
    """Review-samenvatting via store.steampowered.com/appreviews/{appid}
    (officieel, keyless). language=all + purchase_type=all + filter=all ->
    hetzelfde totaalbeeld als op de storepagina (voor F2P-games telt
    purchase_type=all de niet-aangeschafte reviews mee). num_per_page=0 ->
    alleen de query_summary, geen review-teksten. Retourneert
    {"review_score":.., "review_score_desc":.., "review_positive":..,
    "review_negative":.., "review_total":..} of None als de request na 2
    pogingen mislukte. Een game ZONDER reviews geeft geen None maar een
    lege summary (desc 'No user reviews', alles 0). Wordt NOOIT gebruikt
    om te blacklisten of de run te stoppen."""
    url = REVIEWS_URL.format(appid=appid) + "?" + urllib.parse.urlencode({
        "json": 1, "language": "all", "purchase_type": "all",
        "filter": "all", "num_per_page": 0})
    for attempt in (1, 2):
        if stop_requested:
            return None
        try:
            req = urllib.request.Request(url,
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(
                    resp.read().decode("utf-8", errors="replace"))
            qs = ((payload.get("query_summary") or {})
                  if isinstance(payload, dict) else {})
            if not isinstance(qs, dict):
                return None
            return {
                "review_score": qs.get("review_score"),
                "review_score_desc": qs.get("review_score_desc"),
                "review_positive": qs.get("total_positive"),
                "review_negative": qs.get("total_negative"),
                "review_total": qs.get("total_reviews"),
            }
        except Exception:  # noqa: BLE001 - netwerk/parse/throttle
            if attempt == 1:
                wait_chunked(2.0)
    return None


def enrich_live(record, aid, args, stats):
    """Vul een slank record aan met last_seen_player_count en de
    review-samenvatting (beide keyless, robuust). Gebruikt door de gewone
    toevoeging EN de us_region_blocked-toevoeging (via een andere regio)."""
    pc = fetch_player_count(aid, args.timeout)
    stats["requests"] += 1
    record["last_seen_player_count"] = pc
    # Review-samenvatting ALTIJD ophalen (geen --no-reviews meer): reviews
    # moeten zo compleet mogelijk zijn.
    rev = fetch_review_summary(aid, args.timeout)
    stats["requests"] += 1
    for key in ("review_score", "review_score_desc",
                "review_positive", "review_negative",
                "review_total"):
        record[key] = (rev or {}).get(key)
    return pc


# --------------------------------------------------------------------------- #
#  Storefront ophalen (per regio; standaard USD)                               #
# --------------------------------------------------------------------------- #
def fetch_store_batch(appids, args, stats, cc="us"):
    """Haal een batch appids op in EEN storefront-request (STORE_BATCH per
    request, l=english; standaard cc=us -> USD/Engels, maar de caller kan een
    andere regio meegeven, bv. 'nl' voor us_region_blocked-games -> EUR) met
    retry/backoff op het hele request. Retourneert voor elk appid een
    (appid, outcome, info)-tuple:
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
                # cc + l=english: voorkomt willekeurige valuta/taal per
                # request (de store-API kiest anders zelf een regio/edge).
                {"appids": [str(a) for a in appids],
                 "cc": cc, "l": "english"}, doseq=True)
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
                    # response maar heeft geen US-storepagina) -> 'skipped';
                    # de caller classificeert verder (regio/duplicaat/
                    # no_store_page) i.p.v. blind te blacklisten.
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


def find_fallback_region(aid, args, stats):
    """Zoek of de game (success:true) in een fallback-regio bestaat
    (REGION_FALLBACKS, dus niet-US). Retourneert:
      cc (str) -> daar te koop (de caller haalt hem via die regio op en
                  neemt hem op in games.jsonl + us_region_blocked.json),
      ""       -> ook elders geen pagina (definitief),
      None     -> request definitief mislukt (netwerk/429) -> dan NIETS
                  doen; de volgende run probeert het opnieuw."""
    for cc in REGION_FALLBACKS:
        for attempt in range(1, args.max_retries + 1):
            if stop_requested:
                return None
            stats["requests"] += 1
            try:
                url = APP_DETAILS_URL + "?" + urllib.parse.urlencode(
                    {"appids": aid, "cc": cc, "l": "english"})
                req = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req,
                                            timeout=args.timeout) as resp:
                    body = resp.read()
                    code = resp.status
            except urllib.error.HTTPError as e:
                code = e.code
            except (urllib.error.URLError, OSError, ValueError):
                if attempt < args.max_retries:
                    wait_chunked(backoff_seconds(attempt, 0))
                    continue
                return None
            if code == 200:
                try:
                    payload = json.loads(
                        body.decode("utf-8", errors="replace"))
                except ValueError:
                    if attempt < args.max_retries:
                        wait_chunked(backoff_seconds(attempt, 0))
                        continue
                    return None
                # 200 zonder dit appid = geen normale response -> retry
                if not isinstance(payload, dict) or str(aid) not in payload:
                    if attempt < args.max_retries:
                        wait_chunked(backoff_seconds(attempt, 0))
                        continue
                    return None
                if payload.get(str(aid)).get("success"):
                    return cc
                break          # deze regio: geen pagina -> volgende fallback
            elif code == 429:
                if attempt >= args.max_retries:
                    return None
                if attempt == 1:
                    print(f"! HTTP 429 bij regiocheck appid {aid} ({cc}) - "
                          f"pauze {backoff_seconds(attempt, 429):.0f}s...")
                wait_chunked(backoff_seconds(attempt, 429))
            elif 500 <= code < 600:
                if attempt >= args.max_retries:
                    return None
                wait_chunked(backoff_seconds(attempt, code))
            else:
                return ""      # andere HTTP-fout: geen pagina in deze regio
    return ""


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
                   help="Steam Web API key (overslaat de popup; wordt meteen "
                        "encoded bewaard voor volgende runs)")
    p.add_argument("--keyfile", default=None,
                   help="pad van het bestand met de bewaarde (encoded) key "
                        "(default: buiten de repo, in "
                        "%%APPDATA%%/SteamDataOfficial/api_key.txt)")
    p.add_argument("--forget-key", action="store_true",
                   help="bewaarde key wissen en stoppen")
    p.add_argument("--data-dir", default="data",
                   help="map voor de aux-output (default: data)")
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
    p.add_argument("--max-duration-minutes", type=float, default=None,
                   help="max. aantal minuten dat deze run mag draaien "
                        "(zet dit gelijk aan de step-timeout in de GitHub "
                        "Action). Het script stopt zelf NETJES "
                        f"{GRACEFUL_STOP_MARGIN_MINUTES} minuten vóór die "
                        "grens: de laatste records worden weggeschreven en "
                        "blacklist/duplicates/us_region_blocked bewaard, "
                        "daarna stopt het netjes (exit 0) i.p.v. keihard "
                        "afgekapt te worden. Zonder deze optie draait het "
                        "onbeperkt.")
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
        keyfile = args.keyfile or default_keyfile()
        if os.path.isfile(keyfile):
            os.remove(keyfile)
            print(f"> Bewaarde API key verwijderd ({keyfile})")
        else:
            print("> Geen bewaarde API key aanwezig.")
        return

    if args.reset:
        # Opnieuw beginnen: dataset + output wissen (geen checkpoint meer).
        # us_region_blocked.json hoort erbij: die verwijst naar games die
        # straks niet meer in de master staan. games.jsonl en
        # duplicates.jsonl kunnen uit meerdere delen bestaan
        # (games_2.jsonl, duplicates_2.jsonl, ... na ~90 MB-rotatie) -
        # remove_all_parts wist basis + delen.
        if os.path.abspath(out_path) != os.path.join(data_dir, "games.jsonl"):
            remove_all_parts(out_path)
        remove_all_parts(os.path.join(data_dir, "games.jsonl"))
        remove_all_parts(os.path.join(data_dir, "duplicates.jsonl"))
        for name in ("blacklist.json", US_BLOCKED_FILE):
            path = os.path.join(data_dir, name)
            if os.path.isfile(path):
                os.remove(path)
        print("> Reset: games/blacklist/duplicates/us_region_blocked.json "
              "gewist - begint opnieuw vanaf het begin van de app-lijst")

    key = resolve_api_key(args)

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
    dup_seen = load_duplicate_appids(data_dir)
    us_blocked = load_us_region_blocked(data_dir)
    new_ids = sorted(aid for aid in api
                     if aid not in known_appids
                     and aid not in blacklist
                     and aid not in dup_seen)

    print(f"\nTotaal in de API-call          : {len(api)}")
    print(f"Al bekend (in de output)       : {len(known_appids)}")
    print(f"In de blacklist               : {len(blacklist)}")
    print(f"Al duplicaat (duplicates.jsonl): {len(dup_seen)}")
    print(f"US-geblokkeerd (elders opgehaald): {len(us_blocked)}")
    print(f"NIEUW (te verwerken)          : {len(new_ids)}")
    check_non_usd_tracking(out_path, us_blocked)

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
        print("RUN_STATUS=complete")
        return

    stats = {"requests": 0, "added": 0, "duplicate": 0, "dup_saved": 0,
             "other": 0, "skipped": 0, "failed": 0, "blacklisted": 0,
             "us_blocked": 0}
    added_titles = set()          # deze run toegevoegd (voorkomt intra-run dubbels)

    # Duurbudget (--max-duration-minutes): netjes stoppen enkele minuten
    # vóór de opgegeven grens, zodat de GitHub step-timeout de run niet
    # keihard afkapt.
    t0 = time.monotonic()
    if args.max_duration_minutes:
        deadline = max(0.0, args.max_duration_minutes
                       - GRACEFUL_STOP_MARGIN_MINUTES) * 60.0
    else:
        deadline = None

    n_req = (len(selected) + STORE_BATCH - 1) // STORE_BATCH
    print(f"\n> Storefront-data ophalen voor {len(selected)} nieuwe appids "
          f"(cc=us -> USD; {STORE_BATCH} appid per request -> "
          f"{n_req} requests) ...")
    print("  Druk op Ctrl+C om netjes te stoppen.\n")

    games_file = RotatingAppend(out_path, log=print)
    processed = 0

    def save_new_game(info, aid, note=""):
        """Schrijf een nieuw slank record naar games.jsonl en werk de
        in-memory known-sets bij. Gebruikt door de gewone toevoeging én de
        us_region_blocked-toevoeging (via een andere regio/valuta)."""
        title = (info.get("name") or "").strip().lower()
        games_file.write_line(json.dumps(info, ensure_ascii=False))
        stats["added"] += 1
        known_appids.add(aid)
        known_titles.add(title)
        added_titles.add(title)
        title_appid.setdefault(title, aid)
        print(f"   + {aid}  {info.get('name')}  {note}"
              f"(players: {info.get('last_seen_player_count')}, reviews: "
              f"{info.get('review_score_desc')})")

    try:
        for start in range(0, len(selected), STORE_BATCH):
            if stop_requested:
                break
            if deadline is not None and (time.monotonic() - t0) >= deadline:
                print("\n> Tijdsbudget bijna op (limiet "
                      f"{args.max_duration_minutes:.0f} min, marge "
                      f"{GRACEFUL_STOP_MARGIN_MINUTES} min) - de run wordt "
                      "netjes afgerond; draai het script opnieuw voor de "
                      "rest.")
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
                        # Momenteel spelersaantal (keyless, robuust); dit is
                        # de enige regel van deze game in de basis (geen
                        # appid_amount - die nummering doet de extra-info).
                        enrich_live(info, aid, args, stats)
                        save_new_game(info, aid)
                elif outcome == "other":
                    stats["other"] += 1
                    # Geen game (dlc/demo/software/...): afgehandeld -> in de
                    # blacklist, zodat een volgende run ze niet opnieuw haalt.
                    reason = f"not_game:{info.get('type')}"
                    if blacklist_add(blacklist, aid, info.get("name"),
                                     reason):
                        stats["blacklisted"] += 1
                    if stats["blacklisted"] % 50 == 0:
                        save_blacklist(blacklist_path, blacklist)
                elif outcome == "skipped":
                    # Echte success:false op cc=us: geen US-storepagina.
                    # Classificeren i.p.v. blind blacklisten.
                    stats["skipped"] += 1
                    name = info.get("name") or api.get(aid)
                    title = (name or "").strip().lower()
                    # 1) Bestaat de game in een andere regio (bv. cc=nl)?
                    cc_found = find_fallback_region(aid, args, stats)
                    if cc_found is None:      # netwerkfout bij de check ->
                        stats["failed"] += 1  # NIETS doen; volgende run
                        continue               # probeert het opnieuw.
                    if cc_found:
                        # 1a) Titel al in de master? Dan is het een regionaal
                        #     duplicaat van een bekende game (bv. IL-2 Dover
                        #     63970 -> 63950) -> duplicate_of, niet toevoegen.
                        canon = title_appid.get(title) if title else None
                        if canon is not None:
                            stats["duplicate"] += 1
                            dup_record = {"appid": aid, "name": name,
                                          "duplicate_of": canon,
                                          "reason": "us_region_blocked"}
                            if append_duplicates(data_dir, [dup_record],
                                                 dup_seen):
                                stats["dup_saved"] += 1
                            continue
                        # 1b) Elders te koop + titel nog niet in de master:
                        #     opnemen via die regio (eigen valuta, bv. EUR)
                        #     én registreren in us_region_blocked.json.
                        res = fetch_store_batch([aid], args, stats,
                                                cc=cc_found)
                        if res is None:        # onderbroken (Ctrl+C)
                            break
                        o2, i2 = res[0][1], res[0][2]
                        if o2 == "game":
                            enrich_live(i2, aid, args, stats)
                            currency = ((i2.get("price_overview") or {})
                                        .get("currency"))
                            if us_blocked_add(us_blocked, aid,
                                              i2.get("name"), cc_found,
                                              currency):
                                stats["us_blocked"] += 1
                            save_new_game(
                                i2, aid,
                                f"(US-geblokkeerd -> {cc_found}/"
                                f"{currency}) ")
                        elif o2 == "other":
                            stats["other"] += 1
                            reason = f"not_game:{i2.get('type')}"
                            if blacklist_add(blacklist, aid, i2.get("name"),
                                             reason):
                                stats["blacklisted"] += 1
                        else:
                            stats["failed"] += 1
                        continue
                    # 2) Bestaat nergens én matcht zijn naam (uit de app-lijst)
                    #    een game in de master? Dan is het een legacy-
                    #    duplicaat zonder eigen pagina (bv. Max Payne
                    #    201330 -> 12140) -> duplicates.jsonl i.p.v. blacklist.
                    canon = title_appid.get(title) if title else None
                    if canon is not None:
                        stats["duplicate"] += 1
                        dup_record = {"appid": aid, "name": name,
                                      "duplicate_of": canon,
                                      "reason": "no_store_page"}
                        if append_duplicates(data_dir, [dup_record],
                                             dup_seen):
                            stats["dup_saved"] += 1
                        continue
                    # 3) Echt verwijderd/verdwenen -> blacklist.
                    if blacklist_add(blacklist, aid, name, "no_store_page"):
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
        if stats["us_blocked"]:
            save_us_region_blocked(data_dir, us_blocked)

    remaining_new = (len(new_ids) - stats["added"] - stats["blacklisted"]
                     - stats["duplicate"])
    print("\n=== Samenvatting ===")
    print(f"Appids verwerkt deze run     : {processed}")
    print(f"Requests (store-API)         : {stats['requests']}")
    print(f"Nieuwe games toegevoegd      : {stats['added']}   "
          f"(waarvan us_region_blocked: {stats['us_blocked']})")
    print(f"Totaal in games.jsonl        : {len(known_appids)}")
    print(f"Duplicaten (duplicates.jsonl): {stats['duplicate']}   "
          f"({stats['dup_saved']} nieuw bewaard; de rest stond er al in)")
    print(f"In de blacklist gezet        : {stats['blacklisted']}   "
          f"(niet-game {stats['other']} + no_store_page "
          f"{stats['skipped']})")
    print(f"Failed (netwerk/429)         : {stats['failed']}   "
          "(volgende run probeert ze vanzelf opnieuw)")
    if remaining_new > 0:
        print(f"\n> {remaining_new} appids blijven 'nieuw'; draai het script "
              "opnieuw voor de volgende.")
    # Machine-leesbare status voor de GitHub Action: 'complete' = alle
    # appids verwerkt, 'partial' = netjes gestopt vóór alles klaar was
    # (tijdsbudget/Ctrl+C/limit).
    print("RUN_STATUS=complete" if remaining_new <= 0
          else "RUN_STATUS=partial")


if __name__ == "__main__":
    main()
