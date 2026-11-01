from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import tempfile
import unittest

from workspace.brain import Brain
from workspace.server import backup, initialize, restore, set_password


def claim_worker(path, agent):
    return Brain(path).claim(agent)


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = initialize(self.root / 'data')
        self.brain = Brain(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def expire(self, job):
        with self.brain.connection(write=True) as db:
            db.execute('UPDATE agent_jobs SET lease_until=0 WHERE id=?', (job['id'],))

    def test_parallel_process_claims_are_exclusive(self):
        for number in range(12):
            self.brain.enqueue(f'task-{number}', f'Task {number}', f'scope-{number}', 'lead')
        with ProcessPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(claim_worker, [self.database] * 12, [f'agent-{n}' for n in range(12)]))
        self.assertEqual(len({job['id'] for job in claims}), 12)
        self.assertEqual(len({job['token'] for job in claims}), 12)
        self.assertIsNone(self.brain.claim('extra-agent'))
        for job in self.brain.snapshot()['jobs']:
            self.assertEqual(job['attempts'], 1)
            self.assertNotIn('token', job)

    def test_scope_reservation_blocks_overlapping_work(self):
        self.brain.enqueue('one', 'First edit', 'same-files', 'lead')
        self.brain.enqueue('two', 'Second edit', 'same-files', 'lead')
        first = self.brain.claim('one')
        self.assertIsNone(self.brain.claim('two'))
        self.brain.heartbeat(first['id'], 'one', first['token'], 600)
        self.brain.finish(first['id'], 'one', first['token'], 'Complete', 'Verified changes')
        self.assertEqual(self.brain.claim('two')['task_key'], 'two')

    def test_expired_work_requires_reconciliation_and_fences_stale_agent(self):
        self.brain.enqueue('one', 'First edit', 'same-files', 'lead', max_attempts=2)
        self.brain.enqueue('two', 'Second edit', 'same-files', 'lead')
        first = self.brain.claim('worker')
        self.expire(first)
        with self.assertRaises(ValueError):
            self.brain.heartbeat(first['id'], 'worker', first['token'])
        self.assertIsNone(self.brain.claim('replacement'))
        self.assertEqual(self.brain.snapshot()['jobs'][0]['state'], 'blocked')
        self.brain.reconcile(first['id'], 'lead', 'requeue', 'Inspected files; no operation was applied.')
        second = self.brain.claim('worker')
        with self.assertRaises(ValueError):
            self.brain.finish(first['id'], 'worker', first['token'], 'Stale completion', 'Old claim')
        self.expire(second)
        self.assertIsNone(self.brain.claim('replacement'))
        with self.assertRaises(ValueError):
            self.brain.reconcile(first['id'], 'lead', 'requeue', 'Try again')
        self.brain.reconcile(first['id'], 'lead', 'cancel', 'Retry budget exhausted; verified worker stopped.')
        self.assertEqual(self.brain.claim('next')['task_key'], 'two')

    def test_record_conflicts_audit_history_and_queue_idempotency(self):
        self.brain.remember('purpose', 'goal', 'Help our family', 'Owner request', 'lead')
        with self.assertRaises(ValueError):
            self.brain.remember('purpose', 'goal', 'Stale change', 'Old session', 'other')
        self.brain.remember('purpose', 'goal', 'Build a family desk', 'Owner clarification', 'lead', 1)
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot['records'][0]['revision'], 2)
        self.assertEqual(len(snapshot['recent_events']), 2)
        one = self.brain.enqueue('task', 'Same task', 'docs', 'lead')
        two = self.brain.enqueue('task', 'Same task', 'docs', 'lead')
        self.assertEqual(one['id'], two['id'])
        with self.assertRaises(ValueError):
            self.brain.enqueue('task', 'Different task', 'docs', 'lead')

    def test_shared_memory_survives_full_database_backup(self):
        set_password(self.database, 'a-test-password-only')
        self.brain.remember('decision', 'decision', 'Use SQLite', 'Owner request', 'lead')
        self.brain.enqueue('migration', 'Prepare recovery', 'ops', 'lead')
        snapshot = self.root / 'snapshot.sqlite3'
        backup(self.database, snapshot)
        restore(snapshot, self.root / 'recovered')
        self.assertEqual(Brain(self.root / 'recovered' / 'family.sqlite3').snapshot(), self.brain.snapshot())


if __name__ == '__main__':
    unittest.main()
