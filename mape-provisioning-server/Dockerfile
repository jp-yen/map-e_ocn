FROM python:3.15-alpine

RUN apk update && apk upgrade && \
    apk add --no-cache iproute2 bind-tools nftables

WORKDIR /app

# mape_calc.py, mape-provisioning-server, certs will be mounted or copied
COPY mape-provisioning-server /app/mape-provisioning-server
COPY mape_calc /usr/local/bin/mape_calc.py

RUN chmod +x /app/mape-provisioning-server /usr/local/bin/mape_calc.py

EXPOSE 443

ENTRYPOINT ["/usr/local/bin/python3", "/app/mape-provisioning-server"]
