#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamDataOfficial - grafieken maken van de verzamelde Steam-data
================================================================

Visualisatiestap voor de CSV's die jsonl_to_table.py in data/ zet.
De aanpak (pandas + matplotlib, grafieken wegschrijven naar een map) is
overgenomen uit DataProjectOld/osrs_youtube_project/osrs_visualize.py.

Grafieken:

  1. games_released_timeline  - GEANIMEERDE 'Amount of Steam games'-lijn:
     het cumulatieve aantal games (appids uit games.csv) dat tot elke
     releasedatum is uitgekomen. De lijn groeit frame voor frame over de
     tijd; een stip + teller tonen de stand van de huidige datum.
     Output: games_released_timeline.gif (altijd), .mp4 (alleen als ffmpeg
     aanwezig is) en games_released_timeline.png (statische eindstand).

  2. games_per_weekday         - statische staafgrafiek: aantal games per
     dag-van-week (maandag t/m zondag). De dag-van-week komt uit
     data/date.csv (date-dimensie, day_of_week = ISO 1..7, maandag=1);
     releasedatums buiten het bereik van date.csv (bv. games van vóór
     2003) worden direct uit de datum afgeleid als fallback.

Vereist pandas + matplotlib + pillow (zie requirements.txt) -> draaien
vanuit de .venv (zie repo-conventie: alleen voor scripts met externe
packages; de fetch-scripts blijven stdlib-only):

    python -m venv .venv
    .venv/Scripts/pip install -r requirements.txt
    .venv/Scripts/python visualize_games.py

Gebruik:
    python visualize_games.py
    python visualize_games.py --out-dir graphs
    python visualize_games.py --fps 20 --no-mp4
"""

import argparse
import math
import os
import shutil
import sys
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # headless: schrijf direct naar bestanden, geen GUI

import matplotlib.animation as animation
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd

# Steam-achtig kleurenpalet.
STEAM_DARK = "#1b2838"
STEAM_BLUE = "#66c0f4"
STEAM_LIGHT = "#c7d5e0"
ACCENT = "#e74c3c"

# Volgorde maandag..zondag (labels lowercase, zoals in date.csv).
DAY_LABELS = ["monday", "tuesday", "wednesday", "thursday",
              "friday", "saturday", "sunday"]

DEFAULT_GAMES = "data/games.csv"
DEFAULT_DATE = "data/date.csv"
DEFAULT_OUT = "graphs"
DEFAULT_FPS = 20


# ---------------------------------------------------------------------------
# Inlezen
# ---------------------------------------------------------------------------
def read_csv(path):
    """Lees een csv. De tabellen zijn utf-8-sig (BOM, zoals jsonl_to_table.py
    ze schrijft) zodat Excel de tekst goed toont."""
    return pd.read_csv(path, encoding="utf-8-sig")


def games_with_date(games):
    """games.csv -> alleen rijen met een geldige releasedatum; voegt 'release'
    (datetime) toe. Retourneert (subset, aantal overgeslagen zonder datum)."""
    rel = games.dropna(subset=["release_date_fmt"]).copy()
    rel["release"] = pd.to_datetime(rel["release_date_fmt"], errors="coerce")
    rel = rel.dropna(subset=["release"])
    skipped = len(games) - len(rel)
    return rel, skipped


# ---------------------------------------------------------------------------
# 1. Geanimeerde tijdlijn: amount of steam games (cumulatief per releasedatum)
# ---------------------------------------------------------------------------
def create_games_timeline(games, date_dim, out_dir, fps=DEFAULT_FPS,
                          with_mp4=True):
    """Geanimeerde 'Amount of Steam games'-groeilijn. Schrijft altijd de .gif
    en een statische eindstand (.png); de .mp4 alleen als ffmpeg aanwezig is.
    date_dim wordt niet gebruikt maar houdt de aanroepsignatuur uniform."""
    rel, skipped = games_with_date(games)
    if not len(rel):
        print("   Waarschuwing: geen games met een releasedatum - "
              "tijdlijn overgeslagen")
        return

    # Cumulatief aantal per unieke releasedatum = de 'stappen' van de lijn.
    per_day = rel.groupby("release").size()
    cum = per_day.cumsum()
    x = mdates.date2num(per_day.index)     # releasedatums als getallen
    y = cum.to_numpy(dtype=float)
    total = int(y[-1])
    first_year = per_day.index.min().year
    last_year = per_day.index.max().year
    ymax = int(math.ceil(total / 100.0)) * 100

    fig, ax = plt.subplots(figsize=(11, 6.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Vaste assen over de hele periode, zodat de lijn zichtbaar 'groeit'.
    ax.set_xlim(mdates.date2num(datetime(first_year - 1, 6, 1)),
                mdates.date2num(datetime(last_year + 1, 6, 1)))
    ax.set_ylim(0, ymax)

    ax.set_title("Amount of Steam games over time", fontsize=15,
                 fontweight="bold", color=STEAM_DARK, pad=14)
    ax.set_xlabel("Release date", fontsize=11)
    ax.set_ylabel("Games released (cumulative)", fontsize=11)
    ax.xaxis.set_major_locator(mdates.YearLocator(base=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.grid(True, axis="y", alpha=0.25, color=STEAM_LIGHT)
    ax.spines[["top", "right"]].set_visible(False)

    # Dynamische artiesten: elke frame bijgewerkt.
    (line,) = ax.plot([], [], color=STEAM_BLUE, lw=2.6, zorder=3)
    (dot,) = ax.plot([], [], "o", ms=9, color=ACCENT, zorder=5,
                     markeredgecolor="white", markeredgewidth=1.5)
    (vline,) = ax.plot([], [], color=STEAM_DARK, lw=0.8, alpha=0.35, zorder=1)
    count_txt = ax.text(0.012, 0.94, "", transform=ax.transAxes, fontsize=28,
                        fontweight="bold", color=STEAM_DARK, va="top")
    date_txt = ax.text(0.012, 0.855, "", transform=ax.transAxes, fontsize=12,
                       color="#555555", va="top")
    fig.text(0.99, 0.015,
             f"{total:,} games met bekende releasedatum "
             f"({len(games):,} totaal in games.csv)",
             ha="right", fontsize=9, color="#777777")

    n = len(x)
    area = {"artist": None}

    def draw_upto(i):
        """Teken de stand van de lijn t/m stap i (0-based, i < n)."""
        xi, yi = x[: i + 1], y[: i + 1]
        line.set_data(xi, yi)
        # Vulgebied opnieuw (vorige poly verwijderen om niet te stapelen).
        if area["artist"] is not None:
            area["artist"].remove()
            area["artist"] = None
        area["artist"] = ax.fill_between(xi, 0, yi, color=STEAM_BLUE,
                                         alpha=0.22, zorder=2)
        d = xi[-1]
        dot.set_data([d], [yi[-1]])
        vline.set_data([d, d], [0, ymax])
        count_txt.set_text(f"{int(yi[-1]):,}")
        date_txt.set_text("games released up to "
                          f"{mdates.num2date(d):%d %b %Y}")
        return line, area["artist"], dot, vline, count_txt, date_txt

    anim = animation.FuncAnimation(fig, draw_upto, frames=n,
                                   interval=1000.0 / fps, blit=False,
                                   repeat=True)

    # Altijd een .gif (PillowWriter; Pillow zit in requirements.txt).
    gif_path = os.path.join(out_dir, "games_released_timeline.gif")
    anim.save(gif_path, writer=animation.PillowWriter(fps=fps), dpi=100)
    print(f"   OK {gif_path} ({n} frames)")

    # .mp4 alleen als ffmpeg beschikbaar is (FFMpegWriter).
    if with_mp4:
        if shutil.which("ffmpeg"):
            mp4_path = os.path.join(out_dir, "games_released_timeline.mp4")
            try:
                anim.save(mp4_path,
                          writer=animation.FFMpegWriter(fps=fps,
                                                        bitrate=2000),
                          dpi=100)
                print(f"   OK {mp4_path}")
            except Exception as exc:
                print("   Waarschuwing: mp4 opslaan mislukt "
                      f"({exc}) - alleen de .gif is gemaakt")
        else:
            print("   Waarschuwing: ffmpeg niet gevonden op PATH - geen "
                  ".mp4 (installeer ffmpeg, of gebruik --no-mp4)")

    # Statische eindstand (.png): makkelijk snel bekijken.
    draw_upto(n - 1)
    png_path = os.path.join(out_dir, "games_released_timeline.png")
    fig.savefig(png_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"   OK {png_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 2. Games per dag-van-week (weekday uit date.csv; ISO maandag=1..zondag=7)
# ---------------------------------------------------------------------------
def create_games_per_weekday(games, date_dim, out_dir):
    """Statische staafgrafiek: aantal games per dag-van-week."""
    rel, skipped = games_with_date(games)
    if not len(rel):
        print("   Waarschuwing: geen games met een releasedatum - "
              "weekday-grafiek overgeslagen")
        return

    # Dag-van-week primair uit de date-dimensie (date_fmt -> day_of_week).
    dow_map = dict(zip(date_dim["date_fmt"], date_dim["day_of_week"]))
    rel["dow"] = rel["release_date_fmt"].map(dow_map)

    # Datums buiten het date.csv-bereik (bv. games van vóór 2003) direct uit
    # de datum afleiden als fallback. Let op: pandas 3.x heeft geen
    # .dt.isoweekday() meer - via Timestamp.isoweekday() (per element) werkt
    # in elke pandas-versie.
    missing = rel["dow"].isna()
    if missing.any():
        rel.loc[missing, "dow"] = (rel.loc[missing, "release"]
                                   .map(lambda ts: ts.isoweekday()))
        print(f"   Info: {int(missing.sum())} releases buiten het bereik "
              "van date.csv; dag-van-week direct uit de datum afgeleid")
    rel["dow"] = rel["dow"].astype(int)

    counts = rel["dow"].value_counts().reindex(range(1, 8), fill_value=0)
    labels = [DAY_LABELS[i].capitalize() for i in range(7)]
    values = counts.to_numpy(dtype=float)
    total = int(values.sum())
    mean = total / 7.0 if total else 0.0

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    bars = ax.bar(labels, values, color=STEAM_BLUE, edgecolor="white",
                  width=0.62, zorder=3)
    bars[int(values.argmax())].set_color(STEAM_DARK)  # drukste dag accentueren

    ax.set_ylim(0, max(values) * 1.22)
    ax.set_title("Games released per weekday", fontsize=15, fontweight="bold",
                 color=STEAM_DARK, pad=14)
    ax.set_ylabel("Number of games", fontsize=11)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.grid(True, axis="y", alpha=0.25, color=STEAM_LIGHT, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)

    # Gemiddelde als stippellijn met label.
    if mean > 0:
        ax.axhline(mean, color=ACCENT, lw=1.2, ls="--", alpha=0.75, zorder=2)
        ax.text(len(labels) - 0.4, mean + max(values) * 0.01, f"avg {mean:.0f}",
                color=ACCENT, fontsize=9, va="bottom", ha="right")

    # Waarde + percentage boven elke staaf.
    for bar, v in zip(bars, values):
        pct = 100.0 * v / total if total else 0.0
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(values) * 0.02, f"{int(v):,} ({pct:.1f}%)",
                ha="center", va="bottom", fontsize=10, color=STEAM_DARK)

    path = os.path.join(out_dir, "games_per_weekday.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"   OK {path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Hoofdprogramma
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(
        description="Grafieken maken van de Steam-data in data/.")
    p.add_argument("--games", default=DEFAULT_GAMES,
                   help=f"games.csv (default: {DEFAULT_GAMES})")
    p.add_argument("--date", default=DEFAULT_DATE,
                   help=f"date.csv (default: {DEFAULT_DATE})")
    p.add_argument("--out-dir", default=DEFAULT_OUT,
                   help=f"uitvoermap (default: {DEFAULT_OUT})")
    p.add_argument("--fps", type=int, default=DEFAULT_FPS,
                   help="frames per seconde voor de animatie "
                        f"(default: {DEFAULT_FPS})")
    p.add_argument("--no-mp4", action="store_true",
                   help="geen .mp4 proberen (alleen .gif + .png)")
    args = p.parse_args(argv)

    for path in (args.games, args.date):
        if not os.path.isfile(path):
            print(f"! {path} niet gevonden - draai eerst jsonl_to_table.py")
            sys.exit(1)

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 62)
    print("SteamDataOfficial - grafieken")
    print("=" * 62)

    games = read_csv(args.games)
    date_dim = read_csv(args.date)
    rel, skipped = games_with_date(games)
    print(f"> {len(games):,} games in {args.games} "
          f"({len(rel):,} met releasedatum, {skipped} zonder)")

    charts = [
        ("games_released_timeline", create_games_timeline,
         {"fps": args.fps, "with_mp4": not args.no_mp4}),
        ("games_per_weekday", create_games_per_weekday, {}),
    ]

    for name, func, kwargs in charts:
        print(f"\nGenerating: {name}...")
        try:
            func(games, date_dim, out_dir, **kwargs)
        except Exception as exc:
            print(f"   Fout: {exc}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 62)
    print(f"Klaar - grafieken staan in: {out_dir}/")
    print("=" * 62)


if __name__ == "__main__":
    main()
