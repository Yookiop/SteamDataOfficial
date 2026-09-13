#!/usr/bin/env python3
"""0_watchdog - kijkt of de dagtaken gedraaid zijn en start wat mist.

GitHub's `schedule` is "best effort": een geplande run kan uren te laat
starten of zelfs HELEMAAL niet verschijnen (gemeten op 13-09-2026: een run van
19:40 NL die nergens terugkwam, ook niet als wachtende). Deze watchdog mag zelf
best laat zijn - hij kijkt of het WERK gedaan is en niet of het de juiste
minuut was - en start een gemiste taak alsnog met een `workflow_dispatch`.

WELKE TAKEN bewaakt hij? Alle bestanden in .github/workflows met precies één
dagelijkse cron (`M H * * *`). Dat zijn nu:

    1_most_popular.yml     20:35 NL
    2_least_popular.yml    04:35 NL
    3_random.yml           12:35 NL

Het bestand van de watchdog zelf (0_watchdog.yml) heeft `7,37 * * * *` en valt
daarmee automatisch buiten de selectie, net als elke andere cron met een
lijst/stap/dag-beperking. Komt er een vierde dagtaak bij, dan wordt die vanzelf
meegenomen - er staat geen lijst met taken in dit script.

BESLISSING per taak:

    slot  = de meest recente keer dat de cron had moeten lopen (vandaag, of
            gisteren als die tijd vandaag nog niet geweest is), in de tijdzone
            die in dat workflowbestand staat;
    klaar = er is een run van dat bestand gestart op of na (slot - LEAD_MIN);
    mist  = niet klaar EN nu >= slot + GRACE_MIN EN het slot is maximaal
            MAX_LATE_H uur oud.

Een run die nog loopt of in de wachtrij staat telt dus gewoon mee, en een
handmatige run vlak vóór de geplande tijd ook (LEAD_MIN). Na een geslaagde
dispatch bestaat die run meteen, dus de volgende controle laat de taak met rust.

GEBRUIK

    python watchdog_check.py                        # controleren en starten
    python watchdog_check.py --report-only          # alleen laten zien
    python watchdog_check.py --runs-file x.json     # offline: run-lijst uit bestand
    python watchdog_check.py --now 2026-09-13T21:40:00+02:00   # 'nu' forceren

Vereist GH_TOKEN (of --token) en REPO (of --repo), behalve in --report-only
zonder echte API-calls. Exitcode: 0 = ok, 1 = een dispatch mislukte, 2 = geen
enkele dagtaak gevonden (dan kijkt de watchdog dus naar niets).
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WF_DIR = ".github/workflows"
DEFAULT_TZ = "Europe/Amsterdam"
GRACE_MIN = 60  # na de geplande tijd zolang wachten voor we ingrijpen
LEAD_MIN = 30   # run die tot zoveel minuten vóór de geplande tijd start telt mee
MAX_LATE_H = 8  # een slot dat ouder is dan dit halen we niet meer in: de taken
                # staan 8 uur uit elkaar, dus de volgende is dan al aan de beurt
                # (inhalen zou alleen dubbel werk en opstopping geven)
API = "https://api.github.com"

CRON_RE = re.compile(r'^\s*-\s*cron:\s*"(\d{1,2}) (\d{1,2}) \* \* \*"\s*$', re.M)
TZ_RE = re.compile(r'^\s*timezone:\s*"([^"]+)"\s*$', re.M)
NAME_RE = re.compile(r'^name:\s*(.+)$', re.M)


def find_jobs(wf_dir):
    """Alle dagtaken uit de workflowbestanden (1 cron per dag, `M H * * *`)."""
    jobs = []
    for fn in sorted(os.listdir(wf_dir)):
        if not fn.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(wf_dir, fn), encoding="utf-8-sig") as fh:
            text = fh.read()
        cron = CRON_RE.search(text)
        if not cron:
            continue
        tz = TZ_RE.search(text)
        nm = NAME_RE.search(text)
        jobs.append({
            "file": fn,
            "name": nm.group(1).strip() if nm else fn,
            # cron is "minuut uur * * *" -> bewaar als (uur, minuut)
            "at": (int(cron.group(2)), int(cron.group(1))),
            "tz": tz.group(1) if tz else DEFAULT_TZ,
        })
    return jobs


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def zone(name):
    """ZoneInfo met begrijpelijke foutmelding (Windows heeft geen tz-database)."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        print("!: tijdzone '%s' niet gevonden. Op Windows: `pip install tzdata`; "
              "op de GitHub-runner zit de tz-database in het OS." % name)
        raise SystemExit(2)


def latest_slot(now, at):
    """Meest recente moment dat de cron had moeten lopen (vandaag of gisteren).

    `at` is (uur, minuut) in de tijdzone van `now`.
    """
    slot = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    while slot > now:
        slot -= timedelta(days=1)
    return slot


def api(url, token, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read()
        return resp.status, (json.loads(payload) if payload else None)


def created_at_list(repo, file, token, runs_file=None):
    """created_at van de laatste runs van dit workflowbestand."""
    if runs_file is not None:
        with open(runs_file, encoding="utf-8-sig") as fh:
            return list(json.load(fh).get(file, []))
    _, data = api("%s/repos/%s/actions/workflows/%s/runs?per_page=20" % (API, repo, file), token)
    return [run["created_at"] for run in data.get("workflow_runs", [])]


def main():
    ap = argparse.ArgumentParser(description="Kijkt of de dagtaken gedraaid zijn en start wat mist.")
    ap.add_argument("--repo", default=os.environ.get("REPO"))
    ap.add_argument("--token", default=os.environ.get("GH_TOKEN"))
    ap.add_argument("--ref", default="main")
    ap.add_argument("--workflows-dir", default=WF_DIR)
    ap.add_argument("--runs-file", help="offline testen: JSON met per bestand een lijst created_at")
    ap.add_argument("--now", help="ISO-tijd om 'nu' te forceren (testen), bijv. 2026-09-13T21:40:00+02:00")
    ap.add_argument("--report-only", action="store_true", help="niets starten, alleen laten zien")
    args = ap.parse_args()

    now = parse_iso(args.now).astimezone(timezone.utc) if args.now else datetime.now(timezone.utc)
    try:
        jobs = find_jobs(args.workflows_dir)
    except FileNotFoundError:
        print("!: map %s niet gevonden - draai dit script vanuit de repo-root." % args.workflows_dir)
        return 2
    if not jobs:
        print("!: geen dagtaak gevonden (geen enkele cron van de vorm `M H * * *`).")
        return 2

    print("> nu %s UTC | %s NL" % (
        now.strftime("%Y-%m-%d %H:%M"),
        now.astimezone(zone(DEFAULT_TZ)).strftime("%Y-%m-%d %H:%M")))
    print("")
    print("| taak | gepland | laatste run | oordeel |")
    print("|------|---------|-------------|---------|")

    missing = []
    done_n = wait_n = old_n = 0
    for job in jobs:
        tz = zone(job["tz"])
        slot = latest_slot(now.astimezone(tz), job["at"])
        cutoff = slot - timedelta(minutes=LEAD_MIN)
        try:
            runs = created_at_list(args.repo, job["file"], args.token, args.runs_file)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print("| %s | %02d:%02d | ? | **fout bij ophalen: %s** |" % (job["name"], *job["at"], exc))
            missing.append(job)  # niet stilzwijgend overslaan: laat het maar misgaan
            continue
        stamps = [parse_iso(r) for r in runs]
        done = [s for s in stamps if s >= cutoff]
        last = max(stamps) if stamps else None
        if done:
            verdict = "klaar"
            done_n += 1
        elif now < slot + timedelta(minutes=GRACE_MIN):
            verdict = "nog even wachten (< %d min na gepland)" % GRACE_MIN
            wait_n += 1
        elif now > slot + timedelta(hours=MAX_LATE_H):
            verdict = "te laat om in te halen (slot > %d u oud)" % MAX_LATE_H
            old_n += 1
        else:
            verdict = "**MIST -> starten**"
            missing.append(job)
        print("| %s | %02d:%02d %s | %s | %s |" % (
            job["name"], job["at"][0], job["at"][1], job["tz"],
            last.astimezone(tz).strftime("%Y-%m-%d %H:%M") if last else "nooit",
            verdict))
    print("")

    if not missing:
        print("> niets te starten: %d klaar, %d wacht nog op de geplande run, "
              "%d te oud om in te halen." % (done_n, wait_n, old_n))
        return 0

    if args.report_only or args.runs_file:
        print("> %d taak/taken zouden nu gestart worden: %s" % (
            len(missing), ", ".join(j["name"] for j in missing)))
        return 0

    failed = 0
    for job in missing:
        try:
            status, _ = api("%s/repos/%s/actions/workflows/%s/dispatches" % (API, args.repo, job["file"]),
                            args.token, method="POST", body={"ref": args.ref})
            print("> %s: workflow_dispatch gestart (HTTP %s)" % (job["name"], status))
        except urllib.error.HTTPError as exc:
            failed += 1
            print("! %s: starten MISLUKT (HTTP %s: %s)" % (job["name"], exc.code, exc.read()[:200].decode(errors="replace")))
        except urllib.error.URLError as exc:
            failed += 1
            print("! %s: starten MISLUKT (%s)" % (job["name"], exc))

    if failed:
        print("! %d van %d dispatches mislukt - volgende ronde opnieuw." % (failed, len(missing)))
        return 1
    print("> volgende controle start die taken vanzelf; deze ronde is klaar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
