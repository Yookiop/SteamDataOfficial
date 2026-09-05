#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - games.jsonl omzetten naar een tabel-layout (CSV)
====================================================================

Leest data/games.jsonl (1 JSON-object per regel, gemaakt door
fetch_games_initial.py) en schrijft daar een 'dbt-achtige' set CSV-bestanden
vanuit, zodat je de data in Excel/sheets kunt bekijken zonder dbt Cloud:

    games.csv             HOOFDTABEL - 1 regel per appid (scalaire velden +
                          price_overview platgeslagen naar price_*_fmt-
                          kolommen; alleen de leesbare bedragen, geen ruwe
                          centen, incl. de review-samenvatting: review_score,
                          review_score_desc, review_positive, review_negative,
                          review_total). GEEN appid_amount: dit is de basis,
                          elke appid komt er maar 1x in voor
    game_genres.csv       appid + genre (1 regel per genre per appid)
    game_categories.csv   appid + category (1 regel per category per appid)
    game_publishers.csv   appid + publisher (1 regel per publisher per appid)
    games_extra_info.csv  EXTRA INFO (games_extra_info.jsonl, was
                          player_history.jsonl) - 1 regel per momentopname
                          per appid (de doorlopende reeks per game: appid_amount
                          _1, _2, ...; + refresh_count)
    date.csv              DATE-DIMENSIE - elke dag van --date-start t/m
                          --date-end (default 2003-01-01 t/m 2026-12-31),
                          join-key: date_fmt (= release_date_fmt)

De lijstvelden (genres/categories/publishers) worden dus NIET in de
hoofdtabel geplakt maar genormaliseerd naar losse tabellen, gekoppeld via
appid. Binnen een appid worden dubbele waarden eruit gefilterd (de bron-API
geeft bv. bij Half-Life 2 'DualShock Controller Support' 2x terug), zodat de
losse tabellen netjes uniek zijn per (appid, waarde).

Naast data/games.jsonl (fetch_games_initial.py, de basis) leest het script
ook data/games_extra_info.jsonl (fetch_new_game_info.py; was
player_history.jsonl) en schrijft daarvan games_extra_info.csv: elke regel
is daar een aparte momentopname, dus een appid kan meerdere regels hebben
(gesorteerd op appid + appid_amount-nummer _1, _2, ...). Staat het
bestand er niet, dan wordt alleen games_extra_info.csv overgeslagen.

Bestandsrotatie (data_rotation.py): de jsonl-datasets groeien sinds
2026-09-05 door in delen van max ~90 MB (games.jsonl + games_2.jsonl +
..., games_extra_info.jsonl + games_extra_info_2.jsonl + ..., t/m _5).
Dit script voegt AL die delen samen (union in volgorde deel 1, 2, 3, ...)
- je geeft dus gewoon het basispad op (--input / --extra) en de CSVs
bevatten de samengevoegde data alsof het één bestand was. In Power BI is
dat één combinatietabel i.p.v. meerdere losse CSV's.

Games.csv en games_extra_info.csv houden alleen release_date_fmt bij (de
join-sleutel); de datumdelen (year, month_number, month_label, day_of_week,
day_of_week_label, day_of_month) staan uitsluitend in date.csv. Je joint
games.csv/games_extra_info.csv dus op release_date_fmt = date_fmt met
date.csv en haalt de datumdelen daar vandaan.

Output staat standaard naast de invoer (data/). De bestanden zijn UTF-8 met
BOM (utf-8-sig), zodat Excel de tekst/valuta-codes goed toont.

Gebruik:
    python jsonl_to_table.py
    python jsonl_to_table.py --input data/games.jsonl
    python jsonl_to_table.py --extra data/games_extra_info.jsonl
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, timedelta

# Bestandsrotatie: datasets bestaan uit delen van max ~90 MB
# (basis + _2 .. _5). all_part_paths/canonical_base voegen delen samen.
from data_rotation import all_part_paths, canonical_base

MAIN_FIELDS = [
    # kolom            -> pad in het JSON-record
    ("appid",              ["appid"]),
    ("name",               ["name"]),
    ("type",               ["type"]),
    ("is_free",            ["is_free"]),
    ("price_currency",     ["price_overview", "currency"]),
    ("price_discount_pct", ["price_overview", "discount_percent"]),
    ("price_initial_fmt",  ["price_overview", "initial_formatted"]),
    ("price_final_fmt",    ["price_overview", "final_formatted"]),
    # Alleen de *_fmt-varianten in de CSV: de ruwe centen (price_initial /
    # price_final) en de leesbare release_date-tekst staan wél in de jsonl
    # maar niet in de tabellen; er wordt gejoind op release_date_fmt.
    ("release_date_fmt",   ["release_date_format"]),
    ("recommendations",    ["recommendations_total"]),
    ("last_seen_players",  ["last_seen_player_count"]),
    ("review_score",       ["review_score"]),          # 0-10 (Review API)
    ("review_score_desc",  ["review_score_desc"]),     # bv. Very Positive/Mixed
    ("review_positive",    ["review_positive"]),
    ("review_negative",    ["review_negative"]),
    ("review_total",       ["review_total"]),
    # GEEN appid_amount: games.csv is de basis (elke appid 1x). De
    # doorlopende per-game nummering zit in games_extra_info.csv (zie
    # HISTORY_FIELDS hieronder).
]

CHILD_TABLES = [          # bestandsnaam    -> pad in het JSON-record
    ("game_genres",      ["genres"]),
    ("game_categories",  ["categories"]),
    ("game_publishers",  ["publishers"]),
]

# Voor games_extra_info.jsonl (was player_history.jsonl) geldt dezelfde
# layout als de hoofdtabel, plus appid_amount, refresh_count en
# DataUpdatedAt (datumtijd van schrijven). Daar is elke regel een aparte
# momentopname: de doorlopende reeks per game begint bij _1 en loopt op met
# _2, _3, ... (de basis/master telt niet mee).
HISTORY_FIELDS = MAIN_FIELDS + [
    ("appid_amount",       ["appid_amount"]),
    ("refresh_count",      ["refresh_count"]),
    ("DataUpdatedAt",      ["DataUpdatedAt"]),   # tijdstip van schrijven
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


def read_records_multi(base_path):
    """Lees een dataset uit ALLE rotatiedelen (basis + <naam>_2 .. _5),
    in volgorde samengevoegd. Retourneert (records, totaal_aantal_regels,
    aantal_delen)."""
    parts = all_part_paths(base_path)
    records, total = [], 0
    for path in parts:
        recs, n = read_records(path)
        records.extend(recs)
        total += n
    return records, total, len(parts)


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
    ontbreekt/onherkenbaar is. Zet games_extra_info per appid in
    meetvolgorde."""
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
    p.add_argument("--extra", default="data/games_extra_info.jsonl",
                   help="invoer extra-info van fetch_new_game_info.py, voor "
                        "games_extra_info.csv (default: "
                        "data/games_extra_info.jsonl)")
    p.add_argument("--date-start", default="2003-01-01",
                   help="begin datum voor date.csv (default: 2003-01-01)")
    p.add_argument("--date-end", default="2026-12-31",
                   help="eind datum voor date.csv (default: 2026-12-31)")
    args = p.parse_args(argv)

    in_path = canonical_base(os.path.abspath(args.input))
    if not all_part_paths(in_path):
        print(f"! Invoer niet gevonden: {in_path}")
        sys.exit(1)
    out_dir = (os.path.abspath(args.out_dir) if args.out_dir
               else os.path.dirname(in_path))
    os.makedirs(out_dir, exist_ok=True)

    records, total_lines, n_parts = read_records_multi(in_path)
    delen = f" ({n_parts} delen samengevoegd)" if n_parts > 1 else ""
    print(f"> {len(records)} records gelezen uit {in_path}{delen} "
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

    # Extra info (games_extra_info.jsonl*, geschreven door
    # fetch_new_game_info.py): 1 rij per momentopname (meerdere rijen per
    # appid: _1, _2, ...). Rotatiedelen (_2 .. _5) worden samengevoegd.
    # Ontbreken alle delen, dan wordt alleen games_extra_info.csv
    # overgeslagen.
    extra_base = canonical_base(os.path.abspath(args.extra))
    extra_parts = all_part_paths(extra_base)
    if extra_parts:
        extra_records, _, extra_n = read_records_multi(extra_base)
        if extra_records:
            extra_rows = flatten_rows(extra_records, HISTORY_FIELDS)
            extra_rows.sort(key=lambda r: (r["appid"] is None,
                                           r["appid"] or 0,
                                           amount_ordinal(r)))
            out = os.path.join(out_dir, "games_extra_info.csv")
            n = write_csv(out, extra_rows, HISTORY_FIELDS)
            delen = f" uit {extra_n} delen" if extra_n > 1 else ""
            print(f"> Extra info geschreven: {out} ({n} regels{delen}, "
                  "1 per momentopname per appid)")
        else:
            print(f"> {extra_base} (en delen) zijn leeg - geen "
                  "games_extra_info.csv.")
    else:
        print(f"> {extra_base} niet gevonden - games_extra_info.csv "
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
