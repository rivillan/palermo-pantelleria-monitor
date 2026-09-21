from __future__ import annotations

import argparse
import html
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_API_URL = "https://api.book.dat.dk/RESTv1/travelOptions"
DEFAULT_CITY_PAIR = "PMO-PNL"
ALLOWED_CITY_PAIRS = frozenset({"PMO-PNL", "PNL-PMO", "TPS-PNL", "PNL-TPS"})
DEFAULT_CURRENCY = "EUR"
DEFAULT_PASSENGER_COUNTS = "ADT:1"
DEFAULT_DAYS_BEFORE_DEPARTURE = 1
DEFAULT_DAYS_AFTER_DEPARTURE = 8
DEFAULT_DAYS_BEFORE_RETURN = 2
DEFAULT_DAYS_AFTER_RETURN = 7
DEFAULT_STATE_FILE = ".pmo_pantelleria_state.json"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
DEFAULT_MIN_SEATS = 1
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8080
WEB_ENV_FIELDS = (
    "PMO_PNL_CITY_PAIR",
    "PMO_PNL_DEPARTURE_DATE",
    "PMO_PNL_RETURN_DATE",
    "PMO_PNL_CURRENCY",
    "PMO_PNL_PASSENGER_COUNTS",
    "PMO_PNL_CABIN_CLASS",
    "PMO_PNL_PROMO_CODE",
    "PMO_PNL_COMPANY",
    "PMO_PNL_DAYS_BEFORE_DEPARTURE",
    "PMO_PNL_DAYS_AFTER_DEPARTURE",
    "PMO_PNL_DAYS_BEFORE_RETURN",
    "PMO_PNL_DAYS_AFTER_RETURN",
    "PMO_PNL_HTTP_TIMEOUT_SECONDS",
    "PMO_PNL_MIN_SEATS",
    "PMO_PNL_ROUTE_LABEL",
    "PMO_PNL_STATE_FILE",
    "PMO_PNL_BOOKING_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "SILENT_START",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PmoPantelleriaAvailabilityBot/1.0"


@dataclass(frozen=True)
class Config:
    url: str
    interval_seconds: int
    state_file: Path
    http_timeout_seconds: int
    min_seats: int
    route_label: str
    booking_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    once: bool
    silent_start: bool


def build_monitor_url(
    city_pair: str,
    departure_date: str,
    return_date: str | None,
    currency: str,
    passenger_counts: str,
    cabin_class: str,
    promo_code: str,
    company: str,
    days_before_departure: int,
    days_after_departure: int,
    days_before_return: int,
    days_after_return: int,
) -> str:
    """Costruisce l'URL dell'API travelOptions a partire da parametri generici e configurabili."""
    params = {
        "cityPair": city_pair,
        "departure": departure_date,
        "cabinClass": cabin_class,
        "currency": currency,
        "passengerCounts": passenger_counts,
        "daysBeforeDeparture": str(days_before_departure),
        "daysAfterDeparture": str(days_after_departure),
        "promoCode": promo_code,
        "company": company,
    }
    if return_date:
        params["return"] = return_date
        params["daysBeforeReturn"] = str(days_before_return)
        params["daysAfterReturn"] = str(days_after_return)
    return f"{BASE_API_URL}?{urllib.parse.urlencode(params)}"


@dataclass(frozen=True)
class AvailabilityEvent:
    city_pair: str
    origin: str
    destination: str
    departure_local: str
    arrival_local: str
    flight_number: str | None
    remaining_seats: int


def load_dotenv_file(path: str = ".env") -> None:
    file_path = Path(path)
    if not file_path.exists():
        return

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_or_default(name: str, default: str | None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if not value:
        return default
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitora disponibilita voli Palermo <-> Pantelleria e avvisa quando ci sono posti liberi."
    )
    parser.add_argument(
        "--url",
        default=env_or_default("PMO_PNL_MONITOR_URL", None),
        help="URL API completo da monitorare. Se omesso viene costruito dai parametri --city-pair/--departure-date/ecc.",
    )
    parser.add_argument(
        "--city-pair",
        default=env_or_default("PMO_PNL_CITY_PAIR", DEFAULT_CITY_PAIR),
        help="Tratta da monitorare: PMO-PNL, PNL-PMO, TPS-PNL oppure PNL-TPS.",
    )
    parser.add_argument(
        "--departure-date",
        default=env_or_default("PMO_PNL_DEPARTURE_DATE", None),
        help="Data di partenza (YYYY-MM-DD) da usare come centro della ricerca. Default: oggi.",
    )
    parser.add_argument(
        "--return-date",
        default=env_or_default("PMO_PNL_RETURN_DATE", None),
        help="Data di ritorno opzionale (YYYY-MM-DD); se vuota la ricerca e solo andata.",
    )
    parser.add_argument("--currency", default=env_or_default("PMO_PNL_CURRENCY", DEFAULT_CURRENCY), help="Valuta per le tariffe.")
    parser.add_argument(
        "--passenger-counts",
        default=env_or_default("PMO_PNL_PASSENGER_COUNTS", DEFAULT_PASSENGER_COUNTS),
        help="Conteggio passeggeri, es. ADT:1.",
    )
    parser.add_argument("--cabin-class", default=env_or_default("PMO_PNL_CABIN_CLASS", "") or "", help="Classe di cabina opzionale.")
    parser.add_argument("--promo-code", default=env_or_default("PMO_PNL_PROMO_CODE", "") or "", help="Codice promozionale opzionale.")
    parser.add_argument("--company", default=env_or_default("PMO_PNL_COMPANY", "") or "", help="Filtro compagnia opzionale.")
    parser.add_argument(
        "--days-before-departure",
        type=int,
        default=int(env_or_default("PMO_PNL_DAYS_BEFORE_DEPARTURE", str(DEFAULT_DAYS_BEFORE_DEPARTURE)) or str(DEFAULT_DAYS_BEFORE_DEPARTURE)),
        help="Giorni prima della data di partenza da includere nella ricerca.",
    )
    parser.add_argument(
        "--days-after-departure",
        type=int,
        default=int(env_or_default("PMO_PNL_DAYS_AFTER_DEPARTURE", str(DEFAULT_DAYS_AFTER_DEPARTURE)) or str(DEFAULT_DAYS_AFTER_DEPARTURE)),
        help="Giorni dopo la data di partenza da includere nella ricerca.",
    )
    parser.add_argument(
        "--days-before-return",
        type=int,
        default=int(env_or_default("PMO_PNL_DAYS_BEFORE_RETURN", str(DEFAULT_DAYS_BEFORE_RETURN)) or str(DEFAULT_DAYS_BEFORE_RETURN)),
        help="Giorni prima della data di ritorno da includere nella ricerca.",
    )
    parser.add_argument(
        "--days-after-return",
        type=int,
        default=int(env_or_default("PMO_PNL_DAYS_AFTER_RETURN", str(DEFAULT_DAYS_AFTER_RETURN)) or str(DEFAULT_DAYS_AFTER_RETURN)),
        help="Giorni dopo la data di ritorno da includere nella ricerca.",
    )
    parser.add_argument(
        "--state-file",
        default=env_or_default("PMO_PNL_STATE_FILE", DEFAULT_STATE_FILE),
        help="File locale per evitare notifiche duplicate.",
    )
    parser.add_argument(
        "--http-timeout",
        type=int,
        default=int(env_or_default("PMO_PNL_HTTP_TIMEOUT_SECONDS", str(DEFAULT_HTTP_TIMEOUT_SECONDS)) or str(DEFAULT_HTTP_TIMEOUT_SECONDS)),
        help="Timeout HTTP in secondi.",
    )
    parser.add_argument(
        "--min-seats",
        type=int,
        default=int(env_or_default("PMO_PNL_MIN_SEATS", str(DEFAULT_MIN_SEATS)) or str(DEFAULT_MIN_SEATS)),
        help="Numero minimo di posti per considerare un volo disponibile.",
    )
    parser.add_argument(
        "--route-label",
        default=env_or_default("PMO_PNL_ROUTE_LABEL", "Palermo <-> Pantelleria"),
        help="Etichetta leggibile per le notifiche.",
    )
    parser.add_argument(
        "--booking-url",
        default=env_or_default("PMO_PNL_BOOKING_URL", None),
        help="URL opzionale del sito di prenotazione da includere nel messaggio.",
    )
    parser.add_argument("--telegram-bot-token", default=env_or_default("TELEGRAM_BOT_TOKEN", None), help="Token bot Telegram per le notifiche.")
    parser.add_argument("--telegram-chat-id", default=env_or_default("TELEGRAM_CHAT_ID", None), help="Chat ID Telegram per le notifiche.")
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Compatibilita: il bot esegue sempre un solo controllo e termina.",
    )
    parser.add_argument(
        "--silent-start",
        action="store_true",
        default=parse_bool(env_or_default("SILENT_START", None), False),
        help="Non notifica se risultano gia posti disponibili al primo controllo.",
    )
    parser.add_argument("--web", action="store_true", help="Avvia l'interfaccia web locale per testare il bot.")
    parser.add_argument("--web-host", default=env_or_default("PMO_PNL_WEB_HOST", DEFAULT_WEB_HOST), help="Indirizzo dell'interfaccia web.")
    parser.add_argument("--web-port", type=int, default=int(env_or_default("PMO_PNL_WEB_PORT", str(DEFAULT_WEB_PORT)) or str(DEFAULT_WEB_PORT)), help="Porta dell'interfaccia web.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    city_pair = args.city_pair.strip().upper()
    if city_pair not in ALLOWED_CITY_PAIRS:
        allowed = ", ".join(sorted(ALLOWED_CITY_PAIRS))
        raise ValueError(f"Tratta non supportata: {city_pair}. Scegli una tra: {allowed}.")

    url = args.url
    if not url:
        departure_date = args.departure_date or datetime.now().strftime("%Y-%m-%d")
        url = build_monitor_url(
            city_pair=city_pair,
            departure_date=departure_date,
            return_date=args.return_date,
            currency=args.currency,
            passenger_counts=args.passenger_counts,
            cabin_class=args.cabin_class,
            promo_code=args.promo_code,
            company=args.company,
            days_before_departure=args.days_before_departure,
            days_after_departure=args.days_after_departure,
            days_before_return=args.days_before_return,
            days_after_return=args.days_after_return,
        )
    return Config(
        url=url,
        interval_seconds=DEFAULT_INTERVAL_SECONDS,
        state_file=Path(args.state_file),
        http_timeout_seconds=max(5, args.http_timeout),
        min_seats=max(1, args.min_seats),
        route_label=args.route_label,
        booking_url=args.booking_url or None,
        telegram_bot_token=args.telegram_bot_token or None,
        telegram_chat_id=args.telegram_chat_id or None,
        once=bool(args.once),
        silent_start=bool(args.silent_start),
    )


def fetch_json(url: str, timeout_seconds: int) -> Any:
    if not url or not url.strip():
        raise ValueError("PMO_PNL_MONITOR_URL non e impostato.")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def normalize_options(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    raise ValueError("Formato JSON inatteso: atteso lista di travelOptions.")


def extract_events(payload: Any, min_seats: int) -> list[AvailabilityEvent]:
    events: list[AvailabilityEvent] = []
    for option in normalize_options(payload):
        city_pair = option.get("cityPair", {})
        city_pair_id = str(city_pair.get("identifier") or "")

        for flight in option.get("flights", []):
            if not isinstance(flight, dict):
                continue
            remaining_seats = flight.get("availability")
            if not isinstance(remaining_seats, int):
                continue
            if remaining_seats < min_seats:
                continue

            departure = flight.get("departure") if isinstance(flight.get("departure"), dict) else {}
            arrival = flight.get("arrival") if isinstance(flight.get("arrival"), dict) else {}
            departure_airport = departure.get("airport", {}) if isinstance(departure.get("airport"), dict) else {}
            arrival_airport = arrival.get("airport", {}) if isinstance(arrival.get("airport"), dict) else {}

            events.append(
                AvailabilityEvent(
                    city_pair=city_pair_id,
                    origin=str(departure_airport.get("code") or ""),
                    destination=str(arrival_airport.get("code") or ""),
                    departure_local=str(departure.get("localScheduledTime") or ""),
                    arrival_local=str(arrival.get("localScheduledTime") or ""),
                    flight_number=str(flight.get("flightNumber")) if flight.get("flightNumber") else None,
                    remaining_seats=remaining_seats,
                )
            )

    events.sort(key=lambda event: event.departure_local)
    return events


def build_signature(events: list[AvailabilityEvent]) -> str:
    material = "|".join(
        f"{event.city_pair}:{event.departure_local}:{event.remaining_seats}" for event in events
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def save_web_env(values: dict[str, str], path: Path = Path(".env")) -> None:
    lines = [
        "# Configurazione generata dalla UI locale. Non committare questo file.",
        "",
    ]
    for key in WEB_ENV_FIELDS:
        lines.append(f"{key}={values.get(key, '')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_message(config: Config, events: list[AvailabilityEvent]) -> str:
    lines = [
        f"Disponibile: {config.route_label}",
        "",
    ]
    for event in events:
        flight_suffix = f" (volo {event.flight_number})" if event.flight_number else ""
        lines.append(
            f"- {event.city_pair} | {event.departure_local} -> {event.arrival_local} | posti: {event.remaining_seats}{flight_suffix}"
        )
    if config.booking_url:
        lines.append("")
        lines.append(f"Controlla subito il booking: {config.booking_url}")
    return "\n".join(lines)


def send_telegram_message(config: Config, message: str) -> None:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return

    endpoint = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": config.telegram_chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=config.http_timeout_seconds) as response:
        response.read()


def poll_once(config: Config) -> tuple[bool, list[AvailabilityEvent]]:
    payload = fetch_json(config.url, config.http_timeout_seconds)
    events = extract_events(payload, config.min_seats)
    signature = build_signature(events)

    state = load_state(config.state_file)
    previous_signature = state.get("last_signature")
    previous_available = bool(state.get("last_available", False))

    available = len(events) > 0
    should_notify = available and (not previous_available or signature != previous_signature)
    if config.silent_start and not previous_signature and available:
        should_notify = False

    if should_notify:
        message = format_message(config, events)
        print(message)
        send_telegram_message(config, message)

    state.update(
        {
            "last_checked_at": datetime.now().isoformat(timespec="seconds"),
            "last_available": available,
            "last_signature": signature,
            "last_event_count": len(events),
        }
    )
    save_state(config.state_file, state)
    return available, events


def run_loop(config: Config) -> int:
    try:
        available, events = poll_once(config)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if available:
            print(f"[{timestamp}] Disponibile: {len(events)} voli con posti trovati.")
        else:
            print(f"[{timestamp}] Nessun posto disponibile.")
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"Errore rete: {error.reason}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Errore inatteso: {error}", file=sys.stderr)
        return 1

    return 0


def render_web_page(values: dict[str, str], result: str = "", error: str = "") -> str:
    def value(name: str, default: str = "") -> str:
        return html.escape(values.get(name, default), quote=True)

    result_block = f'<div class="result">{result}</div>' if result else ""
    error_block = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight availability monitor</title>
<style>
body {{ font-family: system-ui, sans-serif; background: #f4f6f8; color: #17202a; margin: 0; }}
main {{ max-width: 860px; margin: 32px auto; padding: 0 18px; }}
.card {{ background: white; border-radius: 12px; padding: 24px; box-shadow: 0 3px 18px #0001; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }}
label {{ display: flex; flex-direction: column; gap: 6px; font-weight: 600; }}
input, select {{ border: 1px solid #c8d0d8; border-radius: 7px; padding: 10px; font: inherit; }}
button {{ margin-top: 18px; background: #1769e0; color: white; border: 0; border-radius: 7px; padding: 11px 18px; font-weight: 700; cursor: pointer; }}
.hint {{ color: #59636e; font-size: .92rem; }}
.section {{ border-top: 1px solid #e2e6ea; margin-top: 22px; padding-top: 16px; }}
.result {{ margin-top: 20px; background: #eef8ef; border-left: 4px solid #2e9d4d; padding: 15px; white-space: pre-wrap; }}
.error {{ margin-top: 20px; background: #fff0f0; border-left: 4px solid #d33; padding: 15px; }}
code {{ background: #eef1f4; padding: 2px 5px; border-radius: 4px; }}
</style>
</head>
<body><main><div class="card">
<h1>Flight availability monitor</h1>
<p class="hint">Modifica la configurazione e salvala in <code>.env</code>. Il processo server può poi eseguire <code>python main.py</code> senza la UI.</p>
<form method="post" action="/check">
<div class="grid">
<label>Tratta
<select name="city_pair">
<option value="PMO-PNL" {"selected" if values.get("city_pair", "PMO-PNL") == "PMO-PNL" else ""}>Palermo → Pantelleria</option>
<option value="PNL-PMO" {"selected" if values.get("city_pair") == "PNL-PMO" else ""}>Pantelleria → Palermo</option>
<option value="TPS-PNL" {"selected" if values.get("city_pair") == "TPS-PNL" else ""}>Trapani → Pantelleria</option>
<option value="PNL-TPS" {"selected" if values.get("city_pair") == "PNL-TPS" else ""}>Pantelleria → Trapani</option>
</select></label>
<label>Data partenza
<input name="departure_date" type="date" value="{value("departure_date")}"></label>
<label>Data ritorno (opzionale)
<input name="return_date" type="date" value="{value("return_date")}"></label>
<label>Posti minimi
<input name="min_seats" type="number" min="1" value="{value("min_seats", "1")}"></label>
<label>Passeggeri
<input name="passenger_counts" value="{value("passenger_counts", "ADT:1")}"></label>
</div>
<div class="section"><h2>Parametri API</h2><div class="grid">
<label>Valuta<input name="currency" value="{value("currency", "EUR")}"></label>
<label>Classe cabina<input name="cabin_class" value="{value("cabin_class")}"></label>
<label>Codice promo<input name="promo_code" value="{value("promo_code")}"></label>
<label>Compagnia<input name="company" value="{value("company")}"></label>
<label>Giorni prima partenza<input name="days_before_departure" type="number" min="0" value="{value("days_before_departure", "1")}"></label>
<label>Giorni dopo partenza<input name="days_after_departure" type="number" min="0" value="{value("days_after_departure", "8")}"></label>
<label>Giorni prima ritorno<input name="days_before_return" type="number" min="0" value="{value("days_before_return", "2")}"></label>
<label>Giorni dopo ritorno<input name="days_after_return" type="number" min="0" value="{value("days_after_return", "7")}"></label>
</div></div>
<div class="section"><h2>Notifiche e stato</h2><div class="grid">
<label>Telegram bot token<input name="telegram_bot_token" type="password" value="{value("telegram_bot_token")}"></label>
<label>Telegram chat ID<input name="telegram_chat_id" value="{value("telegram_chat_id")}"></label>
<label>Etichetta<input name="route_label" value="{value("route_label", "Palermo <-> Pantelleria")}"></label>
<label>File stato<input name="state_file" value="{value("state_file", ".pmo_pantelleria_state.json")}"></label>
<label>URL booking<input name="booking_url" value="{value("booking_url")}"></label>
<label>Timeout HTTP<input name="http_timeout" type="number" min="5" value="{value("http_timeout", "30")}"></label>
</div></div>
<button type="submit">Controlla disponibilità</button>
<button type="submit" formaction="/save">Salva configurazione in .env</button>
</form>
{result_block}{error_block}
<p class="hint">Avvio UI: <code>python main.py --web</code> · Server: <code>python main.py</code> · URL: <code>http://127.0.0.1:8080</code></p>
</div></main></body></html>"""


def run_web_server(host: str, port: int, base_args: argparse.Namespace) -> int:
    defaults = {
        "city_pair": base_args.city_pair,
        "departure_date": base_args.departure_date or datetime.now().strftime("%Y-%m-%d"),
        "return_date": base_args.return_date or "",
        "min_seats": str(base_args.min_seats),
        "passenger_counts": base_args.passenger_counts,
        "min_seats": str(base_args.min_seats),
        "currency": base_args.currency,
        "cabin_class": base_args.cabin_class,
        "promo_code": base_args.promo_code,
        "company": base_args.company,
        "days_before_departure": str(base_args.days_before_departure),
        "days_after_departure": str(base_args.days_after_departure),
        "days_before_return": str(base_args.days_before_return),
        "days_after_return": str(base_args.days_after_return),
        "http_timeout": str(base_args.http_timeout),
        "route_label": base_args.route_label,
        "state_file": base_args.state_file,
        "booking_url": base_args.booking_url or "",
        "telegram_bot_token": base_args.telegram_bot_token or "",
        "telegram_chat_id": base_args.telegram_chat_id or "",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_page(self, page: str) -> None:
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.send_page(render_web_page(defaults))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            fields = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            values = {key: items[0] for key, items in fields.items()}
            try:
                if self.path == "/save":
                    env_values = {
                        "PMO_PNL_CITY_PAIR": values.get("city_pair", defaults["city_pair"]),
                        "PMO_PNL_DEPARTURE_DATE": values.get("departure_date", ""),
                        "PMO_PNL_RETURN_DATE": values.get("return_date", ""),
                        "PMO_PNL_CURRENCY": values.get("currency", "EUR"),
                        "PMO_PNL_PASSENGER_COUNTS": values.get("passenger_counts", DEFAULT_PASSENGER_COUNTS),
                        "PMO_PNL_CABIN_CLASS": values.get("cabin_class", ""),
                        "PMO_PNL_PROMO_CODE": values.get("promo_code", ""),
                        "PMO_PNL_COMPANY": values.get("company", ""),
                        "PMO_PNL_DAYS_BEFORE_DEPARTURE": values.get("days_before_departure", "1"),
                        "PMO_PNL_DAYS_AFTER_DEPARTURE": values.get("days_after_departure", "8"),
                        "PMO_PNL_DAYS_BEFORE_RETURN": values.get("days_before_return", "2"),
                        "PMO_PNL_DAYS_AFTER_RETURN": values.get("days_after_return", "7"),
                        "PMO_PNL_HTTP_TIMEOUT_SECONDS": values.get("http_timeout", "30"),
                        "PMO_PNL_MIN_SEATS": values.get("min_seats", "1"),
                        "PMO_PNL_ROUTE_LABEL": values.get("route_label", ""),
                        "PMO_PNL_STATE_FILE": values.get("state_file", DEFAULT_STATE_FILE),
                        "PMO_PNL_BOOKING_URL": values.get("booking_url", ""),
                        "TELEGRAM_BOT_TOKEN": values.get("telegram_bot_token", ""),
                        "TELEGRAM_CHAT_ID": values.get("telegram_chat_id", ""),
                        "SILENT_START": "false",
                    }
                    save_web_env(env_values)
                    self.send_page(render_web_page({**values, **{
                        "min_seats": env_values["PMO_PNL_MIN_SEATS"],
                        "http_timeout": env_values["PMO_PNL_HTTP_TIMEOUT_SECONDS"],
                    }}, result="Configurazione salvata in .env. Puoi ora avviare il bot con: python main.py"))
                    return
                args = argparse.Namespace(**vars(base_args))
                args.city_pair = values.get("city_pair", defaults["city_pair"])
                args.departure_date = values.get("departure_date", "")
                args.return_date = values.get("return_date", "")
                args.min_seats = int(values.get("min_seats", "1"))
                args.passenger_counts = values.get("passenger_counts", DEFAULT_PASSENGER_COUNTS)
                args.currency = values.get("currency", DEFAULT_CURRENCY)
                args.cabin_class = values.get("cabin_class", "")
                args.promo_code = values.get("promo_code", "")
                args.company = values.get("company", "")
                args.days_before_departure = int(values.get("days_before_departure", "1"))
                args.days_after_departure = int(values.get("days_after_departure", "8"))
                args.days_before_return = int(values.get("days_before_return", "2"))
                args.days_after_return = int(values.get("days_after_return", "7"))
                args.http_timeout = int(values.get("http_timeout", "30"))
                args.route_label = values.get("route_label", defaults.get("route_label", ""))
                args.state_file = values.get("state_file", DEFAULT_STATE_FILE)
                args.booking_url = values.get("booking_url", "")
                args.telegram_bot_token = values.get("telegram_bot_token", "")
                args.telegram_chat_id = values.get("telegram_chat_id", "")
                args.url = None
                config = build_config(args)
                available, events = poll_once(config)
                if available:
                    result = html.escape(format_message(config, events))
                else:
                    result = "Nessun volo con posti disponibili per i parametri selezionati."
                self.send_page(render_web_page(values, result=result))
            except Exception as error:
                self.send_page(render_web_page(values, error=str(error)))

    try:
        server = ThreadingHTTPServer((host, port), Handler)
        print(f"Interfaccia web disponibile su http://{host}:{port}")
        server.serve_forever()
    except OSError as error:
        print(f"Impossibile avviare l'interfaccia web: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if "server" in locals():
            server.server_close()
    return 0


def main() -> int:
    load_dotenv_file()
    args = parse_args()
    if args.web:
        return run_web_server(args.web_host, args.web_port, args)
    config = build_config(args)
    return run_loop(config)


if __name__ == "__main__":
    raise SystemExit(main())
