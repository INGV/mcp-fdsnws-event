# Feature 004: Registrare tool fdsn_search_earthquakes

**Status:** `todo`
**Depends on:** 002, 003
**Blocks:** 005, 006

---

## Description
Registra il nuovo tool MCP `fdsn_search_earthquakes` in `server.py` con decoratore `@mcp.tool()`. Il tool accetta gli stessi parametri di `fdsn_query_earthquakes`, valida l'input con il modello Pydantic, e chiama `search_events_text()` per ottenere i dati in formato leggero. La descrizione del tool deve guidare il modello LLM a preferirlo per ricerche generiche.

## Implementation Steps
1. Aggiungere gli import necessari in `server.py`:
   - `from .models import SearchEarthquakesInput` (o `QueryEarthquakesInput` se alias)
   - `from .obspy_client import search_events_text`
2. Definire il tool con `@mcp.tool()` decoratore:
   ```python
   @mcp.tool(
       name="fdsn_search_earthquakes",
       description=(
           "Lightweight earthquake search — returns a summary list with essential fields "
           "(event ID, time, location, depth, magnitude, location name).\n\n"
           "Use this tool for general searches: recent events, geographic filtering, "
           "magnitude filtering, time ranges. Results are compact and fast.\n\n"
           "For full QuakeML detail (all origins, magnitudes, station data, amplitudes), "
           "use fdsn_query_earthquakes instead.\n\n"
           "Common usage examples:\n"
           '- Recent events (today): {} (no parameters needed)\n'
           '- Significant events: {"minmag": 4.0, "starttime": "YYYY-MM-DDTHH:MM:SS"}\n'
           '- Geographic area: {"minlat": 41.0, "maxlat": 43.0, "minlon": 12.0, "maxlon": 15.0}\n'
           '- Radial search: {"latitude": 41.9, "longitude": 12.5, "maxradiuskm": 50}\n'
           '- Different datacenter: {"datacenter": "IRIS", "minmag": 5.0}\n\n'
           + _DATACENTER_NOTE
       ),
       annotations=_QUERY_ANNOTATIONS,
   )
   ```
3. Implementare la funzione async con stessa firma di `fdsn_query_earthquakes`:
   ```python
   async def fdsn_search_earthquakes(
       starttime: Optional[str] = None,
       endtime: Optional[str] = None,
       updatedafter: Optional[str] = None,
       minmag: Optional[float] = None,
       maxmag: Optional[float] = None,
       minlat: Optional[float] = None,
       maxlat: Optional[float] = None,
       minlon: Optional[float] = None,
       maxlon: Optional[float] = None,
       mindepth: Optional[float] = None,
       maxdepth: Optional[float] = None,
       latitude: Optional[float] = None,
       longitude: Optional[float] = None,
       minradiuskm: Optional[float] = None,
       maxradiuskm: Optional[float] = None,
       limit: int = 100,
       datacenter: str = "INGV",
   ) -> str:
   ```
4. Logica interna (stessa struttura di `fdsn_query_earthquakes`):
   - Validare input con il modello Pydantic (`SearchEarthquakesInput` o `QueryEarthquakesInput`)
   - Calcolare default starttime/endtime (oggi 00:00:00–23:59:59 UTC)
   - Chiamare `search_events_text()` con i parametri validati
   - Costruire la risposta JSON con envelope: `{ datacenter, query, api_url, pagination, events }`
   - Pagination: `returned_count`, `limit`, `has_more` (stessa logica di `fdsn_query_earthquakes`)
5. Posizionare il tool PRIMA di `fdsn_query_earthquakes` nel file, cosi il modello lo vede per primo nella lista tool

## Acceptance Criteria
- [ ] Funzione `fdsn_search_earthquakes()` decorata con `@mcp.tool()` in `server.py`
- [ ] Nome tool: `fdsn_search_earthquakes`
- [ ] Stessa firma parametri di `fdsn_query_earthquakes` (tutti Optional, stessi default)
- [ ] Descrizione chiara che guida il modello a preferirlo per ricerche generiche
- [ ] Annotations: readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
- [ ] Validazione input con modello Pydantic
- [ ] Default temporale: oggi 00:00:00–23:59:59 UTC
- [ ] Chiama `search_events_text()` da `obspy_client.py`
- [ ] Output JSON con envelope: datacenter, query, api_url, pagination, events
- [ ] Pagination con returned_count, limit, has_more (+ note se has_more)
- [ ] Posizionato prima di `fdsn_query_earthquakes` nel file

## Notes & Gotchas
- La descrizione del tool e cruciale: deve chiarire che questo e il tool "leggero" per ricerche generiche, mentre `fdsn_query_earthquakes` e per il dettaglio QuakeML completo.
- L'errore da `search_events_text()` per datacenter che non supportano `format=text` deve propagarsi al client MCP come errore chiaro.
- Riusare le costanti `_QUERY_ANNOTATIONS` e `_DATACENTER_NOTE` gia definite in `server.py`.

## Change Log

| Date       | Status Change      | Notes              |
|------------|-------------------|--------------------|
| 2026-02-26 | todo → created    | Initial plan entry |
