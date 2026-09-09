# Palermo <-> Pantelleria flight availability bot

Bot in Python (solo standard library) che controlla i voli di continuita territoriale sulla tratta Palermo (PMO) <-> Pantelleria (PNL) tramite l'API `travelOptions` e avvisa via Telegram quando un volo ha posti liberi.

## Cosa fa

- Interroga l'API a intervalli regolari (entrambe le direzioni, PMO-PNL e PNL-PMO, in un'unica richiesta).
- Considera disponibile un volo quando `flights[].availability >= PMO_PNL_MIN_SEATS`.
- Salva uno stato locale (`.pmo_pantelleria_state.json`) per evitare notifiche duplicate.
- Puo inviare un messaggio Telegram opzionale.

## Avvio rapido

1. Copia `.env.example` in `.env`.
2. Imposta `PMO_PNL_MONITOR_URL` con l'endpoint da monitorare (date, cityPair, passeggeri, ecc.).
3. Avvia il bot:

```powershell
python main.py
```

## Configurazione

Variabili supportate (vedi `.env.example`):

- `PMO_PNL_MONITOR_URL`: URL dell'API `travelOptions` da controllare.
- `PMO_PNL_CHECK_INTERVAL_SECONDS`: intervallo tra i controlli, default `300`.
- `PMO_PNL_HTTP_TIMEOUT_SECONDS`: timeout delle richieste HTTP, default `30`.
- `PMO_PNL_MIN_SEATS`: soglia minima di posti per considerare un volo disponibile, default `1`.
- `PMO_PNL_ROUTE_LABEL`: testo leggibile usato nelle notifiche.
- `PMO_PNL_STATE_FILE`: file locale usato per evitare notifiche ripetute.
- `PMO_PNL_BOOKING_URL`: URL opzionale del sito di prenotazione da includere nel messaggio.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`: notifiche Telegram opzionali.
- `RUN_ONCE`: se `true`, esegue un solo controllo.
- `SILENT_START`: se `true`, evita la notifica al primo avvio quando ci sono gia posti disponibili.

## Esempio

```powershell
$env:PMO_PNL_MONITOR_URL = "https://api.book.dat.dk/RESTv1/travelOptions?cityPair=PMO-PNL&departure=2026-09-10&cabinClass=&currency=EUR&passengerCounts=ADT%3A1&daysBeforeDeparture=1&daysAfterDeparture=8&promoCode=&company=&return=2026-09-13&daysBeforeReturn=2&daysAfterReturn=7"
$env:TELEGRAM_BOT_TOKEN = "123456:ABCDEF"
$env:TELEGRAM_CHAT_ID = "123456789"
python main.py
```

## GitHub Actions

Il workflow `.github/workflows/pmo-pantelleria-monitor.yml` esegue un controllo ogni 5 minuti usando la cache di GitHub Actions per mantenere lo stato tra le esecuzioni.

Configura questi segreti nel repository:

- `PMO_PNL_MONITOR_URL`
- `TELEGRAM_BOT_TOKEN` se vuoi le notifiche Telegram
- `TELEGRAM_CHAT_ID` se vuoi le notifiche Telegram

## Nota

L'endpoint puo cambiare formato o comportamento nel tempo. Se l'API cambia, va aggiornato il parser in `main.py`.
