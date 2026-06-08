# Feature 006: Test end-to-end

**Status:** `todo`
**Depends on:** 003, 004
**Blocks:** none

---

## Description
Aggiunge test end-to-end per il nuovo tool `fdsn_search_earthquakes` e per la funzione `search_events_text()`. I test richiedono connessione internet per interrogare il datacenter INGV. Devono verificare il parsing del formato text, la conversione dei tipi, e la gestione dei casi limite (nessun evento, errori HTTP).

## Implementation Steps
1. Creare un nuovo file test (es. `test_search_text.py`) o aggiungere sezioni ai test esistenti (`simple_test.py`)
2. Implementare i seguenti test:
   - **Test base**: chiamare `search_events_text()` con parametri che restituiscano eventi noti (es. `starttime="2012-05-29T00:00:00"`, `endtime="2012-05-29T23:59:59"`, `minmag=5.0`, datacenter INGV per il terremoto dell'Emilia) e verificare che la lista non sia vuota
   - **Test struttura output**: verificare che ogni dict nella lista abbia tutte le 14 chiavi attese: event_id, time, latitude, longitude, depth_km, author, catalog, contributor, contributor_id, mag_type, magnitude, mag_author, event_location_name, event_type
   - **Test tipi numerici**: verificare che latitude, longitude, depth_km, magnitude siano `float` (non stringhe)
   - **Test nessun evento (HTTP 204)**: chiamare con filtri molto restrittivi (es. `minmag=9.5`, finestra temporale stretta) e verificare che restituisca lista vuota senza errori
   - **Test URL costruito**: verificare che l'URL restituito contenga `format=text` e i parametri corretti
3. Assicurarsi che i test siano eseguibili sia localmente (`python test_search_text.py`) che dentro Docker
4. Verificare che `./run_tests.sh` includa il nuovo test (aggiornare lo script se necessario)

## Acceptance Criteria
- [ ] File test creato con almeno 4 test case (base, struttura, tipi, nessun evento)
- [ ] Test base: `search_events_text()` restituisce lista non vuota per query nota
- [ ] Test struttura: ogni evento ha tutte le 14 chiavi attese
- [ ] Test tipi: latitude, longitude, depth_km, magnitude sono float
- [ ] Test 204: filtri restrittivi restituiscono lista vuota senza errori
- [ ] Test URL: url_string contiene `format=text`
- [ ] `./run_tests.sh` passa con successo (aggiornare script se necessario)
- [ ] Test eseguibili sia localmente che in Docker

## Notes & Gotchas
- I test richiedono connessione internet al datacenter INGV. Se il datacenter e temporaneamente irraggiungibile, i test falliranno — questo e accettabile per test di integrazione.
- Usare eventi storici noti (es. terremoto Emilia 2012-05-29) per avere risultati deterministici.
- Il file `run_tests.sh` potrebbe dover essere aggiornato per includere il nuovo file test.
- Attenzione al rate limiting: non fare troppe richieste in rapida successione.

## Change Log

| Date       | Status Change      | Notes              |
|------------|-------------------|--------------------|
| 2026-02-26 | todo → created    | Initial plan entry |
