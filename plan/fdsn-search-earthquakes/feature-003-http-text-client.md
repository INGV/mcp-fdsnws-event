# Feature 003: Client HTTP diretto per format=text

**Status:** `todo`
**Depends on:** 001
**Blocks:** 004, 006

---

## Description
Implementa in `obspy_client.py` una nuova funzione async `search_events_text()` che interroga l'endpoint FDSN con `format=text` usando `httpx.AsyncClient` (senza ObsPy per il fetch/parsing). Usa ObsPy solo per risolvere il base URL del datacenter tramite `_get_client()` + `_get_base_url()`. Parsa il CSV pipe-separated restituito dal server FDSN e lo converte in una lista di dict Python.

## Implementation Steps
1. Aggiungere `import httpx` in cima a `obspy_client.py`
2. Definire la costante `TEXT_COLUMNS` con la mappatura nome-colonna FDSN → chiave snake_case:
   ```python
   TEXT_COLUMNS = [
       "event_id", "time", "latitude", "longitude", "depth_km",
       "author", "catalog", "contributor", "contributor_id",
       "mag_type", "magnitude", "mag_author", "event_location_name", "event_type",
   ]
   ```
3. Definire la costante `NUMERIC_FIELDS` per i campi da convertire in float:
   ```python
   NUMERIC_FIELDS = {"latitude", "longitude", "depth_km", "magnitude"}
   ```
4. Implementare una funzione helper `_parse_text_response(text: str) -> list[dict]` che:
   - Splitta per righe, ignora righe vuote e la riga header (inizia con `#`)
   - Per ogni riga di dati, splitta per `|` e mappa alle chiavi `TEXT_COLUMNS`
   - Converte i campi in `NUMERIC_FIELDS` a `float` (o `None` se vuoti)
   - Converte i campi stringa vuoti a `None`
   - Restituisce la lista di dict
5. Implementare la funzione async `search_events_text()` con stessa firma di `query_events()`:
   ```python
   async def search_events_text(
       starttime=None, endtime=None, updatedafter=None,
       minmag=None, maxmag=None, minlat=None, maxlat=None,
       minlon=None, maxlon=None, mindepth=None, maxdepth=None,
       latitude=None, longitude=None,
       minradiuskm=None, maxradiuskm=None,
       limit=None, datacenter="INGV",
   ) -> tuple[list[dict], str]:
   ```
6. Dentro `search_events_text()`:
   - Usare `_get_client(datacenter)` + `_get_base_url()` per ottenere il base URL
   - Costruire i parametri di query (stessa mappatura di `query_events`: minmag→minmagnitude, ecc.)
   - Aggiungere `format=text` e `user=mcp-fdsnws-event` ai parametri
   - Validare con `_validate_params()`
   - Costruire `url_string` per logging
   - Effettuare la richiesta con `httpx.AsyncClient` (timeout 30s)
   - HTTP 200 con body → parsare con `_parse_text_response()`
   - HTTP 204 o body vuoto → restituire lista vuota
   - HTTP 4xx/5xx → raise `ValueError` con messaggio chiaro (suggerire `fdsn_query_earthquakes` come alternativa)
   - Restituire `(list[dict], url_string)`

## Acceptance Criteria
- [ ] Funzione `search_events_text()` in `obspy_client.py` con stessa firma di `query_events()`
- [ ] Usa `httpx.AsyncClient` per la richiesta HTTP (non ObsPy)
- [ ] Usa `_get_client()` + `_get_base_url()` per risolvere il datacenter URL
- [ ] Aggiunge `format=text` e `user=mcp-fdsnws-event` ai parametri
- [ ] Parsa correttamente il CSV pipe-separated in lista di dict
- [ ] Chiavi output: event_id, time, latitude, longitude, depth_km, author, catalog, contributor, contributor_id, mag_type, magnitude, mag_author, event_location_name, event_type
- [ ] Valori numerici (latitude, longitude, depth_km, magnitude) convertiti in float
- [ ] Campi vuoti convertiti in None
- [ ] HTTP 204 / body vuoto → lista vuota (non errore)
- [ ] HTTP 4xx/5xx → ValueError con messaggio chiaro
- [ ] Timeout di 30 secondi sulla richiesta HTTP
- [ ] Funzione esportata (aggiunta al modulo)

## Notes & Gotchas
- La mappatura parametri deve essere identica a `query_events()`: `minmag`→`minmagnitude`, `minlon`→`minlongitude`, `minlat`→`minlatitude`, ecc.
- Alcuni datacenter potrebbero non supportare `format=text`. L'errore HTTP risultante (tipicamente 400) deve essere catturato e trasformato in un messaggio utile.
- Il formato text FDSN ha esattamente 14 colonne. Se una riga ne ha meno, gestire gracefully (padding con None).
- `httpx.AsyncClient` va usato come context manager (`async with`) per gestire correttamente le connessioni.

## Change Log

| Date       | Status Change      | Notes              |
|------------|-------------------|--------------------|
| 2026-02-26 | todo → created    | Initial plan entry |
