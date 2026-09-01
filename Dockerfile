FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md institutions.yaml ./
COPY dues_lib ./dues_lib
COPY web ./web
COPY remind.py config.yaml ./
# Ensure hatchling can build the wheel inside the image.
RUN uv pip install --system --no-cache ".[postgres]"
ENV PORT=8000
EXPOSE 8000
# Render sets PORT; bind all interfaces for health checks.
CMD ["due-board-web"]
