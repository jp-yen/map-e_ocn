FROM alpine:latest

# ホストOSのパッケージ追加を不要にするため、軽量なAlpine内でiptablesを実行
RUN apk add --no-cache iptables

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
