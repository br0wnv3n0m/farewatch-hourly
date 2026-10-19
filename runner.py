"""Runs in GitHub Actions; private trip settings stay in Cloudflare D1."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from scraper import GoogleFlights, ScraperError


def api(base, token, path, payload):
    request = urllib.request.Request(base + '/api/runner/' + path, data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'FarewatchRunner/1.0'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read(20000).decode('utf-8', errors='replace')
        if 'application/json' in exc.headers.get('Content-Type', ''):
            try:
                reason = json.loads(raw).get('error', 'Request rejected.')
            except ValueError:
                reason = 'Invalid API response.'
        else:
            match = re.search(r'error\s*(?:code\s*[:=]?\s*)?(1\d{3})', raw, re.I)
            reason = 'Request rejected before reaching the dashboard API.' + (' Cloudflare error ' + match[1] + '.' if match else '')
        raise RuntimeError(f'Dashboard API returned HTTP {exc.code}. {reason}') from None
    except (urllib.error.URLError, ValueError, TimeoutError):
        raise RuntimeError('Could not reach the dashboard API.') from None


def execute():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', help='Local JSON with config and pairs; does not report or send email.')
    parser.add_argument('--output', help='Where to save local probe results.')
    args = parser.parse_args()
    if args.probe:
        job = json.loads(Path(args.probe).read_text(encoding='utf-8'))
        base = token = ''
    else:
        base = os.environ.get('FAREWATCH_URL', '').rstrip('/')
        token = os.environ.get('FAREWATCH_RUNNER_TOKEN', '')
        url = urllib.parse.urlparse(base)
        if url.scheme != 'https' or not url.netloc or url.username or url.password or url.path or url.query or url.fragment or len(token) < 32:
            raise RuntimeError('Set FAREWATCH_URL to your HTTPS dashboard origin and FAREWATCH_RUNNER_TOKEN to its runner secret (32+ characters).')
        excluded, failed = [], False
        for _ in range(10):
            job = api(base, token, 'job', {'exclude': excluded})
            if job.get('skip'):
                print(job['skip'])
                break
            failed = process_job(job, base, token, args) != 0 or failed
            # Older single-watch servers omit watchId; remain compatible while deploying.
            if not job.get('watchId'):
                break
            excluded.append(job['watchId'])
        return 1 if failed else 0
    return process_job(job, base, token, args)


def process_job(job, base, token, args):
    checked, offers, errors = [], [], 0
    client = GoogleFlights()
    for pair in job['pairs']:
        try:
            offers.extend(client.search(job['config'], pair))
            checked.append(pair)
        except ScraperError as exc:
            # Do not print settings, dates, raw HTML, tokens or private email in public logs.
            print('Search failed: ' + str(exc), file=sys.stderr)
            errors += 1
            break
        except Exception:
            print('Search failed due to an unexpected scraper response. No uncertain offers were accepted.', file=sys.stderr)
            errors += 1
            break
    report = {'runId': job.get('runId'), 'currency': job['config']['currency'], 'checked': checked, 'offers': offers[:50], 'errorCount': errors}
    if args.probe:
        if args.output:
            Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    else:
        result = api(base, token, 'report', report)
        print(result['message'])
    print(f'Finished: {len(checked)} date combinations; {len(offers)} candidate itineraries; {errors} failed searches.')
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        sys.exit(execute())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
