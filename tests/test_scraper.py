import base64
import json
import unittest
from unittest.mock import patch

from fast_flights.pb.flights_pb2 import ItinerarySummary
from scraper import FlightDataUnavailable, ScraperError, acceptable, make_query, parse_options, select_outbound, GoogleFlights


CONFIG = {'origin': 'AAA', 'destination': 'BBB', 'maxStops': 1, 'maxLayover': 480, 'adults': 2, 'children': 0, 'cabin': 1, 'currency': 'CAD'}
PAIR = {'out': '2099-01-01', 'back': '2099-02-01'}


def fixture(reverse=False, price=2500, currency='CAD', unpriced=False):
    segments = []
    airports = ['BBB', 'CCC', 'AAA'] if reverse else ['AAA', 'CCC', 'BBB']
    for i in range(2):
        s = [None] * 23
        s[3], s[6] = airports[i:i + 2]
        s[8], s[10] = [8 + i * 4], [10 + i * 4]
        s[20] = s[21] = [2099, 2 if reverse else 1, 1]
        s[22] = ['AB', str(123 + i), None, 'Example Air']
        segments.append(s)
    raw = [None] * 25
    raw[2], raw[13] = segments, [[120, 'CCC', 'CCC']]
    summary = ItinerarySummary(flights='AB123|AB124')
    summary.price.price = price * 100
    summary.price.currency = currency
    entry = [raw, [[None] if unpriced else [None, price], base64.b64encode(summary.SerializeToString()).decode()]]
    payload = [None] * 31
    payload[2] = [[entry]]
    return "<script class='ds:1'>AF_initDataCallback({key:'ds:1',data:" + json.dumps(payload) + ",sideChannel:{}});</script>"


class AdapterTests(unittest.TestCase):
    def test_callback_metadata_is_separate_from_json(self):
        html = fixture().replace('sideChannel:{}', 'extraMetadata: true,sideChannel:{"a":1,"b":2}')
        self.assertEqual(parse_options(html, 'CAD')[0]['price'], 2500)

    def test_provider_error_is_never_an_empty_success(self):
        html = "<script class='ds:1'>AF_initDataCallback({key:'ds:1',data:[],errorHasStatus: true,sideChannel:{}});</script>"
        with self.assertRaises(FlightDataUnavailable):
            parse_options(html, 'CAD')

    def test_provider_error_retries_once_and_can_recover(self):
        client = GoogleFlights()
        error = "<script class='ds:1'>data:[],errorHasStatus: true,sideChannel:{}</script>"
        with patch.object(client, 'fetch', side_effect=[error, fixture()]) as fetch, patch('scraper.time.sleep') as sleep:
            self.assertEqual(client.options(None, 'CAD')[0]['price'], 2500)
            self.assertEqual(fetch.call_count, 2)
            sleep.assert_called_once_with(5)

    def test_provider_error_can_recover_on_last_bounded_retry(self):
        client = GoogleFlights()
        error = "<script class='ds:1'>data:[],errorHasStatus: true,sideChannel:{}</script>"
        with patch.object(client, 'fetch', side_effect=[error, error, fixture()]) as fetch, patch('scraper.time.sleep') as sleep:
            self.assertEqual(client.options(None, 'CAD')[0]['price'], 2500)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 15])

    def test_persistent_provider_error_stays_failed_after_bounded_retries(self):
        client = GoogleFlights()
        error = "<script class='ds:1'>data:[],errorHasStatus: true,sideChannel:{}</script>"
        with patch.object(client, 'fetch', return_value=error) as fetch, patch('scraper.time.sleep'):
            with self.assertRaises(FlightDataUnavailable):
                client.options(None, 'CAD')
            self.assertEqual(fetch.call_count, 3)

    def test_malformed_or_blocked_page_is_not_retried(self):
        client = GoogleFlights()
        with patch.object(client, 'fetch', return_value='<html>Consent</html>') as fetch, patch('scraper.time.sleep') as sleep:
            with self.assertRaises(ScraperError):
                client.options(None, 'CAD')
            self.assertEqual(fetch.call_count, 1)
            sleep.assert_not_called()

    def test_prices_and_currency(self):
        results = parse_options(fixture(price=2345), 'CAD')
        self.assertEqual(results[0]['price'], 2345)
        self.assertEqual(results[0]['flights'][0]['flight_number'], 'AB123')
        with self.assertRaises(ScraperError):
            parse_options(fixture(currency='USD'), 'CAD')

    def test_unpriced_entry_is_not_a_free_flight(self):
        self.assertEqual(parse_options(fixture(unpriced=True), 'CAD'), [])

    def test_broken_or_blocked_response_fails_closed(self):
        for html in ['<html>Consent required</html>', "<script class='ds:1'>data:[],x:{}</script>"]:
            with self.assertRaises(ScraperError):
                parse_options(html, 'CAD')

    def test_both_route_and_layover_are_checked(self):
        leg = parse_options(fixture(), 'CAD')[0]
        self.assertTrue(acceptable(leg, CONFIG, 'AAA', 'BBB', PAIR['out']))
        self.assertFalse(acceptable(leg, CONFIG, 'BBB', 'AAA', PAIR['back']))
        leg['layovers'][0]['duration'] = 480
        self.assertFalse(acceptable(leg, CONFIG, 'AAA', 'BBB', PAIR['out']))

    def test_query_keeps_passenger_count_and_filters(self):
        q = make_query(CONFIG, PAIR)
        self.assertEqual(len(q.passengers), 2)
        self.assertEqual(q.currency, 'CAD')
        self.assertEqual(q.flight_data[0].max_layover_minutes, 479)

    def test_outbound_selection_does_not_mutate_base_query(self):
        q = make_query(CONFIG, PAIR)
        original = q.to_bytes()
        selected = select_outbound(q, parse_options(fixture(), 'CAD')[0])
        self.assertEqual(q.to_bytes(), original)
        self.assertNotEqual(selected.to_bytes(), original)
        self.assertIn(b'123', selected.flight_data[0].SerializeToString())

    def test_round_trip_uses_selected_return_price(self):
        class Stub(GoogleFlights):
            def __init__(self):
                self.calls = 0
            def fetch(self, query):
                self.calls += 1
                return fixture(reverse=self.calls > 1, price=2600 if self.calls > 1 else 2400)
        client = Stub()
        result = client.search(CONFIG, PAIR)
        self.assertEqual(client.calls, 2)
        self.assertEqual(result[0]['back']['price'], 2600)
        self.assertEqual(result[0]['back']['flights'][0]['departure_airport']['id'], 'BBB')

    def test_failed_outbound_selection_is_not_a_round_trip(self):
        class Stub(GoogleFlights):
            def __init__(self): pass
            def fetch(self, query): return fixture()
        with self.assertRaises(ScraperError):
            Stub().search(CONFIG, PAIR)


if __name__ == '__main__':
    unittest.main()
