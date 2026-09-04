# SteamDataOfficial

Steam-game-catalogus via **uitsluitend officiële Steam-bronnen** — geen
derde partijen (zoals SteamSpy). Deze repo vervangt de oude `SteamData`-repo.

## Twee scripts

**`fetch_games_initial.py`** — de EENMALIGE volledige doorgang. Elke game
komt er precies 1× in `data/games.jsonl` (de master, met `appid_amount`
`<appid>_1`):

1. Vraagt je **API key** (popup-venster) — of leest `--key` / een bewaarde key.
2. Haalt de officiële **app-lijst** op uit de **Web API** met key:
   `IStoreService/GetAppList/v1` (gepagineerd via `max_results` + `last_appid`).
3. **"Nieuw" = appids in de lijst die nog niet in de output staan.**
4. Haalt per nieuwe game de **store-data** op (Storefront API, 1 appid per
   request, `cc=us&l=english` → USD/Engels) plus **`last_seen_player_count`**
   (keyless `GetNumberOfCurrentPlayers`) en schrijft het slanke record weg.

Blijf dit script draaien tot elke game 1× is opgenomen — het is hervatbaar en
incrementeel (zie onder).

**`fetch_new_game_info.py`** — de TIJDLIJN. Per run een extra regel per game
in `data/player_history.jsonl`: zelfde velden, verse `last_seen_player_count`,
doorlopende `appid_amount` (`<appid>_2`, `_3`, ...). Geen API key nodig; er
wordt niet geskipt/gededuped — elke run telt, zodat je per game een tijdlijn
van spelersaantallen krijgt. Gebruik `--limit` om per run te begrenzen.

Zo krijg je per game veel meer dan alleen appid + naam, en alles komt van
Steam zelf.

## Velden per game

`appid`, `type`, `name`, `is_free`, `price_overview` (USD, zoals de API hem
geeft — er wordt **niets omgerekend**), `publishers`, `genres` (namen),
`categories` (namen), `release_date` (zoals de API hem geeft),
`release_date_format` (zelfde datum in `yyyy-mm-dd`), `recommendations_total`,
`last_seen_player_count` (momentopname van het huidige spelersaantal, keyless;
`null` bij geen publieke data) en `appid_amount` (uniek volgnummer per
opname: de masterregel is `<appid>_1`, de tijdlijnregels `<appid>_2`, `_3`, ...).

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
python fetch_games_initial.py                 # popup voor de API key, daarna verwerken
python fetch_games_initial.py --save-key      # key bewaren (data/api_key.txt)
python fetch_games_initial.py --forget-key    # bewaarde key wissen
python fetch_games_initial.py --limit 500     # max. 500 nieuwe appids deze run
python fetch_games_initial.py --report-only   # alleen tonen welke nieuw zijn
python fetch_games_initial.py --reset         # games/blacklist/duplicates.jsonl wissen
python fetch_games_initial.py --blacklist-show            # blacklist tonen
python fetch_games_initial.py --blacklist-add 100 200     # zelf toevoegen
python fetch_games_initial.py --blacklist-remove 100      # eruit halen -> weer 'nieuw'
```

Zonder `--limit` wordt de hele resterende lijst verwerkt. Stop je met Ctrl+C,
dan pakt de volgende run op waar `games.jsonl` gebleven was — het aantal
regels in dat bestand is de voortgang (geen checkpoint-bestand).

### 2) Tijdlijn — zo vaak als je wilt herhalen

```bash
python fetch_new_game_info.py                # alle master-games pollen
python fetch_new_game_info.py --limit 500    # max. 500 games deze run
python fetch_new_game_info.py --report-only  # alleen tonen wat er zou komen
```

Alleen de standaardbibliotheek — geen extra packages, geen venv.

## Output (in `data/`)

| Bestand            | Inhoud                                                       |
| ------------------ | ------------------------------------------------------------ |
| `games.jsonl`      | master: 1 JSON-object per regel, alleen games, elke game 1× (`appid_amount` = `<appid>_1`) — het aantal regels is de voortgang |
| `player_history.jsonl` | tijdlijn: per run een extra regel per game (`<appid>_2`, `_3`, ...) met verse `last_seen_player_count` |
| `blacklist.json`   | appids die niet opnieuw worden geprobeerd (niet-game / geen storepagina / geblokkeerd / handmatig), met reden en datum |
| `duplicates.jsonl` | dubbele titels, per appid 1×, met `duplicate_of` = behouden appid |
| `api_key.txt`      | je bewaarde API key (alleen als je `--save-key` gebruikt)    |

`data/` staat in `.gitignore`.

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
