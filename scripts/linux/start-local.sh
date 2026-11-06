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

# Fermare solo i container Compose della precedente installazione riconosciuta.
# Una copia con gli stessi nomi viene rinominata per lasciare liberi i nomi
# richiesti dal nuovo progetto, senza rimuovere container, volumi o dati.
stop_old_street_skate() {
  local container_id name old_dir service project old_name suffix
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    name="$(docker inspect --format '{{.Name}}' "$container_id")"
    name="${name#/}"
    old_dir="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container_id")"
    service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id")"
    project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$container_id")"

    [[ "$project" == "streets_skate_v2" ]] || continue
    [[ "$service" == "web" || "$service" == "db" ]] || continue
    [[ -n "$old_dir" && "$old_dir" != '<no value>' && "$old_dir" != "$project_root" ]] || continue

    if [[ "$(docker inspect --format '{{.State.Running}}' "$container_id")" == "true" ]]; then
      echo "Arresto la vecchia istanza Street Skate: $name ($old_dir)"
      docker stop "$container_id" >/dev/null
    fi
    if [[ "$name" == "street_skate_web" || "$name" == "street_skate_db" ]]; then
      suffix="${container_id:0:12}"
      old_name="${name}_precedente_${suffix}"
      echo "Conservo il vecchio container come $old_name."
      docker rename "$container_id" "$old_name"
    fi
  done < <(docker ps -aq --filter 'label=com.docker.compose.project=streets_skate_v2')
}

stop_old_street_skate

# Due daemon Docker indipendenti non condividono la lista dei container.
# Non terminare processi sconosciuti: segnalare il conflitto con indicazioni.
if command -v ss >/dev/null 2>&1 && ss -H -ltn '( sport = :5000 )' | grep -q .; then
  current_web="$(docker ps -q --filter 'name=^/street_skate_web$' --filter 'label=com.docker.compose.project.working_dir='"$project_root")"
  if [[ -z "$current_web" ]]; then
    echo "La porta 5000 è occupata da un processo non gestito dal Docker corrente." >&2
    echo "Se il vecchio Street Skate è avviato su un altro Docker, arrestalo da quel Docker e riprova." >&2
    echo "Controlla anche eventuali installazioni Docker di sistema e Docker Snap attive contemporaneamente." >&2
    exit 1
  fi
fi

echo "Docker pronto. Avvio Street Skate su http://localhost:5000"
docker compose up --build
