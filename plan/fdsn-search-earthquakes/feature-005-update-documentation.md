# Feature 005: Aggiornare documentazione

**Status:** `todo`
**Depends on:** 004
**Blocks:** none

---

## Description
Aggiorna README.md e CLAUDE.md per documentare il nuovo tool `fdsn_search_earthquakes`. Il tool va presentato come tool raccomandato per ricerche generiche, posizionato prima di `fdsn_query_earthquakes` nella documentazione.

## Implementation Steps
1. **README.md**:
   - Aggiornare il conteggio tool da 6 a 7 nella sezione "Caratteristiche"
   - Aggiornare il conteggio nella sezione "Strumenti Disponibili"
   - Aggiungere `fdsn_search_earthquakes` come primo tool nella lista (prima di `fdsn_query_earthquakes`), rinumerando i tool successivi
   - Descrizione: "Ricerca leggera di eventi sismici. Restituisce una lista compatta con i campi essenziali (ID, tempo, coordinate, profondita, magnitudo, nome localita). Consigliato per ricerche generiche."
   - Documentare i parametri (stessi di `fdsn_query_earthquakes`)
   - Aggiungere nota che per il dettaglio QuakeML completo si usa `fdsn_query_earthquakes`
2. **CLAUDE.md**:
   - Aggiornare il conteggio da 6 a 7 nella riga "The server exposes N tools"
   - Aggiungere riga nella tabella "MCP tools exposed" per `fdsn_search_earthquakes` (prima di `fdsn_query_earthquakes`)
   - Parametri: stessi di `fdsn_query_earthquakes` + nota "uses format=text, lightweight JSON output"

## Acceptance Criteria
- [ ] README.md: conteggio tool aggiornato a 7
- [ ] README.md: `fdsn_search_earthquakes` documentato come primo tool nella lista
- [ ] README.md: parametri e descrizione completi
- [ ] CLAUDE.md: conteggio tool aggiornato a 7
- [ ] CLAUDE.md: `fdsn_search_earthquakes` aggiunto nella tabella "MCP tools exposed"
- [ ] Nessuna modifica ai tool esistenti nella documentazione

## Notes & Gotchas
- Il nuovo tool va posizionato PRIMA di `fdsn_query_earthquakes` in entrambi i file, per guidare il lettore (e il modello LLM) a preferirlo per ricerche generiche.
- La descrizione deve chiarire la differenza: `fdsn_search_earthquakes` = leggero/veloce, `fdsn_query_earthquakes` = dettaglio completo QuakeML.

## Change Log

| Date       | Status Change      | Notes              |
|------------|-------------------|--------------------|
| 2026-02-26 | todo → created    | Initial plan entry |
