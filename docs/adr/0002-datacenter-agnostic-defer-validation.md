# Datacenter-agnostic: defer semantic validation to the datacenter

Status: accepted

The server must work with any FDSNWS-Event-compliant datacenter, so it carries no
datacenter-specific behaviour. Client-side input validation (Pydantic) is limited
to **structural** checks — types, value ranges, datetime format, and the
bbox-vs-radial mutual-exclusion guard. All **semantic / cross-field** validation
(e.g. `starttime` < `endtime`, unsupported parameters) is left to the datacenter,
and its error response is passed back to the caller verbatim. We did this because
every FDSN datacenter already returns precise, human-readable error messages, and
duplicating those rules client-side would drift from — and contradict — the
authoritative source, differently for each datacenter.

## Consequences

- `INGV_WADL_PARAMS` and the `_validate_params` filter are removed: they were
  INGV-specific, applied to all datacenters, and silently dropped parameters.
- No INGV-specific references remain in code (parameter descriptions say "FDSN
  event ID", not "INGV event ID"; docstrings and packaging are generic).
- `datacenter` defaults to `INGV` purely as an overridable convenience default, not
  a binding — the code never depends on INGV.
- **Do not "fix" the absence of cross-field validation** (e.g. by adding a
  `starttime < endtime` check) or re-introduce a parameter allow-list: both are
  deliberate. The single exception is the bbox/radial guard, kept as a UX
  guard-rail because it costs no round-trip and prevents ambiguous queries.
- Parameters a datacenter silently ignores are **not** exposed (see the note on
  `eventtype`/`magnitudetype` in the design doc): a filter that silently no-ops
  would mislead the LLM. They will be added once reliably supported.
