"""Queue transition regressions; no provider calls or real household data."""
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from powerall.jobs import QueuedSync, retry_delay

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE import_jobs(user_id TEXT PRIMARY KEY, days INTEGER, state TEXT, available_at INTEGER, attempts INTEGER DEFAULT 0, owner TEXT)')
        self.queue=QueuedSync(self,'fixture-household')
    def tearDown(self): self.db.close()
    @contextmanager
    def connection(self):
        with self.db: yield self.db
    def job(self): return dict(self.db.execute('SELECT * FROM import_jobs').fetchone())
    def test_manual_retry_accelerates_and_preserves_history(self):
        self.assertTrue(self.queue.trigger(365))
        self.db.execute("UPDATE import_jobs SET available_at=9999999999,attempts=4")
        with patch('powerall.jobs.time.time',return_value=1000):
            self.assertTrue(self.queue.trigger())
        job=self.job()
        self.assertEqual(job['days'],365);self.assertEqual(job['available_at'],1000)
        self.assertEqual(job['attempts'],0)
        self.assertFalse(self.queue.trigger())
    def test_running_and_queued_jobs_are_not_replaced(self):
        self.assertTrue(self.queue.trigger(90));self.assertFalse(self.queue.trigger(30))
        self.db.execute("UPDATE import_jobs SET state='running',attempts=4,owner='fixture-owner'")
        self.assertFalse(self.queue.trigger())
        self.assertEqual(self.job()['owner'],'fixture-owner');self.assertEqual(self.job()['days'],90)
    def test_idle_refresh_clears_old_range_and_explicit_retry_can_choose_range(self):
        self.assertTrue(self.queue.trigger(365))
        self.db.execute("UPDATE import_jobs SET state='idle'")
        self.assertTrue(self.queue.trigger());self.assertIsNone(self.job()['days'])
        self.db.execute('UPDATE import_jobs SET attempts=1')
        self.assertTrue(self.queue.trigger(30));self.assertEqual(self.job()['days'],30)
    def test_backoff_starts_at_minute_and_caps_at_fifteen(self):
        self.assertEqual([retry_delay(i) for i in range(7)],[60,120,240,480,900,900,900])
        self.assertEqual(retry_delay(100000),900)
