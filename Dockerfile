# Nightly job image. Build: docker build -t bidadvisor .
FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.8.17
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra funda --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra funda --no-editable

FROM python:3.12-slim
# libgomp1: OpenMP runtime LightGBM links against.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER app
WORKDIR /app
VOLUME /data
ENTRYPOINT ["bidadvisor", "--lake", "/data"]
CMD ["nightly"]
