# Delegation Bench sandbox world: many resettable fixture sites in one stdlib-only container.
# No dependencies are installed, so the image needs no network at build or run time.
FROM python:3.12.8-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/RapierCraftStudios/delegation-bench" \
      org.opencontainers.image.description="Delegation Bench sandbox world: resettable fixture sites with a /__state oracle" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY sandbox_world /app/sandbox_world

RUN useradd --system --no-create-home --uid 10001 sandbox
USER sandbox

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/robots.txt', timeout=2)"
CMD ["python", "-m", "sandbox_world", "--host", "0.0.0.0", "--port", "8080"]
