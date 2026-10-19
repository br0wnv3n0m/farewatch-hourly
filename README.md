# Farewatch hourly checker

An experimental hourly Google Flights scraper for the Farewatch dashboard. No paid flight API is used. Your watch settings, recipient email and Resend credentials remain in Cloudflare; this repository contains no personal trip configuration.

## Connect it

This folder belongs beside the main `farewatch` folder. In PowerShell, open the main Farewatch folder and run `npm.cmd run connect:github`. Sign into GitHub CLI when instructed, then rerun the command. The helper creates a public `farewatch-hourly` repository in the authenticated account, pushes only the named runner source files, and securely connects it to your existing dashboard. It does not upload the full Farewatch folder, database, `.env` files or unrelated files.

The connection helper generates a separate random runner credential in memory and sends it directly to Cloudflare's `RUNNER_TOKEN` secret and GitHub's `FAREWATCH_RUNNER_TOKEN` Actions secret. It also sets `FAREWATCH_URL`. It never saves the credential to a file or prints it. The dashboard password and Resend key are not sent to GitHub. The runner can request the next batch and report checked itineraries, but cannot open the admin dashboard or change your search settings.

The current helper uses the existing Farewatch deployment address; if reusing the project for another deployment, update its `base` constant before connecting.

## What it checks

- The workflow is scheduled at minute **17 of every hour, UTC**. GitHub can delay or drop scheduled jobs, so this is not a guarantee of exact hourly execution.
- Default: five date combinations per run; choose 1–10 in the dashboard. Twenty-five combinations take approximately five successful hourly runs at the default setting. It does not check all combinations every hour.
- For round trips it selects one eligible outbound and then checks its actual return options. The return result carries the full party's round-trip price. It never adds two independent one-way fares and calls them a through round trip.
- Stops, routes, dates and layovers are checked for both directions again on the server. Missing or inconsistent data fails the search rather than producing a false cheap fare. Checked bags remain unverified.
- Only under-budget results are saved by the dashboard. Resend sends a digest for eligible new or sufficiently cheaper fares, using the existing baggage consent and duplicate-alert rules.
- At most 50 offers are reported per run. Google requests are spaced by at least two seconds, use finite timeouts and stop on an error, consent/block page, or an inconsistent response. No CAPTCHA solving, proxy rotation or paid integrations are included.
- No SerpApi requests are made in scraper mode. Switching back to SerpApi in the dashboard restores its original daily schedule and free credit caps.

## GitHub settings

Standard GitHub-hosted runners are free for public repositories. This workflow uses standard Ubuntu runners, read-only repository permissions, no artifacts, and no paid larger runners. Keep personal data and secrets out of committed files and avoid printing private settings in Actions logs. For private repositories, runner minutes and storage are subject to account allowances.

In a public repository GitHub disables scheduled workflows after 60 days without repository activity. Visit Actions to re-enable the workflow when necessary. Check your Farewatch Recent checks timestamps; a stale timestamp means it has not checked recently. A missing heartbeat does not generate a separate email.

To run immediately: repository → **Actions** → **Hourly flight check** → **Run workflow**. The workflow file must be on the default branch. To stop searching, pause the watch in Farewatch. You can also disable the workflow from its Actions page.

## Manual connection (if you do not use the helper)

1. Create a public repository and upload this folder's contents, including `.github/workflows/check.yml`, preserving the directories.
2. Generate a random credential of at least 32 characters. Put it in Cloudflare as `RUNNER_TOKEN`, and in GitHub repository Settings → Secrets and variables → Actions as `FAREWATCH_RUNNER_TOKEN`.
3. Add the dashboard origin (HTTPS URL with no trailing path) as the GitHub Actions secret `FAREWATCH_URL`.
4. In the Farewatch dashboard choose **Experimental scraper · hourly on GitHub**, set the combinations per run, keep your existing trip settings, enable the watch, and save.
5. Run the workflow manually once and verify its result before relying on the schedule.

## Development and verification

`python -m pip install -r requirements.txt`

`python -m unittest discover -s tests -v`

For a live check without saving results or sending email, pass `python runner.py --probe PATH_TO_PRIVATE_JSON --output PRIVATE_RESULT_PATH`. The JSON contains `config` (origin, destination, adults, children, cabin 1–4, currency, maxStops, maxLayover in minutes) and `pairs` (objects with `out` and optional `back` ISO dates). Never commit a real trip probe file.

The adapter uses `fast-flights` to encode search filters and its protobuf classes to verify currency and price. Its default parser is not used because a live test found it crashed on unpriced results. Selected outbound segments use the observed FlightData field-4 schema, also documented in the MIT-licensed boryzo/google-flights-parser project. Google's response structure is undocumented and can change at any time.

References:

- https://github.com/AWeirdDev/flights
- https://github.com/boryzo/google-flights-parser
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

## Multiple watches

Each hourly run visits up to 10 enabled scraper watches once. Each watch has its own date cursor and batch size. Paused, expired and SerpApi watches are skipped. Failed searches do not prevent attempts for other watches. A 45-minute job timeout applies; the next run starts with the least recently checked watch. Watch names and recipient emails are omitted from runner job settings.
