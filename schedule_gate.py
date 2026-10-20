"""Retry scheduling frequently without repeating a successful hourly fare check."""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request


def recent_check_succeeded(fetch, repository, workflow, now):
    cutoff = now - dt.timedelta(hours=1)
    query = urllib.parse.urlencode({
        'status': 'success', 'per_page': 100,
        # Include jobs that started earlier but finished within the last hour.
        'created': '>=' + (cutoff - dt.timedelta(days=1)).isoformat(),
    })
    runs = fetch(f'/repos/{repository}/actions/workflows/{workflow}/runs?{query}')
    for run in runs['workflow_runs']:
        completed = dt.datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
        if completed <= cutoff:
            continue
        # A skipped fare-check job also makes the workflow green. It must not
        # count as a successful check or it would indefinitely postpone checks.
        jobs = fetch(f'/repos/{repository}/actions/runs/{run["id"]}/jobs?per_page=100')
        if any(job['name'] == 'check' and job['conclusion'] == 'success'
               for job in jobs['jobs']):
            return True
    return False


def main():
    due = True
    if os.environ['GITHUB_EVENT_NAME'] != 'workflow_dispatch':
        def fetch(path):
            request = urllib.request.Request('https://api.github.com' + path, headers={
                'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'FarewatchSchedule/1.0',
            })
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)

        due = not recent_check_succeeded(
            fetch, os.environ['GITHUB_REPOSITORY'], 'check.yml',
            dt.datetime.now(dt.timezone.utc))
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write(f'due={str(due).lower()}\n')
    print('Fare check is due.' if due else 'A fare check succeeded within the last hour; skipping this attempt.')


if __name__ == '__main__':
    main()
