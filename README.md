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

1. Copia `.env.example` in `.env`
2. Inserisci le credenziali OAuth che hai
3. Avvia:

```bash
docker compose up --build
```

4. Apri:

```text
http://localhost:5000
```

## Avvio senza Docker

```bash
pip install -r requirements.txt
python run.py
```

Serve un PostgreSQL raggiungibile dal `DATABASE_URL`.

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
