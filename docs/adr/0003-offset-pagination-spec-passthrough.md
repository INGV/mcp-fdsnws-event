# Offset pagination is FDSN-spec passthrough; no client-side workaround

Status: accepted

`fdsn_query_earthquakes` exposes `offset` and `limit` as straight FDSN passthrough
parameters and reports `returned_count`, `has_more` and a suggested
`next_offset = offset + returned_count`. We pass `offset` through with the
semantics defined by the FDSN spec (1-based start position) and do **not** add any
client-side compensation, even though INGV's implementation is off-by-one.

## Context

Empirically, INGV treats `offset` as a 0-based skip count rather than the spec's
1-based start position: with `limit=1`, `offset=1` returns the *second* event, not
the first (verified against `http://webservices.ingv.it/fdsnws/event/1/query`).
A spec-compliant client paginating from the default `offset=1` therefore silently
loses the first event on INGV. This deviation has been reported to the INGV service
maintainers.

## Why no workaround

- Compensating for INGV's off-by-one (e.g. sending `offset-1`) would **break**
  datacenters that implement the spec correctly — incompatible with ADR-0002
  (datacenter-agnostic).
- The bug belongs to the server and must be fixed there, not masked in every client.

## Consequences

- `offset` is **omitted from the request when it equals its default (1)**. Omitting
  is equivalent to the spec default, so it changes nothing on spec-compliant
  datacenters, but on INGV it avoids the off-by-one silently dropping the most recent
  event on the common single-page query (the flagship "events today" case). This is
  not bug compensation — we simply do not send a redundant default-valued parameter.
- Consequence of the above on INGV only: when paginating across pages, the page-1
  (offset omitted) → page-2 (explicit `offset`) boundary can skip exactly one event,
  because INGV's skip semantics differ from the spec at that boundary. Single-page
  queries are unaffected. This fuzz disappears once INGV fixes the off-by-one.
- `next_offset` is a suggestion, not a guarantee: FDSN `format=text` provides no
  total count, so `has_more` is a heuristic (`returned_count == limit`).
