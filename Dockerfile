FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md ./
COPY dues_lib ./dues_lib
COPY web ./web
COPY remind.py config.yaml ./
RUN uv pip install --system ".[postgres]"
ENV PORT=8000
EXPOSE 8000
CMD ["usyd-due-web"]
