FROM ghcr.io/axoflow/axosyslog:latest

# logrotate と tzdata をインストール
RUN apk add --no-cache logrotate tzdata

# 設定ファイルとエントリポイントスクリプトを配置
COPY logrotate.conf /etc/logrotate.d/syslog-ng
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chmod 0644 /etc/logrotate.d/syslog-ng

ENTRYPOINT ["/entrypoint.sh"]
