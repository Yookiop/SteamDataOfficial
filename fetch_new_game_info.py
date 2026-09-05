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
     officiele Storefront API, cc=us + l=english -> USD).
  2. last_seen_player_count (momentopname, keyless via
     ISteamUserStats/GetNumberOfCurrentPlayers; None bij geen publieke data).
  3. De review-samenvatting (officiele Review API
     store.steampowered.com/appreviews/{appid}, keyless): review_score,
     review_score_desc (bv. 'Very Positive', 'Mixed'), review_positive,
     review_negative en review_total - met language=all + purchase_type=all
     + filter=all (zelfde totaalbeeld als de storepagina; zie
     fetch_games_initial.py). Zo zie je per game ook hoe de beoordeling
     over de tijd verandert. Uitzetten kan met --no-reviews.
  4. appid_amount = "<appid>_<n>" met doorlopende nummering per game binnen
     games_extra_info.jsonl: de eerste regel van een game is <appid>_1,
     daarna <appid>_2, _3, ...
  5. refresh_count = 1 op ELKE regel: zo kun je later per game het aantal
     vernieuwingen optellen (som van refresh_count per appid).

Volgorde (zonder --random): gesorteerd op het LAATST bekende spelersaantal
per game (eerst de laatste games_extra_info-regel van dat appid, anders de
masterwaarde uit games.jsonl), aflopend; bij gelijk -> oplopend appid. Zo
vernieuwt een run met --limit altijd eerst de populairste games. Met
--random wordt de volgorde elke run opnieuw willekeurig gemixt (uniforme
steekproef): dan blijven niet steeds dezelfde top-games vooraan staan en
wordt ook een game die ver achteraan staat (bv. op plek 50.000) regelmatig
bijgewerkt.

Anders dan fetch_games_initial wordt er dus NIET geskipt op bekende appids,
niet gededuped en niet geblacklist: elke run telt. Geen API key nodig (dit
script haalt alleen de (keyless) store-, spelersaantal- en review-data op
van games die al in de master staan).

Gebruik:
    python fetch_new_game_info.py             # alle games pollen (meeste spelers eerst)
    python fetch_new_game_info.py --limit 500 # max. 500 games deze run (populairste eerst)
    python fetch_new_game_info.py --random    # elke run een willekeurige selectie appids,
                                              # zodat niet steeds dezelfde top-games worden
                                              # bijgewerkt (combineerbaar met --limit)
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
from datetime import datetime

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


def load_extra_info(path):
    """Lees games_extra_info.jsonl. Retourneert (counts, last_players,
    latest_reviews): per appid het aantal regels, het laatste bekende
    spelersaantal én de review-samenvatting van de LAATSTE regel (die
    wint - om games.jsonl bij te werken met de laatst bekende data)."""
    counts, last_players, latest_reviews = {}, {}, {}
    if not os.path.isfile(path):
        return counts, last_players, latest_reviews
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
        if any(r.get(k) is not None for k in REVIEW_KEYS):
            latest_reviews[aid] = {k: r.get(k) for k in REVIEW_KEYS}
    return counts, last_players, latest_reviews


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
    """Schrijf de master (dict, volgorde bewaard) terug naar games.jsonl."""
    with open(path, "w", encoding="utf-8") as f:
        for rec in master.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(
        description="EXTRA INFO: per run een extra regel per game toevoegen "
                    "aan games_extra_info.jsonl (was player_history.jsonl: "
                    "zelfde velden + last_seen_player_count + doorlopende "
                    "appid_amount vanaf _1 per game). Geen API key nodig.")
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
    p.add_argument("--no-reviews", action="store_true",
                   help="geen review-samenvatting ophalen (Review API, 1 "
                        "extra request per game); default: wel")
    args = p.parse_args(argv)

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
    extra_counts, extra_last, latest_reviews = load_extra_info(extra_path)

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
    # --random: de volgorde wordt elke run opnieuw gemixt (uniforme
    # willekeurige steekproef). Zo blijven niet steeds dezelfde populairste
    # games vooraan staan en wordt ook iets op plek 50.000 regelmatig
    # bijgewerkt. Zonder --random: meeste spelers eerst.
    if args.random:
        random.shuffle(ordered_ids)

    print("\n=== SteamDataOfficial: extra info per game ===")
    print(f"Games in de master          : {len(master)}")
    print(f"Al in games_extra_info.jsonl: "
          f"{sum(extra_counts.values())} regels")
    if args.random:
        print("Volgorde                    : willekeurig (--random)")
    else:
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

    # Basis (games.jsonl, 1 record per game) eerst bijwerken met de laatst
    # bekende reviews uit games_extra_info (geen extra requests); games die
    # deze run worden verwerkt krijgen daarna de verse waarde van nu.
    master_dirty = apply_latest_reviews(master, latest_reviews) > 0
    stats = {"requests": 0, "added": 0, "skipped": 0}
    os.makedirs(data_dir, exist_ok=True)
    extra_file = open(extra_path, "a", encoding="utf-8")
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
                if args.no_reviews:
                    rev = None
                else:
                    rev = fetch_review_summary(aid, args.timeout)
                    stats["requests"] += 1
                for key in ("review_score", "review_score_desc",
                            "review_positive", "review_negative",
                            "review_total"):
                    record[key] = (rev or {}).get(key)
                record["appid_amount"] = next_amount(aid)
                record["refresh_count"] = 1   # elke extra-info-regel = 1 vernieuwing
                extra_file.write(
                    json.dumps(record, ensure_ascii=False) + "\n")
                extra_file.flush()
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
    print(f"Requests deze run           : {stats['requests']}")
    print(f"Extra-info-regels toegevoegd: {stats['added']}")
    print(f"Overgeslagen (geen pagina)  : {stats['skipped']}")
    print(f"Output                      : {extra_path}")
    if len(selected) > processed:
        print(f"\n> {len(selected) - processed} games nog niet aan bod; draai "
              "het script opnieuw voor de volgende.")


if __name__ == "__main__":
    main()
