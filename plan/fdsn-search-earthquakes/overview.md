# fdsn_search_earthquakes — Overview

## Summary

Aggiunge un nuovo tool MCP `fdsn_search_earthquakes` che interroga gli endpoint FDSN con `format=text` via richiesta HTTP diretta (httpx), restituendo un JSON leggero con sole 14 colonne per evento. Copre il 60% delle richieste utente (ricerche geospaziali/temporali generiche) con payload drasticamente ridotti rispetto al QuakeML completo.

## Feature Status

| ID  | Feature                                    | Status      | Depends On | File                                        |
|-----|--------------------------------------------|-------------|------------|---------------------------------------------|
| 001 | Aggiungere httpx come dipendenza           | todo        | —          | feature-001-add-httpx-dependency.md         |
| 002 | Modello Pydantic SearchEarthquakesInput    | todo        | —          | feature-002-pydantic-model.md               |
| 003 | Client HTTP diretto per format=text        | todo        | 001        | feature-003-http-text-client.md             |
| 004 | Registrare tool fdsn_search_earthquakes    | todo        | 002, 003   | feature-004-register-mcp-tool.md            |
| 005 | Aggiornare documentazione                  | todo        | 004        | feature-005-update-documentation.md         |
| 006 | Test end-to-end                            | todo        | 003, 004   | feature-006-end-to-end-tests.md             |

**Statuses:** `todo` · `in-progress` · `done` · `blocked`

## Implementation Order

Features grouped by dependency layer — implement lower layers first.

**Layer 1 (no dependencies):** 001, 002
**Layer 2 (depends on Layer 1):** 003
**Layer 3 (depends on Layer 2):** 004
**Layer 4 (depends on Layer 3):** 005, 006

## Last Updated
2026-02-26
