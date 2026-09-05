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

> De review-samenvatting kost 1 extra request per game en komt ALTIJD mee
> (geen `--no-reviews`-optie meer) — reviews zijn standaard zo compleet
> mogelijk. Games die al in de basis staan krijgen pas review-velden bij
> een volgende `fetch_new_game_info.py`-run (elke extra-info-regel haalt
> ze vers op) of na `--reset`.

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
aparte momentopname per game is (elke regel heeft ook `DataUpdatedAt` — de
datumtijd waarop die momentopname is geschreven, in **UTC** — handig voor
het verloop van spelersaantallen/reviews over de tijd). De review-velden in
de **basis** zijn de
**laatst bekende** waarden per game (1 record per game), automatisch
gesynct uit de momentopnamen bij elke `fetch_new_game_info.py`-run.

Alleen `type == "game"` wordt opgeslagen. Apps die geen game blijken
(dlc/demo/muziek/software) of geen storepagina hebben, worden automatisch
in `blacklist.json` gezet: die appids worden bij een volgende run niet meer
opgehaald (tijdelijke netwerk/429-fouten worden wél opnieuw geprobeerd).
De blacklist is zelf beheerbaar — via `--blacklist-show` / `--blacklist-add` /
`--blacklist-remove`, of door het bestand te bewerken: een appid eruit halen
maakt hem weer 'nieuw'. Een titel wordt nooit twee keer toegevoegd: dubbele
titels (China/regio-edities) belanden in `duplicates.jsonl`. Games die in de
US-store niet te koop zijn maar elders wel, worden via hun eigen regio
opgenomen en bijgehouden in `us_region_blocked.json` (prijs in de valuta
van die regio). Hoe dat allemaal precies werkt staat in
[Hoe de afhandeling werkt](#hoe-de-afhandeling-werkt-blacklist-duplicates-en-us_region_blocked)
hieronder.

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
python fetch_new_game_info.py --sync-reviews # basis (games.jsonl) bijwerken met
                                             # laatst bekende reviews en stoppen
```

Elke run voegt per game één momentopname toe aan `games_extra_info.jsonl`
(genummerd `_1`, `_2`, ... per game). Alleen de standaardbibliotheek — geen
extra packages, geen venv.

### 3) GitHub Actions (optioneel): de `RUN_STATUS`-marker

`.github/workflows/nightly_fetch.yml` doet hetzelfde automatisch op GitHub:
eerst de bulk (`fetch_games_initial.py`, max 5 uur per run), en alleen als
de catalogus daarna compleet is ook de extra info. Om nooit keihard te
worden afgekapt krijgt het script een duurbudget mee
(`--max-duration-minutes`, gelijk aan de step-timeout) en stopt het zelf
**~5 minuten eerder, netjes afgerond** (laatste records weggeschreven,
exit 0).

Als allerlaatste regel print `fetch_games_initial.py` daarom de status
`RUN_STATUS=...`:

- `RUN_STATUS=complete` — alle 'nieuwe' appids van deze run zijn verwerkt
  (basis in sync met de API-lijst van dit moment; óók als er niets nieuws
  was).
- `RUN_STATUS=partial` — netjes vroegtijdig gestopt (duurbudget, `--limit`,
  Ctrl+C); er zijn nog 'nieuwe' appids over.

De workflow leest die regel uit de log: alleen bij `complete` draait
daarna de extra-info-step; bij `partial` doet de job alleen de eindcommit
en pakt de volgende run de rest op. De status wordt **elke run opnieuw**
bepaald op basis van wat er nog 'nieuw' is — er wordt niets opgeslagen.
Komen er later nieuwe games bij via de API, dan is de run waarin ze
allemaal verwerkt zijn vanzelf weer `complete`.

## Output (in `data/`)

| Bestand            | Inhoud                                                       |
| ------------------ | ------------------------------------------------------------ |
| `games.jsonl`      | basis: 1 JSON-object per regel, alleen games, elke game 1×, **zonder `appid_amount`** — het aantal regels is de voortgang |
| `games_extra_info.jsonl` | extra info (was `player_history.jsonl`): per run een momentopname per game, doorlopend genummerd per game vanaf `<appid>_1` (`_2`, `_3`, ...), met `DataUpdatedAt` = datumtijd van schrijven (**UTC**) |
| `blacklist.json`   | appids die niet opnieuw worden geprobeerd (niet-game / geen storepagina / geblokkeerd / handmatig), met reden en datum |
| `duplicates.jsonl` | dubbele titels, per appid 1×, met `duplicate_of` = behouden appid |
| `us_region_blocked.json` | games die in de US-store niet te koop zijn maar elders wel (via hun eigen regio opgenomen in `games.jsonl`), met `{name, cc, currency, added}` |

> 🔁 2026-09-05: `player_history.jsonl`/`player_history.csv` heten nu
> `games_extra_info.jsonl`/`games_extra_info.csv`, en de per-game nummering
> begint daarin bij `_1` (de basis heeft geen `appid_amount` meer).

`data/` hoort **bewust bij de repo** en staat **niet** in `.gitignore` — de
data wordt mee gecommit. Daarom staat de API key er ook niet in: die leeft
encoded buiten de repo (zie "Eerste doorgang" hierboven).

> 🧮 **CSV's zijn afgeleide bestanden (full refresh).** `games.csv`,
> `game_genres.csv`, `game_categories.csv`, `game_publishers.csv`,
> `date.csv` en `games_extra_info.csv` worden door `jsonl_to_table.py`
> **elke run volledig overschreven** op basis van de actuele jsonl-
> bestanden. Je hoeft ze dus nooit zelf te legen — ook niet na een
> `--reset` of het leeggooien van de json-data: een verse
> `jsonl_to_table.py`-run regenereert alles opnieuw (bij een lege
> `games.jsonl` krijg je `games.csv` met alleen de header, die weer
> meegroeit zodra je opnieuw ophaalt).
>
> ⚠️ **Eén uitzondering:** `games_extra_info.csv` wordt alleen
> (opnieuw) geschreven als `games_extra_info.jsonl` **bestaat én niet
> leeg** is. Is die bron leeg of weg terwijl je de CSV eerder wél hebt
> gegenereerd, dan blijft een **oude** `games_extra_info.csv` staan.
> Wil je een schone lei, verwijder die CSV dan één keer handmatig (de
> rest van de CSV's mag je gewoon laten staan of zelf verwijderen —
> `jsonl_to_table.py` bouwt ze toch opnieuw op).

## 🔁 Bestandsrotatie: ~90 MB per deel (2026-09-05)

GitHub blokkeert pushes van bestanden **> 100 MB**. Datasets die blijven
groeien (de master `games.jsonl` tijdens de bulk, en vooral
`games_extra_info.jsonl` bij elke extra-info-run) worden daarom in delen
van maximaal **~90 MB** bewaard via `data_rotation.py`:

- **Naamgeving:** de basis zonder cijfer, daarna `<naam>_2.<ext>`,
  `<naam>_3.<ext>`, ... t/m `<naam>_5.<ext>` (`MAX_PARTS = 5` — basis +
  4 rotaties van ~90 MB + de git-historie eroverheen ≈ de GitHub-
  vuistregel van ~1 GB). Voorbeelden: `games.jsonl` + `games_2.jsonl`;
  `games_extra_info.jsonl` + `games_extra_info_2.jsonl` ... `_5`.
- **Locken:** zodra een deel door de volgende regel boven ~90 MB zou
  komen, wordt het **gelockt** (er komt nooit meer een regel bij) en
  gaat die regel naar het volgende deel. Een deel dat aan het einde van
  een run net de grens had bereikt, wordt bij de eerste nieuwe regel van
  de volgende run vanzelf gerouleerd.
- **Schrijven:** `fetch_games_initial.py` (`games.jsonl` +
  `duplicates.jsonl`) en `fetch_new_game_info.py`
  (`games_extra_info.jsonl`) schrijven via `RotatingAppend`. Volledige
  herschrijvingen (de master bij `write_master`) verwijderen eerst alle
  oude delen (`rewrite_rotated`) — er blijven dus nooit verouderde delen
  staan.
- **Lezen = samenvoegen:** ál het lezen (diezelfde scripts én
  `jsonl_to_table.py`) ziet de delen als één dataset (union in volgorde
  deel 1, 2, 3, ...). Je geeft overal gewoon het **basispad** op; de
  genummerde delen worden automatisch gevonden. Zo blijft de doorlopende
  `appid_amount`-nummering per game kloppen over de delen heen, en
  produceert `jsonl_to_table.py` één samengevoegde `games_extra_info.csv`
  (en één `games.csv`) — in Power BI is dat dus **één combinatietabel**,
  geen losse CSV's per deel.
- **Limiet bereikt?** Als er al `_5` bestaat en nóg een rotatie nodig is,
  gooit het script een duidelijke fout (`RotationError`) — oude delen
  verwijderen of `MAX_PARTS`/`ROTATE_BYTES` bovenaan `data_rotation.py`
  aanpassen.
- ⚠️ **Dummy-delen (testdata):** `data/games_extra_info.jsonl` + `_2`..`_5`
  bevatten dummy-momentopnamen (Counter-Strike/Valve-games 10/20/440/570/730,
  doorlopende nummering) en `data/games.jsonl` + `games_2`..`games_5`
  bevatten dummy-masterrecords met **fictieve** appids (990000001.., bestaan
  niet op Steam — de echte bulk-run slaat er dus nooit een echte game voor
  over). Ze dienen om de samenvoeglogica te controleren. **Verwijder alle
  dummy-delen vóór de echte runs**: de extra_info-dummy's tellen anders mee
  in de doorlopende nummering van die games (`fetch_new_game_info.py`), en de
  master-dummy's vervuilen anders de master.

## Hoe de afhandeling werkt: blacklist, duplicates en us_region_blocked

`fetch_games_initial.py` verwerkt elke 'nieuwe' appid (uit de officiële
app-lijst, nog niet in `games.jsonl`). Alleen echte games (`type == "game"`)
worden opgeslagen; de rest wordt als volgt afgehandeld.

### Appid zonder US-storepagina (`success:false` op `cc=us`)

1. **Bestaat de game in een andere regio?** Het script checkt
   `REGION_FALLBACKS` (standaard `cc=nl`):
   - **Ja, én dezelfde titel staat nog niet in de master** → de game wordt
     via die regio opgehaald en **gewoon opgenomen** in `games.jsonl`. De
     prijs is dan in de valuta van die regio (bv. **EUR**) — er wordt niets
     omgerekend. Het appid wordt daarnaast bijgehouden in
     `us_region_blocked.json`.
   - **Ja, maar dezelfde titel staat al in de master** → regionaal
     duplicaat (dezelfde game onder een tweede appid) →
     `duplicates.jsonl` met `duplicate_of`.
2. **Bestaat de game nergens?** Dan is het meestal een *legacy-duplicaat*:
   een oud/regio-appid uit de officiële lijst zonder eigen pagina (bv. Max
   Payne `201330` verwijst door naar de echte `12140`). Matcht zijn naam
   een game in de master → `duplicates.jsonl` met `duplicate_of`.
3. **Anders** → echt verwijderd/verdwenen → `blacklist.json` met reden
   `no_store_page`.

Een netwerkfout (geen antwoord van Steam) leidt **nooit** tot een blacklist
of andere classificatie — zo'n appid blijft 'nieuw' en wordt de volgende
run opnieuw geprobeerd.

### `blacklist.json`

Appids die bewust **niet (opnieuw)** worden opgehaald, met reden:

- `not_game:<type>` — geen game (dlc/demo/muziek/software/...)
- `no_store_page` — bestaat (niet meer) als storepagina
- `manual` — zelf toegevoegd met `--blacklist-add`

Beheer: `--blacklist-show`, `--blacklist-add`, `--blacklist-remove` of het
bestand bewerken — een appid eruit halen maakt hem weer 'nieuw'.

### `duplicates.jsonl`

Appids waarvan de titel al door een andere game in de master wordt gedekt
(dubbele/regionale edities én legacy-appids zonder eigen pagina). Eén regel
per appid, met `duplicate_of` = het appid dat in de master is gehouden
(doorgaans het laagste). Zo komt een titel nooit twee keer voor. Een regel
verwijderen maakt dat appid weer 'nieuw' — maar als dezelfde titel nog
steeds in de master staat, wordt het bij de volgende run gewoon opnieuw als
duplicaat geregistreerd.

### `us_region_blocked.json`

Games die in de **US-store niet te koop** zijn maar elders wel. Ze staan
wél in `games.jsonl` (opgehaald via hun eigen regio, in de valuta van die
regio), en dit bestand houdt bij wélke regio/valuta dat was:
`{appid: {name, cc, currency, added}}`. `fetch_new_game_info.py` gebruikt
die `cc` om zulke games bij het pollen óók via hun eigen regio op te halen
(anders zouden ze als 'geen pagina' worden overgeslagen). Vuistregel: elk
record in `games.jsonl` met een niet-USD-prijs hoort hierin te staan — het
script waarschuwt bij de start als dat niet klopt.

### Herstarten

Blacklist, duplicates en us_region_blocked zijn afgeleid van de master en
de huidige Steam-state. Daarom wist `--reset` ze **samen** met
`games.jsonl`: bij een volledige herstart worden ze (vrijwel) hetzelfde
opnieuw opgebouwd — behalve als Steam intussen iets aan zo'n game heeft
aangepast (pagina toegevoegd/verwijderd, naam of regio-beschikbaarheid
gewijzigd).

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
