FROM debian:trixie-slim

# Install runtime dependencies
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ppp \
        pppoe \
        iproute2 \
        procps \
        bind9-dnsutils && \
    rm -rf /var/lib/apt/lists/*

# PPPoE server scripts and entrypoint
COPY entrypoint.sh /entrypoint.sh
COPY ip-up /etc/ppp/ip-up
COPY ip-down /etc/ppp/ip-down

RUN chmod +x /entrypoint.sh /etc/ppp/ip-up /etc/ppp/ip-down && \
    mkdir -p /etc/ppp/plugins && \
    ln -sf /usr/lib/pppd/2.5.2/rp-pppoe.so /etc/ppp/plugins/rp-pppoe.so

ENTRYPOINT ["/entrypoint.sh"]
