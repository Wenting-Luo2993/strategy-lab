FROM eclipse-temurin:17-jre-jammy

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    procps \
    x11vnc \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 ibkr \
    && mkdir -p /opt/ibkr /opt/IBC /opt/ibkr-container /var/log/ibkr /etc/ibkr \
    && chown -R ibkr:ibkr /opt/ibkr /opt/IBC /opt/ibkr-container /var/log/ibkr /home/ibkr

COPY --chown=ibkr:ibkr entrypoint.sh /opt/ibkr-container/entrypoint.sh
COPY --chown=ibkr:ibkr healthcheck.sh /opt/ibkr-container/healthcheck.sh
RUN chmod +x /opt/ibkr-container/entrypoint.sh /opt/ibkr-container/healthcheck.sh

USER ibkr
WORKDIR /opt/IBC

ENTRYPOINT ["/opt/ibkr-container/entrypoint.sh"]
