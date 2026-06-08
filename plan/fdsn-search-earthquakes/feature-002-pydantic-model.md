# Feature 002: Modello Pydantic SearchEarthquakesInput

**Status:** `todo`
**Depends on:** none
**Blocks:** 004

---

## Description
Definisce (o riusa) il modello Pydantic di validazione input per il nuovo tool `fdsn_search_earthquakes`. Poiche i parametri sono identici a `QueryEarthquakesInput`, si puo riusare direttamente quel modello senza crearne uno nuovo. Questa feature consiste nel verificare che il modello esistente sia sufficiente e, se necessario, creare un alias o subclass.

## Implementation Steps
1. Verificare che `QueryEarthquakesInput` in `models.py` copra tutti i parametri necessari: starttime, endtime, updatedafter, minmag, maxmag, minlat, maxlat, minlon, maxlon, mindepth, maxdepth, latitude, longitude, minradiuskm, maxradiuskm, limit, datacenter
2. Se i parametri sono identici (lo sono), riusare `QueryEarthquakesInput` direttamente nel nuovo tool — aggiungere solo un alias `SearchEarthquakesInput = QueryEarthquakesInput` in `models.py` per chiarezza semantica
3. Aggiornare l'import in `models.py` `__init__` o export se necessario

## Acceptance Criteria
- [ ] `SearchEarthquakesInput` disponibile come alias di `QueryEarthquakesInput` in `models.py`
- [ ] L'alias e importabile: `from .models import SearchEarthquakesInput`
- [ ] Tutti i validatori esistenti (radial vs bbox, datetime format) si applicano anche tramite l'alias
- [ ] Nessuna modifica alla logica di `QueryEarthquakesInput`

## Notes & Gotchas
- Un semplice alias `SearchEarthquakesInput = QueryEarthquakesInput` e sufficiente. Non serve una subclass perche i parametri e le validazioni sono identici.
- Se in futuro i due tool divergessero nei parametri, l'alias puo essere sostituito con una classe dedicata senza breaking change.

## Change Log

| Date       | Status Change      | Notes              |
|------------|-------------------|--------------------|
| 2026-02-26 | todo → created    | Initial plan entry |
