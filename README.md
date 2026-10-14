# Street Skate

Web app FastAPI con login OAuth, import percorsi sportivi e persistenza su Postgres.

## Stack

- FastAPI
- PostgreSQL
- Docker Compose
- OAuth provider adapters per Strava, MapMyRun, adidas Running

## Provider

- `Strava`: supporto operativo per OAuth e import attività
- `MapMyRun`: supporto operativo per OAuth e import workout
- `adidas Running`: usa OAuth partner se disponibile, altrimenti può importare dall'export locale `GPX + JSON`

## Avvio rapido

1. Copia `.env.example` in `.env`:

```bash
cp .env.example .env
```

2. Crea i secret locali richiesti da Docker. Sostituisci `SCEGLI_...` con valori sicuri; la password PostgreSQL deve essere identica in entrambi i file indicati.

```bash
mkdir -p docker/secrets
printf '%s' 'SCEGLI_PASSWORD_POSTGRES' > docker/secrets/postgres_password
printf '%s' 'postgresql+psycopg://street_skate:SCEGLI_PASSWORD_POSTGRES@db:5432/street_skate' > docker/secrets/database_url
openssl rand -hex 32 > docker/secrets/app_secret_key
printf '%s' 'SCEGLI_PASSWORD_ADMIN' > docker/secrets/admin_password
chmod 600 docker/secrets/{postgres_password,database_url,app_secret_key,admin_password}
```

3. Inserisci le credenziali OAuth che hai in `.env`.
4. Avvia:

```bash
docker compose up --build
```

In alternativa, gli script che avviano Docker quando possibile sono divisi per sistema operativo:

```powershell
# Windows (da PowerShell)
powershell -ExecutionPolicy Bypass -File .\scripts\windows\start-local.ps1
```

```bash
# Linux
./scripts/linux/start-local.sh
```

5. Apri:

```text
http://localhost:5000
```

## Avvio senza Docker

```bash
pip install -r requirements.txt
python run.py
```

Serve un PostgreSQL raggiungibile dal `DATABASE_URL`.

## Mappa frequenze

Dalla dashboard, **Mappa frequenze** apre `/coverage`: mappa con zoom, legenda e percentuali cliccabili per tratto sull'intero archivio.
La frequenza è il numero di pattinate distinte sul tratto diviso il totale delle pattinate, comprese quelle senza GPS utilizzabile. Ripassare sul tratto nella stessa attività conta una sola volta, in entrambi i sensi.
Il confronto usa una tolleranza GPS di 12 m, senza aggancio alla rete stradale; strade parallele molto vicine possono essere accorpate. Salti tra punti oltre 250 m vengono esclusi. Le strade senza colore non hanno passaggi registrati nell'archivio.

Verifica: `python -m unittest discover -s tests -v`.

## Variabili chiave

- `DATABASE_URL`
- `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`
- `MAPMYRUN_CLIENT_ID`, `MAPMYRUN_CLIENT_SECRET`
- `ADIDAS_CLIENT_ID`, `ADIDAS_CLIENT_SECRET`
- `ADIDAS_AUTH_URL`, `ADIDAS_TOKEN_URL`, `ADIDAS_USER_URL`, `ADIDAS_ACTIVITIES_URL`
- `ADIDAS_EXPORT_DIR`, `ADIDAS_EXPORT_USER_DIR`

## Note integrazione

- I token OAuth vengono salvati in Postgres
- L’autorizzazione può cambiare a ogni login: il record `auth_connections` viene aggiornato a ogni nuovo consenso
- I percorsi vengono salvati per utente/provider nella tabella `routes`
- Se `ADIDAS_EXPORT_DIR` esiste, il bottone `adidas Running` usa l'export locale invece di OAuth
- OAuth `adidas Running` resta disponibile solo con credenziali partner e endpoint ufficiali abilitati lato adidas
