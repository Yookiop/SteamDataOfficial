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
python fetch_games_initial.py --report-only   # alleen tonen welke nieuw zijn
python fetch_games_initial.py --reset         # games/blacklist/duplicates.jsonl wissen
python fetch_games_initial.py --blacklist-show            # blacklist tonen
python fetch_games_initial.py --blacklist-add 100 200     # zelf toevoegen
python fetch_games_initial.py --blacklist-remove 100      # eruit halen -> weer 'nieuw'
python fetch_games_initial.py --no-reviews                # review-samenvatting overslaan
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
