FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Runtime deps + the dashboard group only; no dev tools, no dbt.
RUN uv sync --frozen --no-dev --group dashboard
COPY dashboard ./dashboard
# Streamlit reads .streamlit/config.toml from the working directory (showErrorDetails = false).
COPY .streamlit ./.streamlit
EXPOSE 8501
CMD ["uv", "run", "--no-sync", "streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
