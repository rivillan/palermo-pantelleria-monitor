from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_API_URL = "https://api.book.dat.dk/RESTv1/travelOptions"
DEFAULT_CITY_PAIR = "PMO-PNL"
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
    parser.add_argument("--city-pair", default=env_or_default("PMO_PNL_CITY_PAIR", DEFAULT_CITY_PAIR), help="Coppia di citta IATA, es. PMO-PNL.")
    parser.add_argument(
        "--departure-date",
        default=env_or_default("PMO_PNL_DEPARTURE_DATE", None),
        help="Data di partenza (YYYY-MM-DD) da usare come centro della ricerca. Default: oggi.",
    )
    parser.add_argument(
        "--return-date",
        default=env_or_default("PMO_PNL_RETURN_DATE", None),
        help="Data di ritorno opzionale (YYYY-MM-DD) per includere anche la tratta di rientro.",
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
        "--interval",
        type=int,
        default=int(env_or_default("PMO_PNL_CHECK_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)) or str(DEFAULT_INTERVAL_SECONDS)),
        help="Intervallo tra i controlli in secondi.",
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
    parser.add_argument("--once", action="store_true", default=parse_bool(env_or_default("RUN_ONCE", None), False), help="Esegue un solo controllo e termina.")
    parser.add_argument(
        "--silent-start",
        action="store_true",
        default=parse_bool(env_or_default("SILENT_START", None), False),
        help="Non notifica se risultano gia posti disponibili al primo controllo.",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    url = args.url
    if not url:
        departure_date = args.departure_date or datetime.now().strftime("%Y-%m-%d")
        url = build_monitor_url(
            city_pair=args.city_pair,
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
        interval_seconds=max(10, args.interval),
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
    stop_requested = False

    def handle_stop(signum: int, frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        print("\nArresto richiesto, chiusura in corso...")

    signal.signal(signal.SIGINT, handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_stop)

    while not stop_requested:
        try:
            available, events = poll_once(config)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if available:
                print(f"[{timestamp}] Disponibile: {len(events)} voli con posti trovati.")
            else:
                print(f"[{timestamp}] Nessun posto disponibile.")
        except urllib.error.HTTPError as error:
            print(f"HTTP {error.code}: {error.reason}", file=sys.stderr)
        except urllib.error.URLError as error:
            print(f"Errore rete: {error.reason}", file=sys.stderr)
        except Exception as error:
            print(f"Errore inatteso: {error}", file=sys.stderr)

        if config.once or stop_requested:
            break
        time.sleep(config.interval_seconds)

    return 0


def main() -> int:
    load_dotenv_file()
    args = parse_args()
    config = build_config(args)
    return run_loop(config)


if __name__ == "__main__":
    raise SystemExit(main())
