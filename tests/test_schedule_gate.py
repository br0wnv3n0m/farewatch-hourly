import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import schedule_gate


class ScheduleGateTests(unittest.TestCase):
    now = dt.datetime(2026, 10, 20, 4, 0, tzinfo=dt.timezone.utc)

    def test_recent_successful_fare_check_suppresses_duplicate(self):
        fetch = Mock(side_effect=[
            {'workflow_runs': [{'id': 1, 'updated_at': '2026-10-20T03:30:00Z'}]},
            {'jobs': [{'name': 'check', 'conclusion': 'success'}]},
        ])
        self.assertTrue(schedule_gate.recent_check_succeeded(fetch, 'owner/repo', 'check.yml', self.now))

    def test_green_skipped_workflow_does_not_postpone_next_check(self):
        fetch = Mock(side_effect=[
            {'workflow_runs': [{'id': 2, 'updated_at': '2026-10-20T03:50:00Z'},
                               {'id': 1, 'updated_at': '2026-10-20T02:59:00Z'}]},
            {'jobs': [{'name': 'due', 'conclusion': 'success'},
                      {'name': 'check', 'conclusion': 'skipped'}]},
        ])
        self.assertFalse(schedule_gate.recent_check_succeeded(fetch, 'owner/repo', 'check.yml', self.now))
        self.assertEqual(fetch.call_count, 2)

    def test_no_successful_runs_allows_retry(self):
        fetch = Mock(return_value={'workflow_runs': []})
        self.assertFalse(schedule_gate.recent_check_succeeded(fetch, 'owner/repo', 'check.yml', self.now))

    def test_manual_run_bypasses_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'output')
            with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_OUTPUT': output}), patch('urllib.request.urlopen') as request:
                schedule_gate.main()
                request.assert_not_called()
            self.assertEqual(Path(output).read_text(), 'due=true\n')

    def test_api_failure_does_not_silently_allow_duplicate(self):
        fetch = Mock(side_effect=RuntimeError('API unavailable'))
        with self.assertRaises(RuntimeError):
            schedule_gate.recent_check_succeeded(fetch, 'owner/repo', 'check.yml', self.now)
