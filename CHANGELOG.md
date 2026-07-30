# Release Notes

### Release 1.4.0 (2026-07-30)
  - fix: `eventid` is now an opaque string instead of an integer, which unlocks the GFZ and USGS identifier conventions and fixes silent corruption of EMSC identifiers containing underscores (`20240101_0000328` was rewritten to `202401010000328`, so a valid event was reported as absent). A JSON integer is still accepted and normalised (ADR-0007)
  - fix: extract the event id from non-INGV resource identifiers, replacing an INGV-specific pattern that returned a query-string fragment for USGS
  - fix: correct the supported Python versions and pin dependencies: `requires-python >= 3.11`, `mcp >= 1.7.0` (the previous `>= 1.0.0` was unsatisfiable, since `mcp.server.fastmcp` and `ToolAnnotations` appeared in 1.6.0 and 1.7.0), plus bounded ranges for obspy, pydantic and requests
  - build: add `constraints.txt` pinning the full transitive dependency set, generated inside the `python:3.11-slim` release image
  - ci: run the offline test suite on push and pull request, on Python 3.11, 3.12 and 3.13
  - ci: build docker images on pull requests to develop
  - ci(workflows): trigger docker build on develop branch
  - test: add provider fixtures for EMSC, GFZ and USGS, so search-path behaviour stays reproducible when a service is unavailable
  - test: cover multi-datacenter parsing and error mapping offline
  - test: parametrise live tests over INGV, EMSC, GFZ and USGS; the suite grows from 33 offline / 4 live to 72 offline / 27 live
  - docs: replace IRIS with USGS as an advertised datacenter, the IRIS/EarthScope FDSNWS-Event service having been retired (HTTP 410). `IRIS` is still accepted and returns the upstream error verbatim

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
  - ci: bump GitHub Actions to Node.js 24 compatible major versions

### Release 1.0.0 (2026-06-08)
  - Initial release: MCP server wrapping the FDSNWS Event web service via ObsPy, multi-datacenter (INGV, IRIS, EMSC, GFZ, ...). Exposes 6 stdio tools: `fdsn_query_earthquakes` plus by-id detail tools (event, arrivals, all magnitudes, all origins, focal mechanism)
