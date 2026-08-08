#!/bin/bash
# user-data da EC2 (Amazon Linux 2023): docker + compose + diretório da app.
# O deploy (GitHub Action → SSM) só atualiza a imagem; este script prepara o resto.
set -euxo pipefail

dnf update -y
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

# docker compose plugin
DOCKER_CONFIG=/usr/local/lib/docker
mkdir -p "$DOCKER_CONFIG/cli-plugins"
ARCH=$(uname -m)
curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
  -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
ln -sf "$DOCKER_CONFIG/cli-plugins/docker-compose" /usr/libexec/docker/cli-plugins/docker-compose || true

mkdir -p /opt/quote-agent/infra
cd /opt/quote-agent

# Compose e Caddyfile são enviados pelo aws-bootstrap.sh (via SSM) logo após o boot.
# Placeholder do env da imagem até o primeiro deploy:
echo "API_IMAGE=public.ecr.aws/docker/library/hello-world:latest" > image.env
touch .env
