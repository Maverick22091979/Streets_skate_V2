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
I tracciati già nel DB vengono caricati progressivamente, senza attendere il calcolo: in anteprima sono grigi. Le frequenze vengono preparate in background all'avvio e dopo import/cancellazioni, salvate nella tabella `coverage_snapshots` e riutilizzate finché l'archivio non cambia. La tabella viene creata automaticamente all'avvio. Nessuna chiamata ai provider è necessaria.
La frequenza è il numero di pattinate distinte sul tratto diviso il totale delle pattinate, comprese quelle senza GPS utilizzabile. Ripassare sul tratto nella stessa attività conta una sola volta, in entrambi i sensi.
Il confronto usa una tolleranza GPS di 12 m, senza aggancio alla rete stradale; strade parallele molto vicine possono essere accorpate. Salti tra punti oltre 250 m vengono esclusi. Le strade senza colore non hanno passaggi registrati nell'archivio.

Verifica: `python -m unittest discover -s tests -v` e `node --test tests/coverage.test.cjs tests/session_guard.test.cjs`.

## Ricarica dashboard

La dashboard legge dal DB solo i metadati delle schede, escludendo punti GPS, indici di velocità e campioni delle superfici dalla query. La ricarica usa `/api/routes?after_id=...&limit=25` e segue tutte le pagine; i dettagli completi restano disponibili nella pagina del percorso. Gli errori HTML del proxy vengono mostrati con il codice HTTP e i percorsi già visualizzati vengono mantenuti se la ricarica fallisce. Il conteggio delle schede riflette i filtri attivi.

## Import Strava

L'import della dashboard scorre automaticamente tutte le pagine delle attività fino alla prima pagina vuota, anche se una pagina intermedia contiene meno elementi del previsto. Per ogni attività acquisisce gli stream GPS `latlng`, `altitude`, `time`, `distance` e `velocity_smooth` quando disponibili. I punti dettagliati vengono usati dal profilo del percorso e dalla coverage; la polyline semplificata rimane disponibile come fallback.

Ripetere l'import aggiorna nello stesso record le attività precedentemente salvate con la sola traccia semplificata. Le attività con GPS dettagliato già acquisito vengono riutilizzate senza riscaricare gli stream. Se il recupero del singolo stream fallisce, l'import continua con la geometria semplificata e restituisce un avviso; un import successivo può ritentarlo. Le attività manuali non richiedono stream.

L'elaborazione della dashboard avviene in un worker separato, salva ogni attività prima di passare alla successiva e rilascia la connessione DB durante l'acquisizione GPS e l'arricchimento esterno. I timeout temporanei del monitoraggio non reinviano l'import e il polling rinnova la sessione. Al raggiungimento di un limite API temporaneo viene atteso il nuovo intervallo (fino a tre tentativi); il limite giornaliero interrompe il job con un messaggio esplicito, mantenendo quanto già salvato. Resta necessario rispettare scope, visibilità e limiti assegnati all'app Strava.

Documentazione: [attività e stream](https://developers.strava.com/docs/reference/). Verifica locale: `python -m unittest discover -s tests -p test_strava_import.py -v`.

## Import GPX in produzione

La dashboard invia blocchi sequenziali di massimo 5 percorsi e 4 MB complessivi, mantenendo insieme GPX e JSON omonimo. Un singolo percorso più grande viene inviato da solo, fino al limite applicativo di 20 MB per richiesta. `/api/manual-import/gpx/start` risponde con HTTP 202 e l'avanzamento per file viene letto da `/api/manual-import/jobs/{id}`. Gli errori temporanei di rete o proxy durante la lettura dello stato vengono ritentati senza ripetere l'upload. Anche dopo timeout ripetuti del browser (30 secondi per richiesta), il monitoraggio dello stesso job continua con pause fino a 15 secondi e avviso di riconnessione. Sessione scaduta, job non trovato ed errori di elaborazione interrompono l'import con un messaggio esplicito.

Parsing, arricchimento e salvataggio avvengono in un worker dedicato, con un solo import attivo e massimo 8 blocchi in coda/elaborazione. La sessione DB rilascia la connessione prima del parsing e delle chiamate OSM/meteo, riaprendola per le letture e i salvataggi necessari. Ogni percorso viene salvato con un commit separato: un errore successivo non annulla quelli già importati. La cache coverage viene aggiornata a fine blocco, anche in caso di errore dopo salvataggi parziali.

Il reverse proxy deve accettare almeno 25 MB (`client_max_body_size 25m` su nginx, come nell'esempio in `docker/nginx/default.conf`). Il nginx installato sull'host va configurato separatamente.

I job manuali sono riservati alla sessione admin che li ha avviati e restano in memoria nel processo web. L'inattività non fa scadere una sessione admin con un proprio job ancora attivo; questa protezione non si applica ad altri utenti o a job già terminati. Una sessione assente/scaduta restituisce HTTP 401 con richiesta di nuovo login; un utente autenticato senza ruolo admin riceve HTTP 403. Un riavvio interrompe i job in corso: verificare i percorsi salvati prima di ripetere il caricamento. I filename già importati vengono scartati. Applicare insieme frontend e backend aggiornati; in produzione ricostruire il servizio web, poi ricaricare il browser con Ctrl+F5.

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
