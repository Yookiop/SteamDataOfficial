# SteamDataOfficial

Steam-game-catalogus via **uitsluitend officiële Steam-bronnen** — geen
derde partijen (zoals SteamSpy). Deze repo vervangt de oude `SteamData`-repo.

## Drie officiële Steam-bronnen

1. **Appid-lijst** — keyed Web API (`IStoreService/GetAppList/v1`).
2. **Store-data per game** — Storefront API (`/api/appdetails`, keyless).
3. **Review-samenvatting per game** — Review API
   (`store.steampowered.com/appreviews/{appid}`, keyless). Geeft de
   beoordeling zoals op de storepagina: `review_score` (0–10),
   `review_score_desc` (bv. `Overwhelmingly Positive`, `Mixed`, `No user
   reviews`) en de positive/negative/total-tellingen. Opgevraagd met
   `language=all&purchase_type=all&filter=all` → het **totaalbeeld over alle
   talen én inclusief niet-aangeschafte reviews** (cruciaal voor F2P-games:
   zonder `purchase_type=all` toont Warframe bv. 2.871 reviews i.p.v.
   676.084). `num_per_page=0` → alleen de samenvatting, geen review-teksten.

## Twee scripts

**`fetch_games_initial.py`** — de EENMALIGE volledige doorgang. Elke game
komt er precies 1× in `data/games.jsonl` (de **basis** — elke game 1×,
**zonder `appid_amount`**):

1. Vraagt je **API key** (popup-venster) — of leest `--key` / een bewaarde key.
2. Haalt de officiële **app-lijst** op uit de **Web API** met key:
   `IStoreService/GetAppList/v1` (gepagineerd via `max_results` + `last_appid`).
3. **"Nieuw" = appids in de lijst die nog niet in de output staan.**
4. Haalt per nieuwe game de **store-data** op (Storefront API, 1 appid per
   request, `cc=us&l=english` → USD/Engels) plus **`last_seen_player_count`**
   (keyless `GetNumberOfCurrentPlayers`) en de **review-samenvatting**
   (Review API, zie boven) en schrijft het slanke record weg.

> ⚠️ De review-samenvatting kost 1 extra request per game. Overslaan kan
> met `--no-reviews`. Games die al in de basis staan krijgen pas
> review-velden bij een volgende `fetch_new_game_info.py`-run (elke
> extra-info-regel haalt ze vers op) of na `--reset`.

Blijf dit script draaien tot elke game 1× is opgenomen — het is hervatbaar en
incrementeel (zie onder).

**`fetch_new_game_info.py`** — de EXTRA INFO (was: tijdlijn/
`player_history`). Per run een extra regel per game in
`data/games_extra_info.jsonl`: zelfde velden, verse
`last_seen_player_count`, **verse review-samenvatting** (zo zie je ook hoe
een beoordeling over tijd verandert, bv. Mixed → Positive). De doorlopende
`appid_amount` loopt **per game binnen dit bestand vanaf `<appid>_1`**
(eerste regel van een game = `_1`, daarna `_2`, `_3`, ... — de basis telt
niet meer mee). De **basis** (`games.jsonl`) wordt bij elke run meteen
bijgewerkt met de **laatst bekende review-samenvatting** per game (die uit
de momentopnamen); zonder netwerk kan dat ook los met `--sync-reviews`.
Geen API key nodig; er wordt niet geskipt/gededuped — elke run telt.
Gebruik `--limit` om per run te begrenzen.

Zo krijg je per game veel meer dan alleen appid + naam, en alles komt van
Steam zelf.

## Velden per game

`appid`, `type`, `name`, `is_free`, `price_overview` (USD, zoals de API hem
geeft — er wordt **niets omgerekend**), `publishers`, `genres` (namen),
`categories` (namen), `release_date` (zoals de API hem geeft),
`release_date_format` (zelfde datum in `yyyy-mm-dd`), `recommendations_total`,
`last_seen_player_count` (momentopname van het huidige spelersaantal, keyless;
`null` bij geen publieke data), de review-samenvatting (`review_score` 0–10,
`review_score_desc` bv. `Very Positive`/`Mixed`/`No user reviews`,
`review_positive`, `review_negative`, `review_total` — `null` als die request
mislukte).

> 🔁 **Steam-lancering-clamp:** Steam is op **12 september 2003** gelanceerd.
> Games met een releasedatum vóór die datum (de API geeft bv. Half-Life als
> 1998) zijn pas bij de lancering op Steam verschenen en krijgen daarom
> `release_date_format = 2003-09-12` + `release_date = "12 Sep, 2003"`. Dit
> zit in beide fetch-scripts (`clamp_steam_launch` in `slim_record`); de
> eerder verzamelde data is eenmalig op die manier gemigreerd (min
> releasedatum in `games.csv` is nu 2003-09-12).

De **basis** (`games.jsonl`/`games.csv`) heeft géén `appid_amount` — elke
appid komt er maar 1× in voor. De doorlopende per-game nummering
(`appid_amount` = `<appid>_1`, `_2`, `_3`, ...) leeft uitsluitend in
`games_extra_info.jsonl`/`.csv` (was `player_history`), waar elke regel een
aparte momentopname per game is. De review-velden in de **basis** zijn de
**laatst bekende** waarden per game (1 record per game), automatisch
gesynct uit de momentopnamen bij elke `fetch_new_game_info.py`-run.

Alleen `type == "game"` wordt opgeslagen. Apps die geen game blijken
(dlc/demo/muziek/software) of geen storepagina hebben, worden automatisch
in `blacklist.json` gezet: die appids worden bij een volgende run niet meer
opgehaald (tijdelijke netwerk/429-fouten worden wél opnieuw geprobeerd).
De blacklist is zelf beheerbaar — via `--blacklist-show` / `--blacklist-add` /
`--blacklist-remove`, of door het bestand te bewerken: een appid eruit halen
maakt hem weer 'nieuw'. Een titel wordt nooit twee keer toegevoegd: dubbele
titels (China/regio-edities) belanden in `duplicates.jsonl`.

## Gebruik

### 1) Eerste doorgang — elke game 1× (master)

```bash
python fetch_games_initial.py                 # popup voor de API key (wordt daarna automatisch bewaard)
python fetch_games_initial.py --key <key>     # key meegeven (wordt ook automatisch bewaard)
python fetch_games_initial.py --forget-key    # bewaarde key wissen
python fetch_games_initial.py --limit 500     # max. 500 nieuwe appids deze run
```

🔑 De API key hoef je maar **één keer** in te voeren (popup, of via
`--key`): hij wordt daarna automatisch **encoded** (XOR+base64, obfuscatie)
bewaard in `%APPDATA%\SteamDataOfficial\api_key.txt` — **buiten de repo**,
zodat hij nooit mee gecommit kan worden. Een volgende run leest hem daar
gewoon weer uit en vraagt niet opnieuw. Wissen kan met `--forget-key`
(eigen pad via `--keyfile`).

Zonder `--limit` wordt de hele resterende lijst verwerkt. Stop je met Ctrl+C,
dan pakt de volgende run op waar `games.jsonl` gebleven was — het aantal
regels in dat bestand is de voortgang (geen checkpoint-bestand).

### 2) Extra info — zo vaak als je wilt herhalen

```bash
python fetch_new_game_info.py                # alle master-games pollen
python fetch_new_game_info.py --limit 500    # max. 500 games deze run
python fetch_new_game_info.py --report-only  # alleen tonen wat er zou komen
python fetch_new_game_info.py --no-reviews   # review-samenvatting overslaan
python fetch_new_game_info.py --sync-reviews # basis (games.jsonl) bijwerken met
                                             # laatst bekende reviews en stoppen
```

Elke run voegt per game één momentopname toe aan `games_extra_info.jsonl`
(genummerd `_1`, `_2`, ... per game). Alleen de standaardbibliotheek — geen
extra packages, geen venv.

## Output (in `data/`)

| Bestand            | Inhoud                                                       |
| ------------------ | ------------------------------------------------------------ |
| `games.jsonl`      | basis: 1 JSON-object per regel, alleen games, elke game 1×, **zonder `appid_amount`** — het aantal regels is de voortgang |
| `games_extra_info.jsonl` | extra info (was `player_history.jsonl`): per run een momentopname per game, doorlopend genummerd per game vanaf `<appid>_1` (`_2`, `_3`, ...) |
| `blacklist.json`   | appids die niet opnieuw worden geprobeerd (niet-game / geen storepagina / geblokkeerd / handmatig), met reden en datum |
| `duplicates.jsonl` | dubbele titels, per appid 1×, met `duplicate_of` = behouden appid |

> 🔁 2026-09-05: `player_history.jsonl`/`player_history.csv` heten nu
> `games_extra_info.jsonl`/`games_extra_info.csv`, en de per-game nummering
> begint daarin bij `_1` (de basis heeft geen `appid_amount` meer).

`data/` hoort **bewust bij de repo** en staat **niet** in `.gitignore` — de
data wordt mee gecommit. Daarom staat de API key er ook niet in: die leeft
encoded buiten de repo (zie "Eerste doorgang" hierboven).

## Visualisatie (`viz/` — HTML-app, Backgrounds-stijl)

In `viz/` staat een HTML-app die de grafieken van de CSV's in `data/` in de
browser toont (geen GIF — de animatie speelt live af en je exporteert een
**MP4**):

- **Amount of Steam games over time** — geanimeerde cumulatieve tijdlijn:
  het aantal games (appids uit `games.csv`) dat tot elke releasedatum is
  uitgekomen. De tijdcursor loopt over de hele periode en voegt per release
  games toe aan de lijn; bij de teller staat alleen maand + jaar (geen dag).
  Boven de grafiek staat de bediening: ▶ Play/pause, ⟲ Restart, een
  instelbare **Duration** (10–120 s), de kleurkiezers en de knop
  **⬇ Export MP4** (neemt één volledige cyclus op via canvas +
  MediaRecorder — **geen ffmpeg nodig**) die
  `steam_games_released_timeline.mp4` downloadt.
- **Games released per weekday** — statische staafgrafiek: aantal games per
  dag-van-week (de aantallen staan in de y-as; boven elke staaf staat het
  percentage). Dag-van-week primair uit `date.csv` (`day_of_week_label`,
  ISO maandag=1); releasedatums buiten het bereik van `date.csv` worden
  direct uit de datum berekend (sinds de 2003-09-12-clamp geen enkele meer).

Donker thema; de **hele interface is Engels**. Kleuren zijn **per grafiek**
instelbaar: boven de tijdlijn **Line** (lijn/oppervlak + teller) en
**Accent** (stip); boven de weekday-grafiek een eigen **Color** (alle
balken). Elke grafiek heeft daarnaast zijn eigen rij met **Axis** (grootte
10–44), **Bold** en **Color** voor de x-/y-aswaarden, plus een **Y label**
toggle (verbergt bv. het label "Amount"). Die as-instellingen zijn
gedeeld: ze werken op alle grafieken (ook toekomstige).

Draaien (fetch op de CSV's werkt niet vanaf `file://`, dus via een lokale
server):

```bash
python -m http.server 8090
# open http://localhost:8090/viz/index.html
```

De pagina leest `data/games.csv` + `data/date.csv` bij elke keer laden, dus
na een verse run van de fetch-scripts + `jsonl_to_table.py` staat er meteen
de nieuwste data in. Houd het tabblad zichtbaar tijdens een MP4-opname.

## ⚠️ Belangrijk

- Het **100.000 requests/dag-limiet** uit de Terms of Use geldt voor de
  keyed Web API. De Storefront API (per-game data) heeft geen key en geen
  officieel daglimiet, maar throttlet per IP (HTTP 429 → het script pauzeert
  en probeert opnieuw; blijft een request mislukken, dan stopt de run en
  blijven die appids 'nieuw'). Er gaat 1 request per appid, dus grote
  aantallen lopen over meerdere runs (Steam throttlet na ruwweg ~250
  requests per paar minuten).
- App-lijst: primair `IStoreService/GetAppList/v1`. Zonder/ongeldige key
  geeft hij HTTP 403; is hij uit (404), dan valt het script terug op
  `ISteamApps/GetAppList/v2`.
- De officiële app-lijst geeft zelf **geen** releasedatums/types — die komen
  per game uit de Storefront API (zie "Velden per game").
