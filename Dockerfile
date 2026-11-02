# syntax=docker/dockerfile:1
# Image multi-arch (linux/arm64 pour Raspberry Pi, linux/amd64). Même base que commute-alerts.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data /config && chown app:app /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY weather_alerts.py ./
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Paris \
    DATA_DIR=/data \
    WEATHER_CONFIG=/config/config.yaml
USER app
VOLUME ["/data"]
HEALTHCHECK --interval=5m --timeout=10s --start-period=5m --retries=3 \
    CMD ["python", "weather_alerts.py", "--healthcheck"]
ENTRYPOINT ["python", "weather_alerts.py"]
CMD ["--loop"]
