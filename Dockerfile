# Run-anywhere: zero-dependency Python, python:3.12-alpine keeps it small.
FROM python:3.12-alpine

WORKDIR /app

COPY pyproject.toml README.md ./
COPY alert_routing/ alert_routing/
COPY tests/ tests/
COPY registry.json scenarios/ scenarios/

RUN python -m pip install --no-cache-dir -e . --quiet

# Entry points — tests, CLI, UI
CMD ["python", "-m", "unittest", "discover"]
