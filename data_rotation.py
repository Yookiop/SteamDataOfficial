#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - generieke bestandsrotatie bij ~90 MB per deel
=================================================================

Elke dataset die als jsonl-'log' blijft groeien (games.jsonl,
duplicates.jsonl, games_extra_info.jsonl) wordt opgesplitst in delen van
maximaal ROTATE_BYTES (90 MiB), zodat geen enkel bestand ooit boven
GitHub's limiet van 100 MB per bestand komt:

    games_extra_info.jsonl      deel 1 (de 'basis', zonder cijfer)
    games_extra_info_2.jsonl    deel 2
    games_extra_info_3.jsonl    deel 3
    ...                         t/m MAX_PARTS (5 = basis + _2 .. _5)

Zodra een deel de grens zou overschrijden, wordt het 'gelockt' (er komt
nooit meer een regel bij) en gaat de volgende regel naar het volgende
deel. Zo blijft elk deel ruim onder de 100 MB; basis + 4 rotaties van
~90 MB + de git-historie eroverheen komt samen op ~1 GB, vandaar dat
MAX_PARTS op 5 staat (daarboven wordt de repo onpraktisch groot voor
GitHub).

Alle lezers (fetch_games_initial.py, fetch_new_game_info.py en
jsonl_to_table.py) moeten delen herkennen en SAMENVOEGEN (union in
volgorde deel 1, 2, 3, ...). Volledige herschrijvingen (bv. de master via
write_master) gebruiken rewrite_rotated(): eerst alle oude delen
verwijderen, daarna opnieuw schrijven met rotatie.

Dit is een hulpbibliotheek voor de scripts (alleen stdlib). Importeren
maakt __pycache__/ aan - die map staat in .gitignore en wordt
automatisch opnieuw gegenereerd.
"""

import os
import re

# Een deel wordt gelockt zodra de VOLGENDE regel hem boven deze grens zou
# tillen (90 MiB = ~94,4 MB decimaal; blijft ruim onder GitHub's 100 MB).
ROTATE_BYTES = 90 * 1024 * 1024
# Hoogste deelnummer: de basis (zonder cijfer) telt als deel 1, daarna
# _2 .. _MAX_PARTS. 5 delen * ~90 MB ~= 450 MB werkboom; met de
# git-historie eroverheen ~1 GB, de vuistregel-limiet van GitHub.
MAX_PARTS = 5


class RotationError(RuntimeError):
    """Wordt gegooid als een dataset al MAX_PARTS delen heeft en er nóg
    een rotatie nodig is."""


def canonical_base(path):
    """Normaliseer een pad naar het BASISDEEL: is het pad zelf een
    genummerd deel (<naam>_2.ext), dan wordt het cijfer eraf gehaald.
    Alle lezers/schrijvers werken vanuit dit basispad."""
    return _split(path)[0]


def _split(path):
    """Retourneert (basisdeel-pad, deelnummer) van een pad."""
    d = os.path.dirname(path) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    m = re.fullmatch(r"(.*)_(\d+)", stem)
    if m and int(m.group(2)) > 1:
        return os.path.join(d, m.group(1) + ext), int(m.group(2))
    return path, 1


def part_path(base_path, index):
    """Pad van deel `index`: 1 = het basisbestand zelf, 2 = <naam>_2.ext."""
    base_path = canonical_base(base_path)
    if index <= 1:
        return base_path
    stem, ext = os.path.splitext(base_path)
    return f"{stem}_{index}{ext}"


def existing_parts(base_path):
    """Alle bestaande delen van een dataset, gesorteerd op deelnummer.
    Retourneert [(index, pad), ...]; deel 1 = het basisbestand."""
    base_path = canonical_base(base_path)
    d = os.path.dirname(base_path) or "."
    base = os.path.basename(base_path)
    stem, ext = os.path.splitext(base)
    # Matcht: <stem>.ext  en  <stem>_2.ext, <stem>_3.ext, ...
    pat = re.compile(r"^" + re.escape(stem) + r"(?:_(\d+))?"
                     + re.escape(ext) + r"$")
    found = {}
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for name in names:
        m = pat.fullmatch(name)
        if not m:
            continue
        idx = int(m.group(1)) if m.group(1) else 1
        found[idx] = os.path.join(d, name)
    return [(i, found[i]) for i in sorted(found)]


def all_part_paths(base_path):
    """Alle bestaande paden van een dataset in volgorde (deel 1 eerst)."""
    return [p for _, p in existing_parts(base_path)]


def iter_lines(base_path):
    """Yield elke niet-lege, gestripte regel van ALLE delen (deel 1
    eerst). Gebruik dit voor al het lezen: zo zie je de delen als één
    samengevoegde dataset."""
    for _, path in existing_parts(base_path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line


def remove_all_parts(base_path):
    """Verwijder alle delen van een dataset (basis + genummerd)."""
    for _, path in existing_parts(base_path):
        try:
            os.remove(path)
        except OSError:
            pass


class RotatingAppend:
    """Schrijf regels append aan een dataset en roteer naar het volgende
    deel zodra het huidige deel de grens zou overschrijden.

    Een deel dat de grens bereikt wordt 'gelockt': er wordt nooit meer
    naar teruggeschreven. Een nieuwe run opent het HOOGSTE bestaande
    deel (append) en roteert vandaar verder wanneer nodig - een deel dat
    aan het einde van de vorige run net de grens had bereikt, wordt dus
    bij de eerste nieuwe regel vanzelf gerouleerd.
    """

    def __init__(self, base_path, rotate_bytes=ROTATE_BYTES,
                 max_parts=MAX_PARTS, log=None):
        self.base_path = canonical_base(base_path)
        self.rotate_bytes = rotate_bytes
        self.max_parts = max_parts
        self.log = log if log is not None else (lambda msg: None)
        parts = existing_parts(self.base_path)
        self.index = parts[-1][0] if parts else 1
        d = os.path.dirname(self.base_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = part_path(self.base_path, self.index)
        self._f = open(self.path, "a", encoding="utf-8")
        self.closed = False
        if self.index > 1:
            self.log(f"> {os.path.basename(self.path)} heeft meerdere delen; "
                     f"verder in deel {self.index}.")

    def _size(self):
        try:
            return os.fstat(self._f.fileno()).st_size
        except OSError:
            return os.path.getsize(self.path)

    def _rotate(self):
        if self.index >= self.max_parts:
            raise RotationError(
                f"! Dataset {self.base_path} heeft al {self.max_parts} "
                "delen van ~90 MB (de limiet). Verwijder oude delen of "
                "verhoog MAX_PARTS in data_rotation.py.")
        self._f.close()
        self.index += 1
        self.path = part_path(self.base_path, self.index)
        self._f = open(self.path, "a", encoding="utf-8")
        self.log(f"> {os.path.basename(self.path)} is ~90 MB groot en is "
                 f"gelockt - verder in deel {self.index} "
                 f"({os.path.basename(self.path)}).")

    def write_line(self, line):
        """Schrijf één regel (zonder verplichte \n) naar het actieve deel;
        roteert eerst als die regel het deel boven de grens zou tillen."""
        line = line if line.endswith("\n") else line + "\n"
        size = self._size()
        if size > 0 and size + len(line.encode("utf-8")) > self.rotate_bytes:
            self._rotate()
        self._f.write(line)
        self._f.flush()

    def close(self):
        if not self.closed:
            self._f.close()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def rewrite_rotated(base_path, lines, rotate_bytes=ROTATE_BYTES,
                    max_parts=MAX_PARTS, log=None):
    """Volledige herschrijving van een dataset mét rotatie: alle oude
    delen worden verwijderd en de regels worden vanaf deel 1 opnieuw
    geschreven (bij de grens verder in _2, _3, ...). Retourneert het
    aantal geschreven regels. Gebruik dit voor full rewrites zoals de
    master (write_master) - anders blijven er verouderde delen staan."""
    base_path = canonical_base(base_path)
    remove_all_parts(base_path)
    writer = RotatingAppend(base_path, rotate_bytes, max_parts, log)
    n = 0
    try:
        for line in lines:
            writer.write_line(line)
            n += 1
    finally:
        writer.close()
    return n
