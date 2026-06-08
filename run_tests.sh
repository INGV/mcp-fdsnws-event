#!/usr/bin/env bash
# Complete test suite for the FDSNWS Event MCP server.
#
# By default runs the offline unit tests + the MCP protocol smoke test.
# Pass --integration to also run the live tests against INGV (network required).
set -euo pipefail

RUN_INTEGRATION=0
if [[ "${1:-}" == "--integration" ]]; then
    RUN_INTEGRATION=1
fi

IMAGE="fdsnws-event-server"

echo "FDSNWS Event MCP Server - Test Suite"
echo "===================================="

echo ""
echo "Step 1/4: Building Docker image..."
docker build -t "${IMAGE}" .

echo ""
echo "Step 2/4: Unit tests (offline)..."
docker run --rm "${IMAGE}" pytest -q

if [[ "${RUN_INTEGRATION}" -eq 1 ]]; then
    echo ""
    echo "Step 3/4: Integration tests (live INGV)..."
    docker run --rm "${IMAGE}" pytest -q -m integration
else
    echo ""
    echo "Step 3/4: Integration tests SKIPPED (pass --integration to run them)."
fi

echo ""
echo "Step 4/4: MCP protocol smoke test (tools/list)..."
echo -e '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0.0"}}}\n{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}\n{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}' \
    | docker run -i --rm "${IMAGE}"

echo ""
echo "All tests passed."
