#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - nog niet uitgebrachte games naar de master promoveren
=========================================================================

Broertje van `fetch_games_initial.py`. Dat script zet alleen games die
daadwerkelijk UIT ZIJN in de master (`data/games.jsonl`): een echte
releasedatum, dus een gevulde `release_date_format`. Games die nog moeten
verschijnen ("Coming soon", "To be announced", "Q4 2026", "November 2026",
"2027", ...) belanden in `data/upcoming.jsonl` - een klein record met alleen
appid, naam, de ruwe datumtekst en `release_checked_at`.

Dit script controleert die upcoming-lijst opnieuw via de Storefront API
(keyless, cc=us; games uit `us_region_blocked.json` via hun eigen regio - net
als het zusterscript) en:

  * heeft Steam nu WEL een echte datum -> de game wordt **gepromoveerd**: het
    volledige record (met spelersaantal + review-samenvatting) wordt aan de
    master toegevoegd en de stub verdwijnt uit upcoming.jsonl;
  * nog steeds geen echte datum -> de stub wordt bijgewerkt (ruwe datum kan
    veranderd zijn, bv. "2026" -> "Q4 2026") + `release_checked_at`;
  * geen storepagina meer / niet langer een game -> de stub blijft staan,
    maar wordt vandaag niet opnieuw geprobeerd.

MAX. 1x PER DAG (net als de extra-info-taak): elk upcoming-record heeft
`release_checked_at` (UTC). Een game die op dezelfde "run-dag" (UTC+2, dus de
3 runs 02:30/10:30/18:30 NL) al is bekeken wordt overgeslagen - anders zouden
de 3 runs van een dag drie keer dezelfde games pakken. Uitzetten:
`--ignore-same-day`. Een échte netwerk-/throttle-fout wordt juist NIET
vastgelegd (die proberen we opnieuw).

Volgorde: **eerlijke ronde** - de game met de OUDSTE `release_checked_at`
gaat eerst (een game die nog nooit is gecontroleerd staat vooraan). Is een game
gecontroleerd, dan krijgt hij de datum van vandaag en sluit dus achteraan aan;
zo komt elke game precies even vaak aan de beurt als de rest en kan geen
enkele game worden overgeslagen of voorgetrokken. `--limit` en/of
`--max-duration-minutes` begrenzen de run.

Met `--migrate-unreleased` doet het script ook de eenmalige opruiming: alle
games die nú nog in de master staan met een lege `release_date_format`
verhuizen naar upcoming.jsonl, zodat games.jsonl alleen uitgebrachte games
bevat.

Keyless: de Storefront-, spelersaantal- en Review-API hebben geen API key
nodig (alleen de app-lijst van `fetch_games_initial.py` heeft er een).

Gebruik:
    python fetch_games_initial_update_releases.py --report-only
    python fetch_games_initial_update_releases.py --limit 500
    python fetch_games_initial_update_releases.py --max-duration-minutes 30
    python fetch_games_initial_update_releases.py --ignore-same-day --limit 100
    python fetch_games_initial_update_releases.py --migrate-unreleased
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# Hergebruik van de bestaande scripts: geen tweede kopie van dezelfde logica.
# Uit fetch_games_initial: het HTTP-/recordformaat, de upcoming-lijst en de
# hulpen om records weg te schrijven; uit fetch_new_game_info: de tijd- en
# dag-hulpen (UTC-tijdstempels en de "run-dag"); data_rotation voor de rotatie.
from data_rotation import RotatingAppend
from fetch_games_initial import (CONSECUTIVE_FAIL_STOP, DEFAULT_DELAY,
                                 DEFAULT_JITTER, DEFAULT_MAX_REQUESTS,
                                 DEFAULT_RETRIES, DEFAULT_TIMEOUT,
                                 GRACEFUL_STOP_MARGIN_MINUTES,
                                 append_upcoming, enrich_live,
                                 fetch_store_batch, load_upcoming,
                                 load_us_region_blocked, random_jitter,
                                 upcoming_stub, wait_chunked, write_upcoming)
from fetch_new_game_info import (load_master, now_iso, parse_updated_at,
                                 run_day_key, write_master)

# Veld waarin we bijhouden wanneer deze game voor het laatst op een
# releasedatum is gecontroleerd (UTC). Alleen upcoming-records hebben het.
CHECKED_FIELD = "release_checked_at"
# Veld met de leesbare datum zoals de API hem geeft ("Coming soon", "Q4 2026").
RAW_DATE_FIELD = "release_date"
# Veld met de parseerbare yyyy-mm-dd (leeg = nog geen echte datum).
ISO_DATE_FIELD = "release_date_format"


def has_no_release_date(rec):
    """True als de releasedatum (nog) geen parseerbare yyyy-mm-dd is."""
    return not str(rec.get(ISO_DATE_FIELD) or "").strip()


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Controleert de games in upcoming.jsonl (nog niet "
                    "uitgebracht) en zet ze in de master (games.jsonl) zodra "
                    "Steam een echte releasedatum geeft. Upcoming-records "
                    "zonder datum worden bijgewerkt (ruwe datum + "
                    "release_checked_at). Keyless - geen API key nodig.")
    p.add_argument("--data-dir", default="data",
                   help="map van upcoming/master/us_region_blocked "
                        "(default: data)")
    p.add_argument("--master", default=None,
                   help="master-bestand (default: <data-dir>/games.jsonl); "
                        "alleen gebruikt bij --migrate-unreleased")
    p.add_argument("--migrate-unreleased", action="store_true",
                   help="EENMALIGE migratie: alle games in de master met een "
                        "lege release_date_format verhuizen naar "
                        "upcoming.jsonl (de master bevat daarna alleen "
                        "uitgebrachte games) en stoppen")
    p.add_argument("--limit", type=int, default=None,
                   help="max. aantal games dat deze run wordt gecontroleerd "
                        "(default: geen limiet; het tijdsbudget stopt de run)")
    p.add_argument("--report-only", action="store_true",
                   help="alleen tonen welke games gecontroleerd zouden worden")
    p.add_argument("--ignore-same-day", action="store_true",
                   help="de 'max. 1x per dag'-regel uitzetten (elke game mag "
                        "meerdere keren per run-dag gecontroleerd worden)")
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
                   help="max. aantal minuten dat deze run mag draaien (zet "
                        "dit op de step-timeout in de GitHub Action). Het "
                        "script stopt zelf NETJES "
                        f"{GRACEFUL_STOP_MARGIN_MINUTES} minuten vóór die "
                        "grens en schrijft de master weg, i.p.v. keihard "
                        "afgekapt te worden.")
    args = p.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    master_path = (os.path.abspath(args.master) if args.master
                   else os.path.join(data_dir, "games.jsonl"))

    # ---- --migrate-unreleased: eenmalige opruiming van de master -------- #
    # Games die er nog in stonden met een lege release_date_format (van vóór
    # deze scheiding) verhuizen naar upcoming.jsonl. Daarna bevat games.jsonl
    # alleen games die daadwerkelijk uit zijn.
    if args.migrate_unreleased:
        master = load_master(master_path)
        if not master:
            print(f"! Geen games gevonden in {master_path}.")
            sys.exit(1)
        seen = set(load_upcoming(data_dir))
        stubs = [upcoming_stub(rec, aid)
                 for aid, rec in master.items()
                 if has_no_release_date(rec)]
        if not stubs:
            print("> Niets te migreren: elke game in de master heeft al een "
                  "echte releasedatum.")
            return
        added = append_upcoming(data_dir, stubs, seen)
        moved = {int(r["appid"]) for r in stubs}
        write_master(master_path,
                     {aid: rec for aid, rec in master.items()
                      if aid not in moved})
        print(f"> Migratie klaar: {added} games naar upcoming.jsonl "
              f"({len(seen)} totaal); de master houdt "
              f"{len(master) - len(moved)} games over (alleen uitgebrachte "
              "games).")
        return

    # ---- Kandidaten: de upcoming-lijst ---------------------------------- #
    upcoming = load_upcoming(data_dir)
    if not upcoming:
        print("\n=== SteamDataOfficial: releasedatums bijwerken ===")
        print("> upcoming.jsonl is leeg (of bestaat nog niet): elke bekende "
              "game heeft al een echte releasedatum. Niets te doen.")
        return
    us_blocked = load_us_region_blocked(data_dir)

    # ---- Max. 1x per dag + eerlijke ronde ------------------------------- #
    # Max. 1x per dag: dezelfde run-dag-definitie als de extra-info-taak
    # (UTC+2), zodat de 3 runs van één NL-dag niet dezelfde games pakken.
    # Tegelijk sorteren we op RELEASE_CHECKED_AT (oudste eerst): de games die
    # het langst niet gecontroleerd zijn gaan voor, en een nooit gecontroleerde
    # game (geen veld) staat helemaal vooraan. Een gecontroleerde game krijgt
    # de datum van vandaag en sluit dus achteraan aan - zo komt elke game
    # precies even vaak aan de beurt als de rest, in plaats van dat random een
    # game voortrekt of juist laat liggen.
    run_day = run_day_key(datetime.now(timezone.utc))
    skipped_today = 0
    queue = []                       # [(laatst gecontroleerd of None, appid)]
    for aid in upcoming:
        upd = parse_updated_at(upcoming[aid].get(CHECKED_FIELD))
        if (not args.ignore_same_day and upd is not None
                and run_day_key(upd) == run_day):
            skipped_today += 1       # vandaag al bekeken -> overslaan
            continue
        queue.append((upd, aid))
    # Oudste eerst; 'nooit gecontroleerd' = 0.0 (vóór elk echt tijdstip, want
    # alle tijdstempels liggen na 1970). Het appid beslist bij gelijke
    # tijdstempels, zodat de volgorde deterministisch is (zelfde uitkomst bij
    # een herstart of --report-only).
    queue.sort(key=lambda item: (item[0].timestamp() if item[0] else 0.0,
                                 item[1]))
    candidates = [aid for _, aid in queue]
    if args.limit is not None and len(candidates) > args.limit:
        candidates = candidates[:args.limit]

    print("\n=== SteamDataOfficial: releasedatums bijwerken ===")
    print(f"In upcoming.jsonl (kandidaat): {len(upcoming)}")
    print(f"Te controleren deze run     : {len(candidates)}")
    if args.ignore_same_day:
        print("Max. 1x per dag             : UIT (--ignore-same-day)")
    else:
        print(f"Max. 1x per dag             : aan - {skipped_today} games "
              f"vandaag al bekeken en overgeslagen (run-dag {run_day}).")
    print("Volgorde                    : oudste release_checked_at eerst "
          "(eerlijke ronde)")
    if queue:
        oldest = queue[0][0]
        print("Oudste kandidaat            : "
              + (oldest.isoformat(timespec="seconds") if oldest is not None
                 else "nog nooit gecontroleerd"))

    if args.report_only:
        print("> Zou controleren (de 30 oudste):")
        for aid in candidates[:30]:
            rec = upcoming[aid]
            print(f"   ~ {aid}  {rec.get('name')}  "
                  f"({rec.get(RAW_DATE_FIELD)!r}; laatst gecontroleerd: "
                  f"{rec.get(CHECKED_FIELD) or 'nooit'})")
        if len(candidates) > 30:
            print(f"   ... en nog {len(candidates) - 30} meer")
        print("> --report-only: niets opgehaald/bijgewerkt.")
        return

    if not candidates:
        print("> Alles is vandaag al bekeken. Niets te doen.")
        return

    # Duurbudget: netjes stoppen ~5 min vóór de opgegeven grens, zodat de
    # GitHub step-timeout de run niet keihard afkapt.
    t0 = time.monotonic()
    if args.max_duration_minutes:
        deadline = max(0.0, args.max_duration_minutes
                       - GRACEFUL_STOP_MARGIN_MINUTES) * 60.0
        print(f"> Tijdsbudget: {args.max_duration_minutes:.0f} min "
              f"(marge {GRACEFUL_STOP_MARGIN_MINUTES} min)")
    else:
        deadline = None

    stats = {"requests": 0, "checked": 0, "promoted": 0, "still_empty": 0,
             "skipped": 0, "other": 0, "failed": 0}
    # Gepromoveerde games verdwijnen uit upcoming; bijgewerkte stubs blijven.
    # upcoming.jsonl wordt aan het eind volledig herschreven (klein bestand).
    promoted = set()
    touched = False
    consecutive_failures = 0

    # Uitgebrachte games komen in de master - precies zoals
    # fetch_games_initial dat doet (append, met rotatie).
    games_file = RotatingAppend(master_path, log=print)

    def promote(info, aid):
        """Zet een uitgebrachte game in de master; upcoming wordt aan het
        eind herschreven."""
        enrich_live(info, aid, args, stats)
        games_file.write_line(json.dumps(info, ensure_ascii=False))
        promoted.add(aid)
        stats["promoted"] += 1
        print(f"   + {aid}  {info.get('name')}  "
              f"({info.get(RAW_DATE_FIELD)!r} -> "
              f"{info.get(ISO_DATE_FIELD)!r})  [UITGEBRACHT -> master]")

    try:
        for aid in candidates:
            if deadline is not None and (time.monotonic() - t0) >= deadline:
                print("\n> Tijdsbudget bijna op - de run wordt netjes "
                      "afgerond; draai het script opnieuw voor de rest.")
                break
            if stats["requests"] >= args.max_requests:
                print(f"\n> Veiligheidslimiet van {args.max_requests} "
                      "requests bereikt. Draai het script opnieuw om verder "
                      "te gaan.")
                break
            if consecutive_failures >= CONSECUTIVE_FAIL_STOP:
                print(f"\n! {consecutive_failures} requests op rij mislukt - "
                      "het IP is waarschijnlijk (tijdelijk) geblokkeerd. De "
                      "run stopt netjes; de volgende run pakt de rest.")
                break

            rec = upcoming[aid]
            cc = "us"
            _blk = us_blocked.get(aid)
            if _blk and _blk.get("cc"):
                cc = _blk["cc"]

            results = fetch_store_batch([aid], args, stats, cc=cc)
            if results is None:                 # onderbroken -> netjes stoppen
                break
            _aid, outcome, info = results[0]

            if outcome == "failed":
                # netwerk/throttle: NIET als 'vandaag bekeken' markeren, zodat
                # een volgende run het opnieuw probeert.
                stats["failed"] += 1
                consecutive_failures += 1
                print(f"   ! {aid}  request mislukt (poging deze run: "
                      f"{consecutive_failures})")
                wait_chunked(args.delay + random_jitter(args.jitter))
                continue
            consecutive_failures = 0
            stats["checked"] += 1

            if outcome == "game" and not has_no_release_date(info):
                promote(info, aid)              # uitgebracht -> naar de master
            elif outcome == "game":
                # Nog steeds geen exacte datum: stub bijwerken (de ruwe tekst
                # kan veranderd zijn, bv. "2026" -> "Q4 2026").
                old_raw = rec.get(RAW_DATE_FIELD)
                rec[RAW_DATE_FIELD] = info.get(RAW_DATE_FIELD)
                rec[ISO_DATE_FIELD] = info.get(ISO_DATE_FIELD) or ""
                rec[CHECKED_FIELD] = now_iso()
                touched = True
                stats["still_empty"] += 1
                print(f"   - {aid}  {rec.get('name')}  "
                      f"({old_raw!r} -> {rec.get(RAW_DATE_FIELD)!r} - nog "
                      "niet uitgebracht)")
            else:
                # 'skipped' = geen storepagina (meer), 'other' = geen game
                # meer. Niets aan het record veranderen, maar wel als
                # 'vandaag bekeken' markeren: anders pakken we deze game elke
                # run opnieuw en dat kost alleen maar tijd.
                rec[CHECKED_FIELD] = now_iso()
                touched = True
                if outcome == "skipped":
                    stats["skipped"] += 1
                    print(f"   ? {aid}  {rec.get('name')}  (geen storepagina "
                          "meer - blijft in upcoming, vandaag niet opnieuw)")
                else:
                    stats["other"] += 1
                    print(f"   ? {aid}  {rec.get('name')}  (type "
                          f"{info.get('type')!r} - niet langer een game)")

            if stats["checked"] and stats["checked"] % 25 == 0:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                      f"gecontroleerd={stats['checked']}  "
                      f"datum gevonden={stats['filled']}  "
                      f"requests={stats['requests']}")

            wait_chunked(args.delay + random_jitter(args.jitter))
    finally:
        games_file.close()
        if touched or promoted:
            # Volledige herschrijving mét rotatie, in dezelfde volgorde -
            # alleen zonder de games die nu in de master staan.
            write_upcoming(data_dir,
                           (rec for aid, rec in upcoming.items()
                            if aid not in promoted))
            print(f"> upcoming.jsonl bijgewerkt "
                  f"({len(upcoming) - len(promoted)} games over).")

    print("\n=== Samenvatting ===")
    print(f"Gecontroleerd deze run      : {stats['checked']}")
    print(f"  waarvan UITGEBRACHT       : {stats['promoted']}   "
          "(naar games.jsonl)")
    print(f"  nog niet uitgebracht      : {stats['still_empty']}")
    print(f"Overgeslagen (geen pagina)  : {stats['skipped']}")
    print(f"Niet langer een game        : {stats['other']}")
    print(f"Mislukt (netwerk/throttle)  : {stats['failed']}")
    print(f"Requests                    : {stats['requests']}")
    if stats["checked"] < len(candidates):
        print(f"\n> {len(candidates) - stats['checked']} kandidaten nog niet "
              "bekeken; draai het script opnieuw (of wacht op de volgende "
              "run) voor de rest.")


if __name__ == "__main__":
    main()
