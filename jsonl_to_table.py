#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - games.jsonl omzetten naar een tabel-layout (CSV)
====================================================================

Leest data/games.jsonl (1 JSON-object per regel, gemaakt door
fetch_games_initial.py) en schrijft daar een 'dbt-achtige' set CSV-bestanden
vanuit, zodat je de data in Excel/sheets kunt bekijken zonder dbt Cloud:

    games.csv             HOOFDTABEL - 1 regel per appid (scalaire velden +
                          price_overview platgeslagen naar price_*-kolommen)
    game_genres.csv       appid + genre (1 regel per genre per appid)
    game_categories.csv   appid + category (1 regel per category per appid)
    game_publishers.csv   appid + publisher (1 regel per publisher per appid)
    player_history.csv    TIJDLIJN (player_history.jsonl) - 1 regel per
                          meting per appid (last_seen_player_count over de
                          tijd; appid_amount _2, _3, ...; + refresh_count)
    date.csv              DATE-DIMENSIE - elke dag van --date-start t/m
                          --date-end (default 2003-01-01 t/m 2026-12-31),
                          join-key: date_fmt (= release_date_fmt)

De lijstvelden (genres/categories/publishers) worden dus NIET in de
hoofdtabel geplakt maar genormaliseerd naar losse tabellen, gekoppeld via
appid. Binnen een appid worden dubbele waarden eruit gefilterd (de bron-API
geeft bv. bij Half-Life 2 'DualShock Controller Support' 2x terug), zodat de
losse tabellen netjes uniek zijn per (appid, waarde).

Naast data/games.jsonl (fetch_games_initial.py) leest het script ook
data/player_history.jsonl (fetch_new_game_info.py, de tijdlijn) en schrijft
daarvan player_history.csv: elke regel is daar een aparte meting, dus een
appid kan meerdere regels hebben (gesorteerd op appid + meetnummer). Staat de
tijdlijn er niet, dan wordt alleen player_history.csv overgeslagen.

Games.csv en player_history.csv houden alleen release_date_fmt bij (de
join-sleutel); de datumdelen (year, month_number, month_label, day_of_week,
day_of_week_label, day_of_month) staan uitsluitend in date.csv. Je joint
games.csv/player_history.csv dus op release_date_fmt = date_fmt met
date.csv en haalt de datumdelen daar vandaan.

Output staat standaard naast de invoer (data/). De bestanden zijn UTF-8 met
BOM (utf-8-sig), zodat Excel de tekst/valuta-codes goed toont.

Gebruik:
    python jsonl_to_table.py
    python jsonl_to_table.py --input data/games.jsonl
    python jsonl_to_table.py --history data/player_history.jsonl
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, timedelta

MAIN_FIELDS = [
    # kolom            -> pad in het JSON-record
    ("appid",              ["appid"]),
    ("name",               ["name"]),
    ("type",               ["type"]),
    ("is_free",            ["is_free"]),
    ("price_currency",     ["price_overview", "currency"]),
    ("price_initial",      ["price_overview", "initial"]),      # centen (USD)
    ("price_final",        ["price_overview", "final"]),        # centen (USD)
    ("price_discount_pct", ["price_overview", "discount_percent"]),
    ("price_initial_fmt",  ["price_overview", "initial_formatted"]),
    ("price_final_fmt",    ["price_overview", "final_formatted"]),
    ("release_date",       ["release_date"]),
    ("release_date_fmt",   ["release_date_format"]),
    ("recommendations",    ["recommendations_total"]),
    ("last_seen_players",  ["last_seen_player_count"]),
    ("appid_amount",       ["appid_amount"]),
]

CHILD_TABLES = [          # bestandsnaam    -> pad in het JSON-record
    ("game_genres",      ["genres"]),
    ("game_categories",  ["categories"]),
    ("game_publishers",  ["publishers"]),
]

# Voor de TIJDLIJN (player_history.jsonl) geldt dezelfde layout als de
# hoofdtabel, plus refresh_count. Daar is elke regel een aparte meting: het
# appid_amount vervolgt met _2, _3, ... (nummering inclusief de initiele
# opname uit de master).
HISTORY_FIELDS = MAIN_FIELDS + [
    ("refresh_count",    ["refresh_count"]),
]

# Datumdelen voor de date-dimensie (date.csv).
MONTH_LABELS = ["", "January", "February", "March", "April", "May",
                "June", "July", "August", "September", "October",
                "November", "December"]
# Dag-van-week: ISO-nummering (maandag=1 .. zondag=7); labels lowercase.
DAY_LABELS = {1: "monday", 2: "tuesday", 3: "wednesday", 4: "thursday",
              5: "friday", 6: "saturday", 7: "sunday"}

# Kolommen van de date-dimensie (date.csv).
DATE_TABLE_COLS = [
    ("date_fmt", None),
    ("year", None),
    ("month_number", None),
    ("month_label", None),
    ("day_of_month", None),
    ("day_of_week", None),
    ("day_of_week_label", None),
]


def dig(record, path):
    """Haal de waarde op via een pad van sleutels; None als iets ontbreekt."""
    cur = record
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def read_records(path):
    """Lees alle JSON-records uit een jsonl-bestand (lege regels overslaan).
    Retourneert (records, aantal_regels)."""
    records = []
    total = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except ValueError as e:
                print(f"! Regel {total} overslaan (geen geldige JSON): {e}")
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records, total


def flatten_rows(records, fields):
    """Maak platte rijen (kolom -> waarde via paden) voor een tabel."""
    rows = []
    for rec in records:
        row = {}
        for col, path in fields:
            row[col] = dig(rec, path)
        rows.append(row)
    return rows


def write_csv(path, rows, fields):
    """Schrijf rijen weg als CSV met BOM (Excel-leesbaar) + header."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[c for c, _ in fields])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def amount_ordinal(row):
    """Het getal achter '_' in appid_amount ('730_2' -> 2), of 0 als het
    ontbreekt/onherkenbaar is. Zet de tijdlijn per appid in meetvolgorde."""
    m = re.search(r"_(\d+)\s*$", str(row.get("appid_amount") or ""))
    return int(m.group(1)) if m else 0


def parse_iso_date(iso_str):
    """'yyyy-mm-dd' -> date, of None bij leeg/ongeldig."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", (iso_str or "").strip())
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def date_parts(dt):
    """Datumdelen voor een date: jaar, maandnummer/-label en dag-van-week
    (ISO: maandag=1 t/m zondag=7) + label, voor de date-dimensie."""
    dow = dt.isoweekday()
    return {
        "year": dt.year,
        "month_number": dt.month,
        "month_label": MONTH_LABELS[dt.month],
        "day_of_week": dow,
        "day_of_week_label": DAY_LABELS[dow],
    }


def build_date_rows(start_iso, end_iso):
    """Elke dag van start t/m eind als dict voor date.csv. Retourneert []
    als het bereik ongeldig is."""
    start = parse_iso_date(start_iso)
    end = parse_iso_date(end_iso)
    if start is None or end is None or start > end:
        return []
    rows = []
    cur = start
    while cur <= end:
        row = date_parts(cur)
        row["date_fmt"] = cur.isoformat()
        row["day_of_month"] = cur.day
        rows.append(row)
        cur += timedelta(days=1)
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(
        description="games.jsonl omzetten naar CSV-tabel-layout (per appid "
                    "1 regel + losse tabellen voor genres/categories/"
                    "publishers).")
    p.add_argument("--input", default="data/games.jsonl",
                   help="invoer-jsonl (default: data/games.jsonl)")
    p.add_argument("--out-dir", default=None,
                   help="output-map voor de CSV's (default: map van de "
                        "invoer)")
    p.add_argument("--history", default="data/player_history.jsonl",
                   help="invoer-tijdlijn van fetch_new_game_info.py, voor "
                        "player_history.csv (default: "
                        "data/player_history.jsonl)")
    p.add_argument("--date-start", default="2003-01-01",
                   help="begin datum voor date.csv (default: 2003-01-01)")
    p.add_argument("--date-end", default="2026-12-31",
                   help="eind datum voor date.csv (default: 2026-12-31)")
    args = p.parse_args(argv)

    in_path = os.path.abspath(args.input)
    if not os.path.isfile(in_path):
        print(f"! Invoer niet gevonden: {in_path}")
        sys.exit(1)
    out_dir = (os.path.abspath(args.out_dir) if args.out_dir
               else os.path.dirname(in_path))
    os.makedirs(out_dir, exist_ok=True)

    records, total_lines = read_records(in_path)
    print(f"> {len(records)} records gelezen uit {in_path} "
          f"({total_lines} regels).")

    # Hoofdtabel: 1 rij per appid, gesorteerd op appid.
    rows = flatten_rows(records, MAIN_FIELDS)
    rows.sort(key=lambda r: (r["appid"] is None, r["appid"] or 0))

    # Kindtabellen: (appid, waarde), uniek binnen een appid, gesorteerd.
    child_rows = {name: set() for name, _ in CHILD_TABLES}
    for rec in records:
        try:
            aid = int(dig(rec, ["appid"]))
        except (TypeError, ValueError):
            continue
        for name, path in CHILD_TABLES:
            values = dig(rec, path)
            for v in values or []:
                if isinstance(v, str) and v.strip():
                    child_rows[name].add((aid, v.strip()))

    # Wegschrijven (utf-8-sig zodat Excel de tekens goed toont).
    main_path = os.path.join(out_dir, "games.csv")
    n = write_csv(main_path, rows, MAIN_FIELDS)
    print(f"> Hoofdtabel geschreven: {main_path} ({n} regels, 1 per appid)")

    for name, _ in CHILD_TABLES:
        path = os.path.join(out_dir, f"{name}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["appid", name.replace("game_", "")])
            writer.writerows(sorted(child_rows[name]))
        print(f"> {path} ({len(child_rows[name])} regels)")

    # Tijdlijn (player_history.jsonl, geschreven door fetch_new_game_info.py):
    # 1 rij per meting (meerdere rijen per appid: _2, _3, ...). Ontbreekt de
    # tijdlijn, dan wordt alleen player_history.csv overgeslagen.
    hist_path = os.path.abspath(args.history)
    if os.path.isfile(hist_path):
        hist_records, _ = read_records(hist_path)
        if hist_records:
            hist_rows = flatten_rows(hist_records, HISTORY_FIELDS)
            hist_rows.sort(key=lambda r: (r["appid"] is None,
                                          r["appid"] or 0,
                                          amount_ordinal(r)))
            out = os.path.join(out_dir, "player_history.csv")
            n = write_csv(out, hist_rows, HISTORY_FIELDS)
            print(f"> Tijdlijn geschreven: {out} ({n} regels, "
                  "1 per meting per appid)")
        else:
            print(f"> {hist_path} is leeg - geen player_history.csv.")
    else:
        print(f"> {hist_path} niet gevonden - player_history.csv "
              "overgeslagen.")

    # Date-dimensie (date.csv): elke dag in het bereik, join-key date_fmt.
    date_rows = build_date_rows(args.date_start, args.date_end)
    if date_rows:
        out = os.path.join(out_dir, "date.csv")
        n = write_csv(out, date_rows, DATE_TABLE_COLS)
        print(f"> Date-dimensie geschreven: {out} ({n} regels, "
              f"{date_rows[0]['date_fmt']} t/m "
              f"{date_rows[-1]['date_fmt']})")
    else:
        print(f"> Ongeldig datumbereik ({args.date_start} t/m "
              f"{args.date_end}) - geen date.csv.")


if __name__ == "__main__":
    main()
