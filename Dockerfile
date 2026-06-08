FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for ObsPy (numpy/scipy)
RUN apt-get update && apt-get install -y \
    gcc \
    gfortran \
    libblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .

# Copy source code and tests
COPY src/ src/
COPY tests/ tests/

# Install Python dependencies (including dev extras for pytest)
RUN pip install --no-cache-dir -e ".[dev]"

# Create a non-root user
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

# Expose the MCP server on stdio
CMD ["python", "-m", "fdsnws_event_server.server"]
