"""Experimental Google Flights adapter.

Uses fast-flights (MIT) for queries. The upstream parser currently crashes on
unpriced entries and only returns outbound listings. This adapter skips those
entries and selects an outbound before examining its actual return options.
Field positions can change: missing or inconsistent data raises an error.
"""
import base64
import copy
import json
import math
import time
from datetime import datetime

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.pb.flights_pb2 import ItinerarySummary
from primp import Client
from selectolax.lexbor import LexborHTMLParser


class ScraperError(Exception):
    pass


def _field(number, value):
    """Encode a length-delimited protobuf field (selected flight segments)."""
    data = value.encode() if isinstance(value, str) else value
    def varint(n):
        result = bytearray()
        while n >= 128:
            result.append((n & 127) | 128)
            n >>= 7
        result.append(n)
        return bytes(result)
    return varint((number << 3) | 2) + varint(len(data)) + data


def _stamp(date, clock):
    if not isinstance(date, list) or len(date) != 3 or not isinstance(clock, list):
        raise ScraperError('Missing flight dates or times.')
    hour, minute = (clock + [None, None])[:2]
    return datetime(*date, hour or 0, minute or 0).strftime('%Y-%m-%d %H:%M')


def _payload(html):
    parser = LexborHTMLParser(html)
    script = parser.css_first(r'script.ds\:1')
    if not script:
        raise ScraperError('Google did not return flight data (possibly a consent or blocked page).')
    try:
        payload = json.loads(script.text().split('data:', 1)[1].rsplit(',', 1)[0])
        if not isinstance(payload, list) or len(payload) < 8:
            size = len(payload) if isinstance(payload, list) else 'non-list'
            raise ScraperError(f'Google returned incomplete flight data (fields: {size}).')
        return payload
    except json.JSONDecodeError as exc:
        raise ScraperError(f'Google flight response JSON could not be read (offset {exc.pos}).') from exc
    except (ValueError, IndexError, AttributeError) as exc:
        raise ScraperError('Google flight response format changed.') from exc


def parse_options(html, currency):
    payload = _payload(html)
    options = []
    seen = set()
    for group in (2, 3):
        block = payload[group]
        if block is None:
            continue
        if not isinstance(block, list) or not block:
            raise ScraperError('Unexpected flight-list structure.')
        entries = block[0]
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ScraperError('Unexpected itinerary structure.')
        for entry in entries:
            try:
                # Some listings have no price. They must not become zero-price deals.
                price_data = entry[1][0]
                if not isinstance(price_data, list) or len(price_data) < 2 or price_data[1] is None:
                    continue
                price = price_data[1]
                if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                    continue
                summary = ItinerarySummary()
                summary.ParseFromString(base64.b64decode(entry[1][1]))
                if summary.price.currency != currency:
                    raise ScraperError('Flight currency differs from your selected currency.')
                if abs(summary.price.price / 100 - price) > 1:
                    raise ScraperError('Flight price fields disagree; refusing an uncertain quote.')
                raw = entry[0]
                segments = raw[2]
                flights = []
                for s in segments:
                    carrier, number = s[22][0:2]
                    if not carrier or not number:
                        raise ScraperError('Flight identification is missing.')
                    flights.append({
                        'departure_airport': {'id': s[3], 'time': _stamp(s[20], s[8])},
                        'arrival_airport': {'id': s[6], 'time': _stamp(s[21], s[10])},
                        'flight_number': carrier + str(number),
                        'airline': s[22][3] if len(s[22]) > 3 else carrier,
                    })
                layovers = []
                for l in raw[13] or []:
                    if l[1] != l[2]:
                        raise ScraperError('Airport change detected.')
                    layovers.append({'id': l[1], 'duration': l[0]})
                option = {'price': price, 'flights': flights, 'layovers': layovers}
                identity = json.dumps(flights, sort_keys=True)
                if identity not in seen:
                    seen.add(identity)
                    options.append(option)
            except ScraperError:
                raise
            except (IndexError, TypeError, ValueError, AttributeError) as exc:
                raise ScraperError('Google itinerary format changed; no uncertain data will be reported.') from exc
    return sorted(options, key=lambda item: item['price'])


def acceptable(leg, config, origin, destination, date):
    flights = leg.get('flights', [])
    if not flights or len(flights) - 1 > config['maxStops']:
        return False
    if flights[0]['departure_airport']['id'] != origin or flights[-1]['arrival_airport']['id'] != destination:
        return False
    if not flights[0]['departure_airport']['time'].startswith(date):
        return False
    layovers = leg.get('layovers', [])
    if len(layovers) != len(flights) - 1:
        return False
    for i, layover in enumerate(layovers):
        minutes = layover['duration']
        if not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes < 0 or minutes >= config['maxLayover']:
            return False
        if flights[i]['arrival_airport']['id'] != flights[i + 1]['departure_airport']['id']:
            return False
    return True


def make_query(config, pair):
    routes = [(config['origin'], config['destination'], pair['out'])]
    if pair.get('back'):
        routes.append((config['destination'], config['origin'], pair['back']))
    return create_query(
        flights=[FlightQuery(date=d, from_airport=a, to_airport=b, max_stops=config['maxStops'], max_layover_minutes=config['maxLayover'] - 1) for a, b, d in routes],
        trip='round-trip' if pair.get('back') else 'one-way',
        seat=['economy', 'premium-economy', 'business', 'first'][config['cabin'] - 1],
        passengers=Passengers(adults=config['adults'], children=config.get('children', 0)),
        currency=config['currency'], language='en', hide_separate_and_self_transfer=True,
    )


def select_outbound(query, outbound):
    selected = copy.deepcopy(query)
    # Observed Google FlightData selection schema: field 4 contains segments;
    # segment fields 1/2/3/5/6 are origin/date/destination/carrier/flight number.
    # Also documented by boryzo/google-flights-parser (MIT).
    for f in outbound['flights']:
        number = f['flight_number']
        segment = b''.join(_field(n, value) for n, value in [
            (1, f['departure_airport']['id']), (2, f['departure_airport']['time'][:10]),
            (3, f['arrival_airport']['id']), (5, number[:2]), (6, number[2:]),
        ])
        selected.flight_data[0].MergeFromString(_field(4, segment))
    return selected


class GoogleFlights:
    def __init__(self, delay=2):
        self.client = Client(impersonate='chrome_145', impersonate_os='macos', referer=True, cookie_store=True, timeout=35)
        self.delay = delay
        self.last_request = 0

    def fetch(self, query):
        remaining = self.delay - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request = time.monotonic()
        try:
            response = self.client.get('https://www.google.com/travel/flights', params=query.params())
        except Exception as exc:
            raise ScraperError('Flight website could not be reached.') from exc
        if response.status_code != 200:
            raise ScraperError(f'Flight website returned HTTP {response.status_code}; stopping without retries.')
        return response.text

    def search(self, config, pair):
        query = make_query(config, pair)
        options = parse_options(self.fetch(query), config['currency'])
        outbound = [o for o in options if acceptable(o, config, config['origin'], config['destination'], pair['out'])]
        if not pair.get('back'):
            return [{'pair': pair, 'out': o, 'back': None} for o in outbound[:5]]
        # One outbound option per date pair bounds runtime on free runners.
        if not outbound:
            return []
        selected = outbound[0]
        return_options = parse_options(self.fetch(select_outbound(query, selected)), config['currency'])
        eligible = [o for o in return_options if acceptable(o, config, config['destination'], config['origin'], pair['back'])]
        if return_options and not any(o['flights'][0]['departure_airport']['id'] == config['destination'] for o in return_options):
            raise ScraperError('Google did not select the outbound flight; refusing to treat it as a verified round trip.')
        return [{'pair': pair, 'out': selected, 'back': o} for o in eligible[:5]]
