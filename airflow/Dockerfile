# Build from the repo root: docker build -f airflow/Dockerfile --target runtime .
#
# Airflow keeps its own pinned Python environment. gcwrapped and dbt live in a second virtualenv
# (/opt/gcw-venv) so dbt's dependencies can never conflict with Airflow's constraints; DAG tasks
# shell out to that venv's binaries.
FROM apache/airflow:3.3.2-python3.12 AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /usr/local/bin/uv

USER root
RUN mkdir -p /opt/gcw /opt/gcw-venv && chown airflow:0 /opt/gcw /opt/gcw-venv
USER airflow

ENV UV_NO_CACHE=1 \
    AIRFLOW__CORE__LOAD_EXAMPLES=false

WORKDIR /opt/gcw
COPY --chown=airflow:0 pyproject.toml uv.lock README.md ./
COPY --chown=airflow:0 src ./src
# migrations/ sits next to src/ because gcwrapped.migrate finds it relative to the package, which
# is why the install below is editable.
COPY --chown=airflow:0 migrations ./migrations
# dbt writes target/, logs/ and dbt_packages/ into the project dir, so it must be airflow-owned.
COPY --chown=airflow:0 dbt ./dbt

# Pinned versions come from uv.lock (runtime deps + the dbt group, no dev tools).
RUN uv venv /opt/gcw-venv --python 3.12 \
 && uv export --frozen --no-dev --group dbt --no-emit-project --no-hashes -o /tmp/gcw-requirements.txt \
 && uv pip install --python /opt/gcw-venv/bin/python -r /tmp/gcw-requirements.txt \
 && uv pip install --python /opt/gcw-venv/bin/python --no-deps -e . \
 && rm /tmp/gcw-requirements.txt

COPY --chown=airflow:0 airflow/dags /opt/airflow/dags
WORKDIR /opt/airflow

# Adds pytest to Airflow's environment and the DAG tests. Not part of the runtime image.
FROM runtime AS test
RUN pip install --no-cache-dir pytest
COPY --chown=airflow:0 airflow/tests /opt/airflow/tests
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "/opt/airflow/tests"]
