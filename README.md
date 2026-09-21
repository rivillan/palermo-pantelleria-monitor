# Libertylines availability bot

Bot semplice in Python per controllare la tratta Trapani -> Pantelleria e avvisarti quando il numero di posti torna sopra la soglia configurata.

Il repository include anche un secondo bot, `pmo_pantelleria_monitor.py`, che monitora i voli Palermo <-> Pantelleria tramite l'API di continuita territoriale.

## Cosa fa

- Interroga l'API Libertylines a intervalli regolari.
- Legge la disponibilita dai risultati restituiti dall'endpoint.
- Salva uno stato locale per evitare notifiche duplicate.
- Può inviare un messaggio Telegram opzionale.

## Avvio rapido

1. Copia `.env.example` in `.env`.
2. Imposta `MONITOR_URL` con l'endpoint che vuoi monitorare.
3. Avvia il bot:

```powershell
python main.py
```

## Configurazione

Variabili supportate:

- `MONITOR_URL`: URL dell'API da controllare.
- `CHECK_INTERVAL_SECONDS`: intervallo tra i controlli, default `300`.
- `HTTP_TIMEOUT_SECONDS`: timeout delle richieste HTTP, default `30`.
- `MIN_SEATS`: soglia minima per considerare la tratta disponibile, default `1`.
- `STATE_FILE`: file locale usato per evitare notifiche ripetute.
- `ROUTE_LABEL`: testo leggibile usato nelle notifiche.
- `TELEGRAM_BOT_TOKEN`: token Telegram opzionale.
- `TELEGRAM_CHAT_ID`: chat ID Telegram opzionale.
- `RUN_ONCE`: se impostato a `true`, esegue un solo controllo.
- `SILENT_START`: se `true`, evita la notifica al primo avvio quando la tratta risulta gia disponibile.

## Esempio

```powershell
$env:MONITOR_URL = "https://api.libertylines.it/search/s/Trapani/Pantelleria/2026-08-11/2026-08-16?channel=1&reservationId=&inStaging=false&isReturn=false&excludeExternals=false&partial=false&caller=&changeDate=false&locale=it&coupon="
$env:CHECK_INTERVAL_SECONDS = "300"
$env:TELEGRAM_BOT_TOKEN = "123456:ABCDEF"
$env:TELEGRAM_CHAT_ID = "123456789"
python main.py
```

## GitHub Actions

Puoi far girare il bot su GitHub Actions con un workflow schedulato che esegue un controllo ogni pochi minuti.

Configura questi segreti o variabili nel repository:

- `MONITOR_URL`
- `TELEGRAM_BOT_TOKEN` se vuoi le notifiche Telegram
- `TELEGRAM_CHAT_ID` se vuoi le notifiche Telegram

Il workflow usa la cache di GitHub Actions per mantenere il file di stato tra le esecuzioni, quindi non servono push automatici nel repository.

## Nota

L'endpoint della prenotazione puo cambiare comportamento o formato nel tempo. Se Libertylines modifica la risposta API, va aggiornato il parser in `main.py`.

## Bot voli Palermo <-> Pantelleria

`pmo_pantelleria_monitor.py` interroga una sola volta l'API `travelOptions` (continuita territoriale) per una delle quattro tratte configurabili: `PMO-PNL` (Palermo -> Pantelleria), `PNL-PMO` (Pantelleria -> Palermo), `TPS-PNL` (Trapani -> Pantelleria) o `PNL-TPS` (Pantelleria -> Trapani). Avvisa quando un volo ha posti disponibili (`flights[].availability >= PMO_PNL_MIN_SEATS`) e poi termina.

```powershell
python pmo_pantelleria_monitor.py
```

Per testarlo con una grafica locale:

```powershell
python pmo_pantelleria_monitor.py --web
```

Apri `http://127.0.0.1:8080`, scegli soltanto tratta e date nel form e premi **Controlla disponibilità** oppure **Salva configurazione in .env**. La GUI esegue una verifica singola per ogni invio; arresta il server con `Ctrl+C`. I parametri API, le notifiche e il file di stato restano configurabili solo tramite `.env`. L'indirizzo e la porta sono configurabili con `PMO_PNL_WEB_HOST` e `PMO_PNL_WEB_PORT`.

L'URL dell'API viene costruito automaticamente da parametri generici (nessuna data o dato personale hardcoded): `PMO_PNL_CITY_PAIR`, `PMO_PNL_DEPARTURE_DATE` (default: oggi), `PMO_PNL_RETURN_DATE`, `PMO_PNL_CURRENCY`, `PMO_PNL_PASSENGER_COUNTS`, `PMO_PNL_CABIN_CLASS`, `PMO_PNL_PROMO_CODE`, `PMO_PNL_COMPANY` e la finestra di ricerca `PMO_PNL_DAYS_*`. Se `PMO_PNL_RETURN_DATE` è vuota, la richiesta è solo andata e non contiene parametri di ritorno. In alternativa, `PMO_PNL_MONITOR_URL` permette di impostare un URL completo che ha priorità su tutto il resto. Altre variabili: `PMO_PNL_HTTP_TIMEOUT_SECONDS`, `PMO_PNL_MIN_SEATS`, `PMO_PNL_ROUTE_LABEL`, `PMO_PNL_STATE_FILE`, `PMO_PNL_BOOKING_URL`. Il bot esegue sempre una singola verifica; `RUN_ONCE` è mantenuta solo per compatibilità.

Anche se l'API restituisce una finestra di date, il bot filtra la risposta e mostra solo la data di partenza selezionata. Se è impostata una data di ritorno, include anche soltanto il rientro in quella data.
