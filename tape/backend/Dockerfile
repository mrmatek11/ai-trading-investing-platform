FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY tape ./tape
RUN pip install ".[postgres]" \
    && useradd --create-home --uid 10001 --user-group app  # „tape” to w Debianie systemowa grupa

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "tape.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
