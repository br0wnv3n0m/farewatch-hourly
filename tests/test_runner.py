import os
import unittest
from unittest.mock import patch
import runner


class RunnerTests(unittest.TestCase):
    def test_one_workflow_visits_each_watch_once_and_continues_after_search_failure(self):
        calls = []
        jobs = [{'runId': 'a', 'watchId': 1}, {'runId': 'b', 'watchId': 2}, {'skip': 'Finished'}]

        def api(base, token, path, payload):
            calls.append(payload['exclude'][:])
            return jobs.pop(0)

        with patch.dict(os.environ, {'FAREWATCH_URL': 'https://farewatch.test', 'FAREWATCH_RUNNER_TOKEN': 'r' * 64}), patch('sys.argv', ['runner.py']), patch.object(runner, 'api', side_effect=api), patch.object(runner, 'process_job', side_effect=[1, 0]) as process:
            self.assertEqual(runner.execute(), 1)
        self.assertEqual(calls, [[], [1], [1, 2]])
        self.assertEqual(process.call_count, 2)

    def test_older_single_watch_server_is_compatible(self):
        with patch.dict(os.environ, {'FAREWATCH_URL': 'https://farewatch.test', 'FAREWATCH_RUNNER_TOKEN': 'r' * 64}), patch('sys.argv', ['runner.py']), patch.object(runner, 'api', return_value={'runId': 'a'}) as api, patch.object(runner, 'process_job', return_value=0):
            self.assertEqual(runner.execute(), 0)
            self.assertEqual(api.call_count, 1)
