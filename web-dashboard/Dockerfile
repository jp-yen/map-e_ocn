FROM python:3.11-slim

# Install system utilities: iproute2 (ip command), procps (ps/kill), iputils-ping (ping), sudo
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    procps \
    iputils-ping \
    fping \
    make \
    perl \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# ホストと同じ UID 1000 / GID 1000 の abc123 ユーザーを作成し、docker(989)・kea(101) グループへのアクセス権を設定
RUN groupadd -g 1000 abc123 && \
    useradd -u 1000 -g abc123 -m -s /bin/bash abc123 && \
    (getent group 989 || groupadd -g 989 docker) && \
    (getent group 101 || groupadd -g 101 kea) && \
    usermod -aG docker,kea abc123 && \
    echo "abc123 ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

WORKDIR /app

# Ensure unbuffered python output
ENV PYTHONUNBUFFERED=1

USER abc123

CMD ["python3", "/app/app.py"]
