# PRD: fdsn_search_earthquakes — Lightweight Text-Format Search Tool

## Introduction

Il 60% delle richieste degli utenti sono ricerche geospaziali/temporali generiche ("terremoti di oggi", "M>4 ultima settimana"). Attualmente il tool `fdsn_query_earthquakes` restituisce l'intero albero QuakeML serializzato in JSON, producendo payload pesanti. Lo standard FDSN supporta `format=text` che restituisce un CSV pipe-separated con sole 14 colonne (~50x piu leggero). Si aggiunge un nuovo tool `fdsn_search_earthquakes` che usa `format=text` via richiesta HTTP diretta (senza ObsPy), restituendo un JSON snello. Per il dettaglio completo restano disponibili `fdsn_query_earthquakes` e i tool `*_by_id`.

## Goals

- Ridurre drasticamente la dimensione del payload per le ricerche generiche (da QuakeML completo a ~14 campi per evento)
- Mantenere lo stesso set completo di parametri di filtro di `fdsn_query_earthquakes`
- Usare richieste HTTP dirette con `httpx` (async-native), senza ObsPy, per il parsing del formato text
- Usare ObsPy solo per risolvere i base URL dei datacenter (`URL_MAPPINGS`)
- Restituire un JSON con lo stesso envelope del tool esistente (datacenter, api_url, pagination, events)
- Restituire errore chiaro se un datacenter non supporta `format=text`

## User Stories

### US-001: Aggiungere `httpx` come dipendenza

**Description:** Come sviluppatore, devo aggiungere `httpx` a `pyproject.toml` affinche il nuovo tool possa effettuare richieste HTTP async dirette.

**Acceptance Criteria:**

- [ ] `httpx` aggiunto nella lista `dependencies` di `pyproject.toml`
- [ ] Nessuna modifica alle dipendenze esistenti

### US-002: Implementare il client HTTP diretto per `format=text`

**Description:** Come sviluppatore, implemento in `obspy_client.py` una funzione `search_events_text()` che interroga l'endpoint FDSN con `format=text` via `httpx`, parsa il CSV pipe-separated e restituisce una lista di dict + l'URL usato.

**Acceptance Criteria:**

- [ ] Nuova funzione async `search_events_text()` in `obspy_client.py`
- [ ] Accetta gli stessi parametri di `query_events()` (starttime, endtime, updatedafter, minmag, maxmag, minlat, maxlat, minlon, maxlon, mindepth, maxdepth, latitude, longitude, minradiuskm, maxradiuskm, limit, datacenter)
- [ ] Usa `URL_MAPPINGS` di ObsPy per risolvere il base URL del datacenter (riusa `_get_base_url` esistente o logica equivalente con `_get_client`)
- [ ] Costruisce l'URL con `format=text` e `user=mcp-fdsnws-event`
- [ ] Richiesta HTTP async con `httpx.AsyncClient`
- [ ] HTTP 204 o body vuoto → restituisce lista vuota (non errore)
- [ ] HTTP 400/500 → rilancia errore chiaro
- [ ] Parsa l'header `#EventID|Time|...` per estrarre i nomi delle colonne
- [ ] Converte ogni riga in un dict con chiavi minuscole snake_case: `event_id`, `time`, `latitude`, `longitude`, `depth_km`, `author`, `catalog`, `contributor`, `contributor_id`, `mag_type`, `magnitude`, `mag_author`, `event_location_name`, `event_type`
- [ ] Converte i valori numerici (latitude, longitude, depth_km, magnitude) in float; lascia stringhe vuote come `None`
- [ ] Restituisce `tuple[list[dict], str]` (lista eventi, url_string)

### US-003: Aggiungere il modello Pydantic `SearchEarthquakesInput`

**Description:** Come sviluppatore, creo il modello di validazione input per il nuovo tool. Puo riusare `QueryEarthquakesInput` direttamente oppure essere un alias/subclass se sufficiente.

**Acceptance Criteria:**

- [ ] Modello di validazione per il nuovo tool in `models.py`
- [ ] Stessi parametri e validatori di `QueryEarthquakesInput` (radial vs bbox mutually exclusive, datetime validation, etc.)
- [ ] Se i parametri sono identici, e accettabile riusare `QueryEarthquakesInput` direttamente senza creare un nuovo modello

### US-004: Registrare il tool `fdsn_search_earthquakes` in `server.py`

**Description:** Come sviluppatore, registro il nuovo tool MCP in `server.py` con descrizione chiara che guidi il modello a preferirlo per ricerche generiche.

**Acceptance Criteria:**

- [ ] Nuova funzione `fdsn_search_earthquakes()` decorata con `@mcp.tool()`
- [ ] Nome tool: `fdsn_search_earthquakes`
- [ ] Stessa firma parametri di `fdsn_query_earthquakes` (tutti Optional, stessi default)
- [ ] Descrizione che chiarisca: "Lightweight earthquake search — returns summary list (ID, time, location, magnitude). Use this for general searches. For full QuakeML detail, use fdsn_query_earthquakes."
- [ ] Annotations: `readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True`
- [ ] Logica: valida input con il modello Pydantic → calcola default starttime/endtime (oggi) → chiama `search_events_text()` → restituisce JSON
- [ ] Output JSON con envelope: `{ datacenter, query, api_url, pagination: { returned_count, limit, has_more }, events: [...] }`
- [ ] Ogni evento in `events` ha le 14 colonne del formato text come chiavi

### US-005: Aggiornare documentazione

**Description:** Come sviluppatore, aggiorno README.md e CLAUDE.md per documentare il nuovo tool.

**Acceptance Criteria:**

- [ ] README.md: aggiunto `fdsn_search_earthquakes` nella sezione "Strumenti Disponibili" (prima di `fdsn_query_earthquakes`, come tool raccomandato per ricerche generiche)
- [ ] README.md: aggiornato conteggio tool da 6 a 7
- [ ] CLAUDE.md: aggiunto nella tabella "MCP tools exposed"
- [ ] CLAUDE.md: aggiornato conteggio tool da 6 a 7

### US-006: Aggiungere test per il nuovo tool

**Description:** Come sviluppatore, scrivo un test che verifica il funzionamento end-to-end del nuovo tool (richiede connessione internet a INGV).

**Acceptance Criteria:**

- [ ] Nuovo file test o sezione in test esistente che testa `search_events_text()` direttamente
- [ ] Verifica che la risposta sia una lista di dict con le chiavi attese
- [ ] Verifica che i valori numerici siano float (non stringhe)
- [ ] Verifica gestione HTTP 204 (nessun evento) con filtri molto restrittivi
- [ ] `./run_tests.sh` passa con successo

## Functional Requirements

- FR-1: Il nuovo tool `fdsn_search_earthquakes` usa `httpx` per richieste HTTP async dirette con `format=text`
- FR-2: I base URL dei datacenter vengono risolti tramite `obspy.clients.fdsn.header.URL_MAPPINGS` (usando `_get_client` + `_get_base_url` esistenti)
- FR-3: Il parametro `user=mcp-fdsnws-event` viene aggiunto a tutte le richieste
- FR-4: HTTP 204 e body vuoto vengono trattati come "nessun evento" (lista vuota), non come errore
- FR-5: Se il datacenter restituisce un errore (es. `format=text` non supportato), il tool restituisce un messaggio chiaro che suggerisce di usare `fdsn_query_earthquakes`
- FR-6: Il parsing del CSV pipe-separated e robusto: gestisce righe vuote, header con `#`, valori mancanti
- FR-7: I nomi delle colonne nell'output JSON sono snake_case: `event_id`, `time`, `latitude`, `longitude`, `depth_km`, `author`, `catalog`, `contributor`, `contributor_id`, `mag_type`, `magnitude`, `mag_author`, `event_location_name`, `event_type`
- FR-8: I valori numerici (latitude, longitude, depth_km, magnitude) sono convertiti in `float`; i campi vuoti sono `null` nel JSON
- FR-9: L'envelope JSON di risposta e identico a `fdsn_query_earthquakes`: `{ datacenter, query, api_url, pagination, events }`
- FR-10: Il default temporale (oggi 00:00:00–23:59:59 UTC) e lo stesso di `fdsn_query_earthquakes`

## Non-Goals

- Non sostituisce `fdsn_query_earthquakes`: il tool esistente resta invariato per chi necessita del QuakeML completo
- Non supporta `format=xml` o altri formati: questo tool e esclusivamente per `format=text`
- Non implementa fallback automatico a XML se `format=text` non e supportato
- Non aggiunge caching delle risposte
- Non modifica il comportamento degli altri 6 tool esistenti

## Technical Considerations

- **httpx**: libreria HTTP async-native, va aggiunta a `pyproject.toml` e al `Dockerfile` (gia inclusa se `pip install -e .` viene eseguito)
- **URL_MAPPINGS**: la mappa di ObsPy potrebbe non includere tutti i datacenter che supportano `format=text`. Errore chiaro in caso di incompatibilita
- **Colonne FDSN text**: lo standard definisce esattamente 14 colonne con header `#EventID|Time|Latitude|Longitude|Depth/Km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType`. Alcuni datacenter potrebbero avere variazioni minori
- **Parametri FDSN vs parametri tool**: `minmag` → `minmagnitude`, `minlon` → `minlongitude`, ecc. La mappatura deve essere coerente con `query_events()`
- **Riuso**: `_get_client()`, `_get_base_url()`, `_validate_params()` esistenti vengono riusati per risolvere URL e validare parametri

## Success Metrics

- Il payload di risposta per una ricerca tipica (es. terremoti di oggi in Italia) e almeno 10x piu leggero rispetto a `fdsn_query_earthquakes`
- `./run_tests.sh` passa con successo
- Il modello LLM (Claude) sceglie `fdsn_search_earthquakes` per ricerche generiche e `fdsn_query_earthquakes` solo quando servono dati dettagliati

## Open Questions

- Quali datacenter supportano effettivamente `format=text`? (INGV e IRIS sicuramente, gli altri da verificare)
- Serve un timeout specifico per le richieste httpx? (suggerito: 30s)
- I nomi delle colonne nello standard FDSN sono fissi o possono variare tra datacenter?
