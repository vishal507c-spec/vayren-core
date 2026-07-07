FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH="/app/01_foundation:/app/02_platform:/app/03_market:/app/04_signals:/app/05_strategies:/app/06_risk:/app/07_execution:/app/08_portfolio:/app/09_analytics:/app/10_research:/app/11_interfaces:/app/12_infrastructure:/app/13_knowledge"

FROM base AS builder
COPY pyproject.toml .
RUN pip install --user .

FROM base AS runtime
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
CMD ["uvicorn", "interfaces.api.server:create_app", "--host", "0.0.0.0", "--port", "8000"]
