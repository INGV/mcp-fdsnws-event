# Feature 001: Aggiungere httpx come dipendenza

**Status:** `todo`
**Depends on:** none
**Blocks:** 003

---

## Description
Aggiunge `httpx` alla lista delle dipendenze in `pyproject.toml`. httpx e una libreria HTTP async-native necessaria per il nuovo tool `fdsn_search_earthquakes` che effettua richieste dirette agli endpoint FDSN con `format=text`, bypassando ObsPy.

## Implementation Steps
1. Aprire `pyproject.toml`
2. Aggiungere `"httpx>=0.27.0"` alla lista `dependencies` (dopo `pydantic`)
3. Verificare che `pip install -e .` funzioni senza errori
4. Verificare che il Docker build funzioni (`docker build -t fdsnws-event-server .`)

## Acceptance Criteria
- [ ] `httpx>=0.27.0` presente in `pyproject.toml` sotto `dependencies`
- [ ] Nessuna modifica alle dipendenze esistenti (mcp, obspy, pydantic)
- [ ] `pip install -e .` completa senza errori
- [ ] Docker build completa senza errori

## Notes & Gotchas
- httpx supporta sia sync che async. Nel progetto useremo `httpx.AsyncClient` per coerenza con il pattern async del server MCP.
- La versione minima 0.27.0 garantisce il supporto stabile per async context manager.

## Change Log

| Date       | Status Change      | Notes              |
|------------|-------------------|--------------------|
| 2026-02-26 | todo → created    | Initial plan entry |
