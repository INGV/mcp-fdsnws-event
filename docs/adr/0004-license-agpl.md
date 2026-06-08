# License: AGPL-3.0-or-later

Status: accepted

The project is released under the **GNU Affero General Public License v3.0 or
later**. We chose strong copyleft over a permissive license (MIT/Apache-2.0) and
over plain GPL-3.0 for two reasons: it is publicly funded INGV software, where the
intent is that improvements flow back to the community; and the project ships an
`mcpo` deployment mode that exposes the server as a network HTTP service — the
exact "SaaS loophole" that GPL-3.0 leaves open and AGPL closes via its network-use
clause. ObsPy (LGPL-3.0) and the other dependencies (MIT/Apache-2.0) are all
compatible with AGPL-3.0, so the choice carries no dependency conflict.

## Considered Options

- **MIT / Apache-2.0** — maximum adoption, but allows closing the source in
  derivative or hosted products; rejected for publicly funded software.
- **GPL-3.0** — copyleft on distribution, but no obligation when the software is
  run only as a network service; rejected because of the `mcpo` HTTP mode.
- **EUPL-1.2** — strong institutional fit for an EU public administration and
  network-use copyleft; a valid alternative, not chosen in favour of the more
  widely recognised AGPL-3.0.
