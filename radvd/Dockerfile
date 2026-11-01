FROM alpine:latest

RUN apk update && apk upgrade && \
    apk add --no-cache radvd procps

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 546/udp

ENTRYPOINT ["/entrypoint.sh"]

