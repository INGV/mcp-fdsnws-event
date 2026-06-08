# Docker image and release strategy

Status: accepted

A single Docker Hub repository — `ingv/mcp-fdsnws-event` — holds **two** artifacts:
the base stdio MCP server (clean tags) and the `mcpo` OpenAPI wrapper (same tags
with a `-mcpo` suffix). The GitHub Actions workflow builds both multi-arch
(linux/amd64, linux/arm64) and tags them as follows:

| Trigger | Base tag | mcpo tag |
|---|---|---|
| Push to `main` | `main` | `main-mcpo` |
| Tag `vX.Y.Z` | `X.Y.Z` (the `v` is stripped) | `X.Y.Z-mcpo` |
| Newest tag | also `latest` | also `latest-mcpo` |
| Pull request | build only, no push | not built |

Semantic-version tags drop the leading `v` (git tag `v1.0.0` → image `1.0.0`) so
image tags follow Docker convention rather than the git tag spelling.

## Consequences

- The mcpo image must not hard-code its parent. `Dockerfile.mcpo` takes an
  `ARG BASE_IMAGE` and the workflow passes the immutable digest of the base image
  it just pushed, so the two artifacts always stay in lockstep.
- **Retention**: on every tag push, a cleanup step keeps only the **5 most recent
  semver versions**, deleting both the base (`X.Y.Z`) and the mcpo (`X.Y.Z-mcpo`)
  tags of older versions. The `latest`, `latest-mcpo`, `main`, and `main-mcpo`
  tags are never deleted. This requires `DOCKER_HUB_ACCESS_TOKEN` to have **delete**
  scope, not just read/write.
- Choosing one repo with a suffix (over two separate repositories) keeps the two
  images discoverable together and lets the retention logic operate on a single
  tag list, at the cost of mixing two artifacts in one tag namespace.
