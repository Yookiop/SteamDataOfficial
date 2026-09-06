#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - extra-info-script (was tijdlijn/player_history):
herhaald per game pollen
=====================================================================

Zusterscript van fetch_games_initial.py. Waar dat script elke game EEN keer
ophaalt (data/games.jsonl - de basis: elke game 1x, ZONDER appid_amount),
schrijft DIT script per run een EXTRA regel per game naar
data/games_extra_info.jsonl (hernoemd van player_history.jsonl): een
doorlopende reeks momentopnamen per game, genummerd "<appid>_1", "_2",
"_3", ... De nummering is per game binnen games_extra_info zelf: de eerste
regel van een game is <appid>_1 (de master/basis telt dus NIET meer mee).

De basis (data/games.jsonl, 1 record per game) wordt bij elke run
automatisch bijgewerkt met de LAATST BEKENDE review-samenvatting per game
(die uit de momentopnamen van games_extra_info); zo heeft games.json ook de
review_*-velden. Zonder netwerk kan dat ook los: --sync-reviews.

Per game per run (games met de MEESTE spelers eerst, daarna aflopend):
  1. Dezelfde slanke velden als fetch_games_initial (vers opgehaald uit de
     officiele Storefront API, cc=us + l=english -> USD; us_region_blocked-
     games die in de US-store niet te koop zijn worden via hun eigen regio
     opgehaald -> de valuta is dan die van die regio, bv. EUR voor nl).
  2. last_seen_player_count (momentopname, keyless via
     ISteamUserStats/GetNumberOfCurrentPlayers; None bij geen publieke data).
  3. De review-samenvatting (officiele Review API
     store.steampowered.com/appreviews/{appid}, keyless): review_score,
     review_score_desc (bv. 'Very Positive', 'Mixed'), review_positive,
     review_negative en review_total - met language=all + purchase_type=all
     + filter=all (zelfde totaalbeeld als de storepagina; zie
     fetch_games_initial.py). Zo zie je per game ook hoe de beoordeling
     over de tijd verandert (de review-samenvatting komt altijd mee).
  4. appid_amount = "<appid>_<n>" met doorlopende nummering per game binnen
     games_extra_info.jsonl: de eerste regel van een game is <appid>_1,
     daarna <appid>_2, _3, ...
  5. refresh_count = 1 op ELKE regel: zo kun je later per game het aantal
     vernieuwingen optellen (som van refresh_count per appid).
  6. DataUpdatedAt = de datumtijd (UTC, yyyy-mm-ddThh:mm:ss+00:00) op het
     moment dat deze regel wordt weggeschreven: zo zie je later per game
     wanneer elke momentopname is gemaakt (handig voor het verloop van
     spelersaantal en reviews over de tijd).

Volgorde (zonder --random): gesorteerd op het LAATST bekende spelersaantal
per game (eerst de laatste games_extra_info-regel van dat appid, anders de
masterwaarde uit games.jsonl), aflopend; bij gelijk -> oplopend appid. Zo
vernieuwt een run met --limit altijd eerst de populairste games. Met
--random wordt de volgorde elke run opnieuw willekeurig gemixt (uniforme
steekproef): dan blijven niet steeds dezelfde top-games vooraan staan en
wordt ook een game die ver achteraan staat (bv. op plek 50.000) regelmatig
bijgewerkt.

Met --player-limit <N> wordt de selectie gefilterd op games met WEINIG
spelers (bv. voor een video over de minst gespeelde games): per game wordt
het GEMIDDELDE last_seen_player_count over zijn eigen
games_extra_info-regels berekend (regels zonder int-waarde tellen niet
mee). Heeft een game nog geen extra-info-regels, dan telt de master-snapshot
(last_seen_player_count in games.jsonl) als enige steekproef. Alleen games
met een gemiddelde ONDER N worden deze run bijgewerkt; games zonder enige
spelerswaarde (geen publieke Steam-data) worden overgeslagen (null is
onbekend, geen 0). De volgorde wordt dan oplopend op dat gemiddelde (minste
spelers eerst), zodat een run met --limit altijd de laagste games als eerste
ververst; combineerbaar met --random (dan wordt de gefilterde selectie
gemixt).

Naast --player-limit zijn er twee vlaggen om op andere eigenschappen te
filteren (alles combineert met EN):
- --is_free_true: alleen GRATIS games bijwerken (is_free == true in de
  master). Bewust GEEN --is_free_false: het merendeel van de games is niet
  gratis, dus dat filter zou bijna de hele catalogus selecteren.
- --genre_<naam>: alleen games bijwerken waarbij dat genre in de genres-
  lijst van de game voorkomt (bv. --genre_sports of --genre_indie). Een
  game met meerdere genres telt mee zodra het genre er een van is. Spaties,
  hoofdletters en '&' doen er niet toe: --genre_action matcht 'Action',
  --genre_massively_multiplayer matcht 'Massively Multiplayer' en
  --genre_design_illustration matcht 'Design & Illustration'. Meerdere
  --genre_*-vlaggen = OF (een game met een van de opgegeven genres telt).
  Deze vlaggen zijn dynamisch (argparse kent geen vaste genrenamen): ze
  worden vóór het parsen uit de commandoregel gehaald (split_genre_flags)
  en genormaliseerd met genre_key (lowercase + niet-alfanumeriek weg).

Anders dan fetch_games_initial wordt er dus NIET geskipt op bekende appids,
niet gededuped en niet geblacklist: elke run telt. Geen API key nodig (dit
script haalt alleen de (keyless) store-, spelersaantal- en review-data op
van games die al in de master staan).

Gebruik:
    python fetch_new_game_info.py             # alle games pollen (meeste spelers eerst)
    python fetch_new_game_info.py --limit 500 # max. 500 games deze run (populairste eerst)
    python fetch_new_game_info.py --limit 3500 --random # max. 500 games deze run (populairste eerst)
    python fetch_new_game_info.py --random    # elke run een willekeurige selectie appids,
                                              # zodat niet steeds dezelfde top-games worden
                                              # bijgewerkt (combineerbaar met --limit)
    python fetch_new_game_info.py --player-limit 50  # alleen games met een GEMIDDELD aantal
                                                     # spelers < 50 bijwerken (video over de
                                                     # minst gespeelde games; combineerbaar
                                                     # met --limit en --random)
    python fetch_new_game_info.py --is_free_true     # alleen GRATIS games bijwerken
    python fetch_new_game_info.py --is_free_true --player-limit 50 #gratis games en filter op max 50 gemiddelde spelers
    python fetch_new_game_info.py --genre_sports     # alleen games met genre Sports bijwerken
                                                     # (elke dynamische --genre_<naam> werkt;
                                                     # meerdere --genre_*-vlaggen = OF)
    python fetch_new_game_info.py --genre_sports --player-limit 50 #sports genre games met gemiddeld max 50 spelers
    python fetch_new_game_info.py --max-duration-minutes 30 # netjes stoppen ~5 min vóór
                                                  # de step-timeout (GitHub Action)
    python fetch_new_game_info.py --sync-reviews  # basis (games.jsonl) bijwerken met de
                                                  # laatst bekende reviews en stoppen
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
from datetime import datetime, timezone

# Bestandsrotatie: datasets groeien door in delen van max ~90 MB
# (games.jsonl, games_extra_info.jsonl, ...). Al het lezen gaat via
# iter_lines (delen samengevoegd), schrijven via RotatingAppend/
# rewrite_rotated - zie data_rotation.py.
from data_rotation import (existing_parts, iter_lines, rewrite_rotated,
                           RotatingAppend)

APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
PLAYER_COUNT_URL = ("https://api.steampowered.com/ISteamUserStats/"
                    "GetNumberOfCurrentPlayers/v1/")
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DEFAULT_DELAY = 0.4
DEFAULT_JITTER = 0.1
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 6
DEFAULT_MAX_REQUESTS = 10000

# Marge (minuten) die het script aanhoudt vóór het opgegeven duurbudget
# (--max-duration-minutes): netjes stoppen vóór de GitHub step-timeout de
# run keihard zou afkappen.
GRACEFUL_STOP_MARGIN_MINUTES = 5

stop_requested = False

# Review-velden: staan in games_extra_info.jsonl als momentopname, en in
# de basis games.jsonl als de LAATST BEKENDE waarde (1 record per game).
REVIEW_KEYS = ("review_score", "review_score_desc", "review_positive",
               "review_negative", "review_total")


def on_signal(signum, frame):  # pragma: no cover - alleen interactief
    global stop_requested
    stop_requested = True
    print("\n> Stop aangevraagd (Ctrl+C). Voortgang wordt netjes bewaard...")


def now_iso():
    """Huidige datumtijd in UTC (bv. 2026-09-05T18:34:12+00:00). Bewust
    UTC: lokale runs en de GitHub Action (die in UTC draait) krijgen zo
    dezelfde tijdsbasis voor DataUpdatedAt."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Records (zelfde slanke formaat als fetch_games_initial)                     #
# --------------------------------------------------------------------------- #
_MONTH_NUMS = {}
for _i, _m in enumerate(
        ("January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"),
        start=1):
    _MONTH_NUMS[_m.lower()] = _i
    _MONTH_NUMS[_m.lower()[:3]] = _i


def iso_release_date(date_str):
    """Zet een releasedatum om naar 'yyyy-mm-dd'. Retourneert '' als de
    datum ontbreekt of niet herkend wordt."""
    if not date_str:
        return ""
    s = date_str.strip()
    m = re.fullmatch(r"(\d{4})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", s)
    if m and m.group(2).lower() in _MONTH_NUMS:
        return f"{int(m.group(3)):04d}-{_MONTH_NUMS[m.group(2).lower()]:02d}-" \
               f"{int(m.group(1)):02d}"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
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
    """Zelfde slanke velden als fetch_games_initial (USD door cc=us)."""
    price = data.get("price_overview")
    if isinstance(price, dict):
        price = dict(price)
        # Bij GEEN korting laat Steam initial_formatted leeg; zet hem dan
        # gelijk aan final_formatted (zelfde bedrag, geen korting).
        if not price.get("discount_percent") and price.get("final_formatted"):
            price["initial_formatted"] = price["final_formatted"]
    else:
        price = {}
    release = data.get("release_date") or {}
    rel_str = release.get("date") or ""
    rel_iso = iso_release_date(rel_str)
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


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def wait_chunked(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not stop_requested:
        time.sleep(min(0.5, end - time.monotonic()))


def backoff_seconds(attempt, code):
    base = 30.0 if code == 429 else 5.0
    cap = 300.0 if code == 429 else 120.0
    return min(cap, base * (2 ** (attempt - 1)))


def random_jitter(jitter):
    return random.uniform(0, jitter)


def genre_key(name):
    """Normaliseer een genrenaam voor vergelijking: lowercase + alle niet-
    alfanumerieke tekens weg (spaties, '&', underscores, ...). Zo matchen
    --genre_action en 'Action' elkaar, --genre_massively_multiplayer matcht
    'Massively Multiplayer' en --genre_design_illustration matcht 'Design &
    Illustration' (zonder dat '&' in de shell gequote hoeft te worden)."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def split_genre_flags(argv):
    """Argparse kent geen dynamische opties: haal alle --genre_<naam>-
    vlaggen uit de commandoregel vóór het parsen (anders faalt parse_args op
    onbekende opties). Retourneert (overige_argv, genre_vlaggen) met de
    vlaggen in de oorspronkelijke volgorde; lege namen (--genre_) worden
    genegeerd."""
    remaining, flags = [], []
    for arg in argv:
        if arg.startswith("--genre_") and len(arg) > len("--genre_"):
            flags.append(arg[len("--genre_"):])
        else:
            remaining.append(arg)
    return remaining, flags


def load_us_region_blocked(data_dir):
    """Lees us_region_blocked.json (uit fetch_games_initial): games die in
    de US-store niet te koop zijn maar elders wel, opgenomen in de master
    via hun eigen regio. Formaat: {appid: {name, cc, currency, added}} met
    ints als sleutels. Deze games pollen we via hun eigen cc (bv. nl ->
    EUR) in plaats van cc=us."""
    path = os.path.join(data_dir, "us_region_blocked.json")
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


def fetch_store_record(appid, args, stats, cc="us"):
    """Storefront appdetails voor EEN appid met retry/backoff. Standaard
    cc=us (USD), maar voor us_region_blocked-games geeft de caller hun eigen
    regio mee (bv. cc='nl' -> EUR). Retourneert een slank record, of None
    als de game (nu) geen storepagina heeft of het request na alle pogingen
    mislukte."""
    for attempt in range(1, args.max_retries + 1):
        if stop_requested:
            return None
        stats["requests"] += 1
        try:
            url = APP_DETAILS_URL + "?" + urllib.parse.urlencode(
                {"appids": appid, "cc": cc, "l": "english"})
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                body = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
            body = b""
        except (urllib.error.URLError, OSError, ValueError):
            if attempt < args.max_retries:
                wait_chunked(backoff_seconds(attempt, 0))
                continue
            return None

        if code == 200:
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except ValueError:
                if attempt < args.max_retries:
                    wait_chunked(backoff_seconds(attempt, 0))
                    continue
                return None
            # 200-response zonder dit appid = geen normale response -> retry
            if not isinstance(payload, dict) or str(appid) not in payload:
                if attempt < args.max_retries:
                    wait_chunked(backoff_seconds(attempt, 0))
                    continue
                return None
            entry = payload.get(str(appid)) or {}
            if not entry.get("success"):
                return None           # geen storepagina (meer)
            data = entry.get("data") or {}
            if data.get("type") == "game":
                return slim_record(appid, data)
            return None               # geen game (meer) -> niet in de extra-info
        elif code == 429:
            if attempt >= args.max_retries:
                return None
            if attempt == 1:
                print(f"! HTTP 429 (throttled) bij appid {appid} - "
                      f"pauze {backoff_seconds(attempt, 429):.0f}s...")
            wait_chunked(backoff_seconds(attempt, 429))
        elif 500 <= code < 600:
            if attempt >= args.max_retries:
                return None
            wait_chunked(backoff_seconds(attempt, code))
        else:
            return None
    return None


def fetch_player_count(appid, timeout=30):
    """Huidig aantal spelers (officieel, keyless). Retourneert int of None."""
    url = PLAYER_COUNT_URL + "?" + urllib.parse.urlencode(
        {"appid": appid, "format": "json"})
    for attempt in (1, 2):
        if stop_requested:
            return None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(
                    resp.read().decode("utf-8", errors="replace"))
            pc = ((payload.get("response") or {}).get("player_count"))
            return pc if isinstance(pc, int) else None
        except Exception:  # noqa: BLE001
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
    lege summary (desc 'No user reviews', alles 0)."""
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
        except Exception:  # noqa: BLE001
            if attempt == 1:
                wait_chunked(2.0)
    return None


# --------------------------------------------------------------------------- #
#  Master + history lezen                                                     #
# --------------------------------------------------------------------------- #
def load_master(path):
    """Lees de unieke games uit games.jsonl* (master; alle delen
    samengevoegd). Retourneert een dict {appid: record} (alleen regels met
    een appid en type == game)."""
    master = {}
    for line in iter_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        try:
            aid = int(r.get("appid"))
        except (TypeError, ValueError):
            continue
        master[aid] = r
    return master


def load_extra_info(path):
    """Lees games_extra_info*.jsonl (alle delen samengevoegd, in
    volgorde). Retourneert (counts, last_players, avg_players,
    latest_reviews): per appid het aantal regels, het laatste bekende
    spelersaantal, het GEMIDDELDE spelersaantal (over alle regels van die
    game met een int-waarde; voor --player-limit) én de
    review-samenvatting van de LAATSTE regel over alle delen heen (die
    wint - om games.jsonl bij te werken met de laatst bekende data)."""
    counts, last_players, latest_reviews = {}, {}, {}
    player_sums, player_samples = {}, {}
    for line in iter_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            aid = int(r.get("appid"))
        except (TypeError, ValueError):
            continue
        counts[aid] = counts.get(aid, 0) + 1
        pc = r.get("last_seen_player_count")
        if isinstance(pc, int):
            last_players[aid] = pc
            player_sums[aid] = player_sums.get(aid, 0) + pc
            player_samples[aid] = player_samples.get(aid, 0) + 1
        if any(r.get(k) is not None for k in REVIEW_KEYS):
            latest_reviews[aid] = {k: r.get(k) for k in REVIEW_KEYS}
    # Gemiddelde over de regels met een int-waarde; regels met null (geen
    # publieke data) tellen niet mee (null is onbekend, geen 0).
    avg_players = {aid: player_sums[aid] / player_samples[aid]
                   for aid in player_samples}
    return counts, last_players, avg_players, latest_reviews


def apply_latest_reviews(master, latest_reviews):
    """Zet de laatst bekende review-samenvatting (uit games_extra_info) in
    de basisrecords van games.jsonl (1 record per game). Retourneert het
    aantal games dat is bijgewerkt."""
    changed = 0
    for aid, rev in latest_reviews.items():
        rec = master.get(aid)
        if rec is None or not rev:
            continue
        if any(rec.get(k) != rev.get(k) for k in REVIEW_KEYS):
            for k in REVIEW_KEYS:
                rec[k] = rev.get(k)
            changed += 1
    return changed


def write_master(path, master):
    """Schrijf de master (dict, volgorde bewaard) terug naar games.jsonl*.
    Volledige herschrijving mét rotatie: oude delen worden eerst verwijderd
    en bij >90 MB gaat het verder in games_2.jsonl enz. (rewrite_rotated)."""
    rewrite_rotated(
        path,
        (json.dumps(rec, ensure_ascii=False) + "\n"
         for rec in master.values()))


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
def main(argv=None):
    # --genre_<naam>-vlaggen zijn dynamisch (argparse kent geen vaste lijst
    # van genrenamen): haal ze er vóór het parsen uit, zie split_genre_flags.
    argv, genre_flags = split_genre_flags(
        sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        description="EXTRA INFO: per run een extra regel per game toevoegen "
                    "aan games_extra_info.jsonl (was player_history.jsonl: "
                    "zelfde velden + last_seen_player_count + doorlopende "
                    "appid_amount vanaf _1 per game). Geen API key nodig.",
        epilog="Selectievlaggen (combineerbaar; alles geldt tegelijk):\n"
               "  --player-limit <N>   alleen games met GEMIDDELD < N spelers\n"
               "  --is_free_true       alleen GRATIS games (is_free=true)\n"
               "  --genre_<naam>       dynamisch: bv. --genre_sports,\n"
               "                       --genre_indie of --genre_rpg: alleen\n"
               "                       games waar dat genre in voorkomt\n"
               "                       (spaties/& mogen ontbreken: bv.\n"
               "                       --genre_massively_multiplayer matcht\n"
               "                       'Massively Multiplayer'); meerdere\n"
               "                       --genre_*-vlaggen = OF (een game met\n"
               "                       een van de genres telt)")
    p.add_argument("--data-dir", default="data",
                   help="map voor master + extra-info (default: data)")
    p.add_argument("--master", default=None,
                   help="unieke masterlijst (default: <data-dir>/games.jsonl)")
    p.add_argument("--extra", default=None,
                   help="extra-info-output (default: "
                        "<data-dir>/games_extra_info.jsonl)")
    p.add_argument("--limit", type=int, default=None,
                   help="max. aantal games dat deze run wordt verwerkt "
                        "(1 request per game; de rest komt de volgende run "
                        "aan de beurt; default: alle games)")
    p.add_argument("--report-only", action="store_true",
                   help="alleen tonen wat er zou worden toegevoegd")
    p.add_argument("--sync-reviews", action="store_true",
                   help="alleen de basis (games.jsonl) bijwerken met de "
                        "laatst bekende review-samenvatting uit "
                        "games_extra_info.jsonl en stoppen (geen netwerk)")
    p.add_argument("--random", action="store_true",
                   help="appids in willekeurige volgorde verwerken i.p.v. "
                        "meeste spelers eerst (--limit blijft werken): zo "
                        "worden niet steeds dezelfde populairste games "
                        "bijgewerkt, maar ook ver achteraan staande games")
    p.add_argument("--player-limit", type=int, default=None,
                   help="alleen games bijwerken met een GEMIDDELD aantal "
                        "spelers ONDER deze limiet (voor een video over de "
                        "minst gespeelde games). Het gemiddelde komt uit de "
                        "eigen games_extra_info-regels van de game (regels "
                        "zonder waarde tellen niet mee); zonder historie "
                        "telt de master-snapshot uit games.jsonl mee. Games "
                        "zonder enige spelersdata worden overgeslagen. "
                        "Volgorde wordt dan minste spelers eerst "
                        "(combineerbaar met --limit en --random)")
    p.add_argument("--is_free_true", action="store_true",
                   help="alleen GRATIS games bijwerken (is_free == true in "
                        "de master). Bewust geen --is_free_false: het "
                        "merendeel van de games is niet gratis, dus dat "
                        "filter zou bijna de hele catalogus selecteren. "
                        "Combineerbaar met --player-limit, --genre_*, "
                        "--limit en --random")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"seconden rust tussen twee requests "
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
                   help=f"veiligheidslimiet op het aantal requests per run "
                        f"(default: {DEFAULT_MAX_REQUESTS})")
    p.add_argument("--max-duration-minutes", type=float, default=None,
                   help="max. aantal minuten dat deze run mag draaien "
                        "(zet dit gelijk aan de step-timeout in de GitHub "
                        "Action). Het script stopt zelf NETJES "
                        f"{GRACEFUL_STOP_MARGIN_MINUTES} minuten vóór die "
                        "grens en rondt netjes af (laatste momentopnamen "
                        "weggeschreven + basis-reviews bijgewerkt), i.p.v. "
                        "keihard afgekapt te worden. Zonder deze optie "
                        "draait het onbeperkt.")
    args = p.parse_args(argv)
    # Genre-vlaggen: bewaard voor de meldingen (oorspronkelijke
    # schrijfwijze, zonder dubbelen) en genormaliseerd voor de matching.
    args.genre_flags = []
    args.genres = set()
    for _f in genre_flags:
        if _f not in args.genre_flags:
            args.genre_flags.append(_f)
        _k = genre_key(_f)
        if _k:
            args.genres.add(_k)

    data_dir = os.path.abspath(args.data_dir)
    master_path = (os.path.abspath(args.master) if args.master
                   else os.path.join(data_dir, "games.jsonl"))
    extra_path = (os.path.abspath(args.extra) if args.extra
                  else os.path.join(data_dir, "games_extra_info.jsonl"))

    master = load_master(master_path)
    if not master:
        print(f"! Geen games gevonden in {master_path}. Draai eerst "
              "fetch_games_initial.py om de masterlijst op te bouwen.")
        sys.exit(1)
    extra_counts, extra_last, avg_players, latest_reviews = \
        load_extra_info(extra_path)
    us_blocked = load_us_region_blocked(data_dir)

    # --sync-reviews: de basis (games.jsonl, 1 record per game) bijwerken
    # met de laatst bekende reviews uit de momentopnamen en stoppen.
    if args.sync_reviews:
        changed = apply_latest_reviews(master, latest_reviews)
        if changed:
            write_master(master_path, master)
        print(f"> Basis bijgewerkt met laatst bekende review-samenvatting "
              f"voor {changed} games ({master_path})")
        return

    # Doorlopende nummering PER GAME binnen games_extra_info.jsonl: de
    # eerste regel van een game is <appid>_1, daarna _2, _3, ... Het aantal
    # komt uit het aantal eerdere regels van dat appid (de master/basis
    # telt niet meer mee).
    def next_amount(aid):
        return f"{aid}_{extra_counts.get(aid, 0) + 1}"

    # Volgorde: laatste bekende spelersaantal per game (eerst de laatste
    # games_extra_info-regel van dat appid, anders de masterwaarde),
    # aflopend -> de populairste games eerst.
    def order_score(aid):
        val = extra_last.get(aid)
        if val is None:
            val = master[aid].get("last_seen_player_count")
        return val if isinstance(val, int) else -1

    ordered_ids = sorted(master,
                         key=lambda a: (-order_score(a), a))

    # Selectiefilters. Alles combineert met EN; meerdere --genre_*-vlaggen
    # zijn OF binnen de genres. Beschikbare filters:
    #   --player-limit <N> -> alleen games met een GEMIDDELD aantal spelers
    #      onder N. Het gemiddelde komt uit de eigen games_extra_info-regels
    #      van de game (regels met null tellen niet mee); zonder historie
    #      telt de master-snapshot (last_seen_player_count in games.jsonl)
    #      als enige steekproef. Games zonder ENIGE spelerswaarde (geen
    #      publieke Steam-data) worden overgeslagen: null is onbekend, geen 0.
    #   --is_free_true      -> alleen gratis games (is_free == true in de
    #      master). Bewust geen --is_free_false: het merendeel van de games
    #      is niet gratis, dus dat filter zou bijna alles selecteren.
    #   --genre_<naam>      -> alleen games waarbij dat genre voorkomt in de
    #      genres-lijst van de game (een game met meerdere genres telt mee
    #      zodra het genre er een van is).
    def reference_players(aid):
        val = avg_players.get(aid)
        if val is not None:
            return val
        mval = master[aid].get("last_seen_player_count")
        return float(mval) if isinstance(mval, int) else None

    def matches_selection(aid):
        rec = master[aid]
        if args.player_limit is not None:
            val = reference_players(aid)
            if val is None or val >= args.player_limit:
                return False
        if args.is_free_true and not rec.get("is_free"):
            return False
        if args.genres:
            game_keys = {genre_key(g) for g in rec.get("genres") or []}
            if not (game_keys & args.genres):
                return False
        return True

    selected_ids = [aid for aid in ordered_ids if matches_selection(aid)]

    # Aantallen per filter (voor de meldingen): elke filter telt
    # onafhankelijk van de andere, zodat elke melding zijn eigen selectie
    # toont; de combinatie staat in de samenvattingsregel hieronder.
    under_count = unknown_count = None
    if args.player_limit is not None:
        under_count, unknown_count = 0, 0
        for aid in ordered_ids:
            val = reference_players(aid)
            if val is None:
                unknown_count += 1
            elif val < args.player_limit:
                under_count += 1
    free_count = None
    if args.is_free_true:
        free_count = sum(1 for rec in master.values() if rec.get("is_free"))
    genre_counts = []
    for _f in args.genre_flags:
        _key = genre_key(_f)
        genre_counts.append(
            (_f, sum(1 for rec in master.values()
                     if any(genre_key(g) == _key
                            for g in rec.get("genres") or []))))

    # Volgorde: zonder --random meeste spelers eerst (uit ordered_ids), met
    # --player-limit minste spelers eerst (oplopend op gemiddelde: met
    # --limit pakt de run dan altijd de laagste games als eerste), met
    # --random willekeurig gemixt (uniforme steekproef: zo blijven niet
    # steeds dezelfde top-games vooraan staan en wordt ook iets op plek
    # 50.000 regelmatig bijgewerkt).
    if args.player_limit is not None:
        selected_ids.sort(key=lambda a: (reference_players(a), a))
    if args.random:
        random.shuffle(selected_ids)

    print("\n=== SteamDataOfficial: extra info per game ===")
    print(f"Games in de master          : {len(master)}")
    n_parts = len(existing_parts(extra_path))
    print(f"Al in games_extra_info*.jsonl ({n_parts} deel"
          + ("en" if n_parts != 1 else "") + "): "
          f"{sum(extra_counts.values())} regels")
    if args.random:
        print("Volgorde                    : willekeurig (--random)")
    elif args.player_limit is not None:
        print("Volgorde                    : minste spelers eerst "
              "(--player-limit)")
    else:
        print("Volgorde                    : meeste spelers eerst")
    if args.player_limit is not None:
        print(f"> --player-limit {args.player_limit}: alleen games met een "
              f"gemiddeld spelersaantal < {args.player_limit} worden "
              f"bijgewerkt ({under_count} games; {unknown_count} zonder "
              "enige spelersdata overgeslagen).")
    if args.is_free_true:
        print(f"> --is_free_true: alleen GRATIS games worden bijgewerkt "
              f"({free_count} games).")
    for _f, _cnt in genre_counts:
        print(f"> --genre_{_f}: alleen games met genre '{_f}' worden "
              f"bijgewerkt ({_cnt} games).")
    n_filters = sum((args.player_limit is not None,
                     args.is_free_true, bool(args.genres)))
    # De gezamenlijke selectie tonen zodra er meerdere filtervlaggen tegelijk
    # actief zijn (bv. --genre_a --genre_b = OF, of --is_free_true +
    # --genre_x = EN): dan is het totaal niet uit een enkele melding af te
    # lezen. Bij één filter zegt de eigen melding al hoeveel games dat zijn.
    if n_filters and (n_filters > 1 or len(args.genre_flags) > 1):
        print(f"> Samen na alle filters: {len(selected_ids)} games.")
    if n_filters and not selected_ids:
        print("\n> Geen games voldoen aan de actieve selectie. Draai het "
              "script zonder filters of verruim ze.")
        return

    lim = args.limit or None
    selected = selected_ids
    if lim is not None and len(selected_ids) > lim:
        selected = selected_ids[:lim]
        print(f"> Beperkt tot {lim} games deze run (--limit); de rest komt "
              "de volgende run aan de beurt.")

    if args.report_only:
        print("> Zou toevoegen (eerste 30):")
        for aid in selected[:30]:
            print(f"   + {aid}  {master[aid].get('name')}  ->  "
                  f"{next_amount(aid)}")
        if len(selected) > 30:
            print(f"   ... en nog {len(selected) - 30} meer")
        print("> --report-only: niets opgehaald/toegevoegd.")
        return

    # Basis (games.jsonl, 1 record per game) eerst bijwerken met de laatst
    # bekende reviews uit games_extra_info (geen extra requests); games die
    # deze run worden verwerkt krijgen daarna de verse waarde van nu.
    master_dirty = apply_latest_reviews(master, latest_reviews) > 0
    stats = {"requests": 0, "added": 0, "skipped": 0}
    os.makedirs(data_dir, exist_ok=True)
    # Schrijven met rotatie: bij ~90 MB wordt het deel gelockt en gaat de
    # rest naar games_extra_info_2.jsonl, _3.jsonl, ... (max _5).
    extra_file = RotatingAppend(extra_path, log=print)
    processed = 0

    # Duurbudget (--max-duration-minutes): netjes stoppen enkele minuten
    # vóór de opgegeven grens, zodat de GitHub step-timeout de run niet
    # keihard afkapt.
    t0 = time.monotonic()
    if args.max_duration_minutes:
        deadline = max(0.0, args.max_duration_minutes
                       - GRACEFUL_STOP_MARGIN_MINUTES) * 60.0
    else:
        deadline = None
    try:
        for aid in selected:
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

            cc = "us"
            _blk = us_blocked.get(aid)
            if _blk and _blk.get("cc"):
                cc = _blk["cc"]
            record = fetch_store_record(aid, args, stats, cc=cc)
            if stop_requested:
                break
            if record is None:
                # geen (game-)storepagina meer, of request definitief mislukt
                stats["skipped"] += 1
            else:
                pc = fetch_player_count(aid, args.timeout)
                stats["requests"] += 1
                record["last_seen_player_count"] = pc
                # Review-samenvatting ALTIJD ophalen (geen --no-reviews
                # meer): reviews moeten zo compleet mogelijk zijn.
                rev = fetch_review_summary(aid, args.timeout)
                stats["requests"] += 1
                for key in ("review_score", "review_score_desc",
                            "review_positive", "review_negative",
                            "review_total"):
                    record[key] = (rev or {}).get(key)
                record["appid_amount"] = next_amount(aid)
                record["refresh_count"] = 1   # elke extra-info-regel = 1 vernieuwing
                # Tijdstip van schrijven: zo zie je later per game wanneer
                # elke momentopname is gemaakt (verloop over de tijd).
                record["DataUpdatedAt"] = now_iso()
                extra_file.write_line(
                    json.dumps(record, ensure_ascii=False))
                stats["added"] += 1
                if rev is not None:
                    # Dit is nu de laatst bekende review-samenvatting -> ook
                    # de basis (games.jsonl) van deze game bijwerken.
                    rec = master.get(aid)
                    if rec is not None:
                        for key in REVIEW_KEYS:
                            if rec.get(key) != record[key]:
                                rec[key] = record[key]
                                master_dirty = True
                print(f"   + {aid}  {record.get('name')}  ->  "
                      f"{record['appid_amount']}  (players: {pc}, reviews: "
                      f"{record.get('review_score_desc')})")

            processed += 1
            if processed % 25 == 0:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                      f"verwerkt={processed}/{len(selected)}  "
                      f"requests={stats['requests']}  "
                      f"toegevoegd={stats['added']}")

            if not stop_requested:
                wait_chunked(args.delay + random_jitter(args.jitter))
    finally:
        extra_file.close()

    if master_dirty:
        write_master(master_path, master)
        print("> Basis (games.jsonl) bijgewerkt met de laatst bekende "
              "review-samenvatting.")

    print("\n=== Samenvatting ===")
    print(f"Games verwerkt deze run     : {processed}")
    print(f"Extra-info-regels toegevoegd: {stats['added']}")
    print(f"Overgeslagen (geen pagina)  : {stats['skipped']}")
    if len(selected) > processed:
        print(f"\n> {len(selected) - processed} games nog niet aan bod; draai "
              "het script opnieuw voor de volgende.")


if __name__ == "__main__":
    main()
