FROM ghcr.io/axoflow/axosyslog:4.29.0

# パッケージ更新と logrotate のインストール
RUN apk update && apk upgrade && \
    apk add --no-cache logrotate

# 設定ファイルとエントリポイントスクリプトを配置
COPY logrotate.conf /etc/logrotate.d/syslog-ng
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chmod 0644 /etc/logrotate.d/syslog-ng

ENTRYPOINT ["/entrypoint.sh"]
