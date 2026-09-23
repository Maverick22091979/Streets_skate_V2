#!/usr/bin/env bash

set -Eeuo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ ! -f "$project_root/.env" ]]; then
  echo "Configurazione locale mancante: $project_root/.env" >&2
  echo "Creala con: cp .env.example .env" >&2
  echo "Poi configura i file richiesti in docker/secrets (vedi README.md)." >&2
  exit 1
fi

required_secrets=(postgres_password database_url app_secret_key admin_password)
missing_secrets=()
for secret in "${required_secrets[@]}"; do
  [[ -f "$project_root/docker/secrets/$secret" ]] || missing_secrets+=("$secret")
done

if (( ${#missing_secrets[@]} > 0 )); then
  echo "Secret Docker mancanti in $project_root/docker/secrets:" >&2
  printf '  - %s\n' "${missing_secrets[@]}" >&2
  echo "Segui la sezione 'Avvio rapido' del README.md per crearli." >&2
  exit 1
fi

docker_ready() {
  docker version --format '{{.Server.Version}}' >/dev/null 2>&1
}

start_docker() {
  # Docker Desktop for Linux exposes this subcommand on recent installations.
  if docker desktop --help >/dev/null 2>&1; then
    docker desktop start >/dev/null 2>&1 || true
    return
  fi

  # Native Docker installations normally provide a systemd service.
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files docker.service >/dev/null 2>&1; then
    sudo systemctl start docker
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker non trovato. Installa Docker (o Docker Desktop) e riprova." >&2
  exit 1
fi

if ! docker_ready; then
  start_docker

  echo "Attendo l'avvio di Docker..."
  deadline=$((SECONDS + 180))
  until docker_ready; do
    if (( SECONDS >= deadline )); then
      echo "Docker non e disponibile dopo 3 minuti." >&2
      exit 1
    fi
    sleep 3
  done
fi

cd "$project_root"
echo "Docker pronto. Avvio Street Skate su http://localhost:5000"
docker compose up --build
