FROM python:3.14-slim-trixie

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iproute2 bind9-dnsutils && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# mape_calc.py, mape-provisioning-server, certs will be mounted or copied
COPY mape-provisioning-server /app/mape-provisioning-server
COPY mape_calc /usr/local/bin/mape_calc.py

RUN chmod +x /app/mape-provisioning-server /usr/local/bin/mape_calc.py

EXPOSE 443

ENTRYPOINT ["/usr/local/bin/python3", "/app/mape-provisioning-server"]
