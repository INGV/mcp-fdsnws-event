# Release Notes

### Release 1.4.0-dev (2026-06-09)
  - . . .

### Release 1.3.0 (2026-06-09)
  - feat: by-id tools now distinguish "event not found" from "event found but the requested sub-resource is absent" via a three-state `found` / `message` contract (ADR-0006)
  - feat: `eventid` parameter descriptions state provenance (must come from a prior `fdsn_query_earthquakes` result) and forbid invented/placeholder values
  - test: add by-id three-state contract tests and a standalone OpenWebUI A/B harness for the eventid-hallucination failure mode
  - docs: document the A/B harness in the README OpenWebUI section
  - chore: make Docker Hub login resilient with retry and backoff

### Release 1.2.0 (2026-06-08)
  - chore: bump version to 1.2.0 (first fully published Docker Hub release)

### Release 1.1.0 (2026-06-08)
  - feat: add Docker Hub CI/CD (multi-arch amd64/arm64), AGPL-3.0-or-later license and project metadata (publiccode.yml, AUTHORS.md)
  - docs: add project context (CONTEXT.md), ADRs and feature planning artifacts
  - ci: bump GitHub Actions to Node.js 24 compatible major versions

### Release 1.0.0 (2026-06-08)
  - Initial release: MCP server wrapping the FDSNWS Event web service via ObsPy, multi-datacenter (INGV, IRIS, EMSC, GFZ, ...). Exposes 6 stdio tools: `fdsn_query_earthquakes` plus by-id detail tools (event, arrivals, all magnitudes, all origins, focal mechanism)
