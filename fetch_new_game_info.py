#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - TIJDLIJN-script: herhaald per game pollen
=============================================================

Zusterscript van fetch_games_initial.py. Waar dat script elke game EEN keer
ophaalt (data/games.jsonl, de unieke masterlijst), voegt DIT script per run
een EXTRA regel per game toe aan data/player_history.jsonl - zodat je per
game een tijdlijn krijgt van last_seen_player_count (bv. 10_1 in de master,
dan 10_2, 10_3, ... in de history).

Per game per run (games met de MEESTE spelers eerst, daarna aflopend):
  1. Dezelfde slanke velden als fetch_games_initial (vers opgehaald uit de
     officiele Storefront API, cc=us + l=english -> USD).
  2. last_seen_player_count (momentopname, keyless via
     ISteamUserStats/GetNumberOfCurrentPlayers; None bij geen publieke data).
  3. appid_amount = "<appid>_<n>" met doorlopende nummering INCLUSIEF de
     initiele opname: de masterrij is <appid>_1, de eerste history-regel
     wordt <appid>_2, daarna <appid>_3, ... Het aantal komt uit het aantal
     eerdere regels van dat appid in player_history.jsonl.
  4. refresh_count = 1 op ELKE history-regel: zo kun je later per game het
     aantal vernieuwingen optellen (som van refresh_count per appid).

Volgorde: gesorteerd op het LAATST bekende spelersaantal per game (eerst de
laatste history-regel van dat appid, anders de masterwaarde uit games.jsonl),
aflopend; bij gelijk -> oplopend appid. Zo vernieuwt een run met --limit
altijd eerst de populairste games.

Anders dan fetch_games_initial wordt er dus NIET geskipt op bekende appids,
 niet gededuped en niet geblacklist: elke run telt. Geen API key nodig (dit
script haalt alleen de (keyless) store- en spelersaantal-data op van games
die al in de master staan).

Gebruik:
    python fetch_new_game_info.py             # alle games pollen (meeste spelers eerst)
    python fetch_new_game_info.py --limit 500 # max. 500 games deze run (populairste eerst)
    python fetch_new_game_info.py --report-only   # alleen tonen wat er zou komen
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

APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
PLAYER_COUNT_URL = ("https://api.steampowered.com/ISteamUserStats/"
                    "GetNumberOfCurrentPlayers/v1/")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DEFAULT_DELAY = 0.4
DEFAULT_JITTER = 0.1
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 6
DEFAULT_MAX_REQUESTS = 10000

stop_requested = False


def on_signal(signum, frame):  # pragma: no cover - alleen interactief
    global stop_requested
    stop_requested = True
    print("\n> Stop aangevraagd (Ctrl+C). Voortgang wordt netjes bewaard...")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


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
        "release_date": rel_str,
        "release_date_format": iso_release_date(rel_str),
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


def fetch_store_record(appid, args, stats):
    """Storefront appdetails voor EEN appid met retry/backoff.
    Retourneert een slank record, of None als de game (nu) geen storepagina
    is of het request na alle pogingen mislukte."""
    for attempt in range(1, args.max_retries + 1):
        if stop_requested:
            return None
        stats["requests"] += 1
        try:
            url = APP_DETAILS_URL + "?" + urllib.parse.urlencode(
                {"appids": appid, "cc": "us", "l": "english"})
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
            return None               # geen game (meer) -> niet in de tijdlijn
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


# --------------------------------------------------------------------------- #
#  Master + history lezen                                                     #
# --------------------------------------------------------------------------- #
def load_master(path):
    """Lees de unieke games uit games.jsonl (master). Retourneert een dict
    {appid: record} (alleen regels met een appid en type == game)."""
    master = {}
    if not os.path.isfile(path):
        return master
    for line in open(path, encoding="utf-8"):
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


def load_history(path):
    """Lees player_history.jsonl. Retourneert (counts, last_players): per
    appid het aantal regels én het laatste bekende spelersaantal (de laatste
    regel wint)."""
    counts, last_players = {}, {}
    if not os.path.isfile(path):
        return counts, last_players
    for line in open(path, encoding="utf-8"):
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
    return counts, last_players


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(
        description="TIJDLIJN: per run een extra regel per game toevoegen aan "
                    "player_history.jsonl (zelfde velden + last_seen_player_"
                    "count + doorlopende appid_amount). Geen API key nodig.")
    p.add_argument("--data-dir", default="data",
                   help="map voor master + history (default: data)")
    p.add_argument("--master", default=None,
                   help="unieke masterlijst (default: <data-dir>/games.jsonl)")
    p.add_argument("--history", default=None,
                   help="tijdlijn-output (default: "
                        "<data-dir>/player_history.jsonl)")
    p.add_argument("--limit", type=int, default=None,
                   help="max. aantal games dat deze run wordt verwerkt "
                        "(1 request per game; de rest komt de volgende run "
                        "aan de beurt; default: alle games)")
    p.add_argument("--report-only", action="store_true",
                   help="alleen tonen wat er zou worden toegevoegd")
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
    args = p.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    master_path = (os.path.abspath(args.master) if args.master
                   else os.path.join(data_dir, "games.jsonl"))
    history_path = (os.path.abspath(args.history) if args.history
                    else os.path.join(data_dir, "player_history.jsonl"))

    master = load_master(master_path)
    if not master:
        print(f"! Geen games gevonden in {master_path}. Draai eerst "
              "fetch_games_initial.py om de masterlijst op te bouwen.")
        sys.exit(1)
    hist_counts, hist_last = load_history(history_path)

    # Doorlopende nummering INCLUSIEF de initiele opname: master = _1, dus de
    # eerstvolgende history-regel van een appid is _2, daarna _3, ...
    def next_amount(aid):
        return f"{aid}_{hist_counts.get(aid, 0) + 2}"

    # Volgorde: laatste bekende spelersaantal per game (eerst de laatste
    # history-regel van dat appid, anders de masterwaarde), aflopend -> de
    # populairste games eerst.
    def order_score(aid):
        val = hist_last.get(aid)
        if val is None:
            val = master[aid].get("last_seen_player_count")
        return val if isinstance(val, int) else -1

    ordered_ids = sorted(master,
                         key=lambda a: (-order_score(a), a))

    print("\n=== SteamDataOfficial: tijdlijn (player count per game) ===")
    print(f"Games in de master          : {len(master)}")
    print(f"Al in player_history.jsonl  : {sum(hist_counts.values())} "
          f"regels")
    print("Volgorde                    : meeste spelers eerst")

    lim = args.limit or None
    selected = ordered_ids
    if lim is not None and len(ordered_ids) > lim:
        selected = ordered_ids[:lim]
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

    stats = {"requests": 0, "added": 0, "skipped": 0}
    os.makedirs(data_dir, exist_ok=True)
    hist_file = open(history_path, "a", encoding="utf-8")
    processed = 0
    try:
        for aid in selected:
            if stop_requested:
                break
            if stats["requests"] >= args.max_requests:
                print(f"\n> Veiligheidslimiet van {args.max_requests} requests "
                      "bereikt. Draai het script opnieuw om verder te gaan.")
                break

            record = fetch_store_record(aid, args, stats)
            if stop_requested:
                break
            if record is None:
                # geen (game-)storepagina meer, of request definitief mislukt
                stats["skipped"] += 1
            else:
                pc = fetch_player_count(aid, args.timeout)
                stats["requests"] += 1
                record["last_seen_player_count"] = pc
                record["appid_amount"] = next_amount(aid)
                record["refresh_count"] = 1   # elke history-regel = 1 vernieuwing
                hist_file.write(
                    json.dumps(record, ensure_ascii=False) + "\n")
                hist_file.flush()
                stats["added"] += 1
                print(f"   + {aid}  {record.get('name')}  ->  "
                      f"{record['appid_amount']}  (players: {pc})")

            processed += 1
            if processed % 25 == 0:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                      f"verwerkt={processed}/{len(selected)}  "
                      f"requests={stats['requests']}  "
                      f"toegevoegd={stats['added']}")

            if not stop_requested:
                wait_chunked(args.delay + random_jitter(args.jitter))
    finally:
        hist_file.close()

    print("\n=== Samenvatting ===")
    print(f"Games verwerkt deze run     : {processed}")
    print(f"Requests deze run           : {stats['requests']}")
    print(f"Tijdlijnregels toegevoegd   : {stats['added']}")
    print(f"Overgeslagen (geen pagina)  : {stats['skipped']}")
    print(f"Output                      : {history_path}")
    if len(selected) > processed:
        print(f"\n> {len(selected) - processed} games nog niet aan bod; draai "
              "het script opnieuw voor de volgende.")


if __name__ == "__main__":
    main()
