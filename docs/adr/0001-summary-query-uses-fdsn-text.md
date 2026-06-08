# Summary queries use FDSN text format; detail tools use QuakeML

Status: accepted

`fdsn_query_earthquakes` fetches results with FDSN `format=text` (one line per
event, preferred origin and magnitude only) and returns them to the MCP client as
a compact tabular JSON object (`columns` + `rows`), parsed from the response
header line. The `*_by_id` detail tools keep fetching full QuakeML via ObsPy and
serialize the complete object tree. We chose this to stay within the LLM context
budget: serializing the full QuakeML tree for every event in a list could reach
hundreds of KB and saturate the model's context window, while the text format
carries everything needed for the common "list events" case.

## Considered options

- **Full QuakeML for every event in the list** — rejected: token cost is
  unbounded with `limit` and defeats context economy.
- **Client-side summary built from QuakeML** — rejected: still pays the full
  download + parse cost; the FDSN text format already provides the summary natively.

## Consequences

- The query path bypasses ObsPy's QuakeML parsing. Raw text is captured via
  `Client.get_events(format="text", filename=<buffer>)` and parsed by us, so we
  keep ObsPy's HTTP layer (datacenter URL mapping, timeout, 204 handling) without
  the heavyweight serialization.
- **Depth is in kilometres in summaries** (FDSN text `Depth/Km` column) but in
  **metres in the detail tools** (QuakeML). This divergence is deliberate and must
  stay documented.
- The summary exposes only the preferred origin/magnitude. Alternative solutions,
  picks, arrivals and focal mechanisms remain the job of the `*_by_id` tools.
