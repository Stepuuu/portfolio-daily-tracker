FROM python:3.11-slim
WORKDIR /app
COPY engine/scripts/ scripts/
COPY dashboard/core/ledger.py core/ledger.py
# The quote rules the snapshot shares with Today: symbols, trading calendar, close rules, rounding.
COPY dashboard/core/provider_errors.py core/provider_errors.py
COPY dashboard/core/market/ core/market/
COPY engine/portfolio/config.example.json portfolio/config.example.json
RUN pip install --no-cache-dir requests
RUN mkdir -p portfolio/holdings portfolio/snapshots
ENV PORTFOLIO_DIR=/app/portfolio
# Usage: docker compose run engine python3 scripts/portfolio_snapshot.py
CMD ["python3", "scripts/portfolio_snapshot.py"]
