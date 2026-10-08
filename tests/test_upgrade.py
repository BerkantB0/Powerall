"""Exercise the real upgrade script/wrapper using a fake Docker executable."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
FAKE='''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['UPGRADE_TEST_LOG'],'a') as log: log.write(json.dumps(args)+'\\n')
# Real wrapper must keep its env file and NPM host-IP override on every call.
prefix=['compose','--env-file','deploy/hosting.env','-f','compose.yaml','-f','compose.npm-ip.yaml']
assert args[:len(prefix)]==prefix
command=args[len(prefix):]
failure=os.environ.get('UPGRADE_TEST_FAIL')
if failure==command[0] or (failure=='migrate' and command[-1]=='migrate') or (failure=='api-health' and command[0]=='up' and command[-1]=='api'): sys.exit(7)
if command[:4]==['ps','--all','-q','database']:
 if not os.environ.get('UPGRADE_TEST_NO_DB'): print('db-fixture')
elif command[:3]==['ps','--all','-q']:
 count={'api':2,'worker':3,'frontend':1,'gateway':1}[command[3]]
 for i in range(count): print(f'fixture-{i}')
'''
class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name)
        (self.root/'scripts').mkdir()
        for name in ['upgrade.sh','compose-npm-ip.sh']:
            p=self.root/'scripts'/name;p.write_bytes((ROOT/'scripts'/name).read_bytes());p.chmod(0o755)
        (self.root/'VERSION').write_text('0.2.0\n')
        docker=self.root/'docker';docker.write_text(FAKE);docker.chmod(0o755)
        self.log=self.root/'calls.jsonl'
        self.env=dict(os.environ,PATH=str(self.root)+os.pathsep+os.environ['PATH'],UPGRADE_TEST_LOG=str(self.log))
    def tearDown(self):self.folder.cleanup()
    def run_upgrade(self,**env):
        return subprocess.run([str(self.root/'scripts/upgrade.sh')],cwd='/tmp',env=dict(self.env,**env),capture_output=True,text=True)
    def commands(self):
        return [json.loads(line)[7:] for line in self.log.read_text().splitlines()]
    def test_order_replica_preservation_and_no_volume_or_database_replacement(self):
        result=self.run_upgrade();self.assertEqual(result.returncode,0,result.stderr)
        commands=self.commands();self.assertIn(['build','api','frontend'],commands)
        up=[c for c in commands if c[0]=='up']
        self.assertEqual([c[-1] for c in up],['database','api','worker','frontend','gateway'])
        self.assertIn('--no-recreate',up[0]);self.assertNotIn('--force-recreate',up[0])
        for c,scale in zip(up[1:],['api=2','worker=3','frontend=1','gateway=1']):
            self.assertIn('--wait',c);self.assertIn('--no-deps',c);self.assertIn(scale,c)
        self.assertLess(commands.index(['run','--rm','--no-deps','migrate']),commands.index(up[1]))
        self.assertFalse(any(c[0] in ['down','pull','rm'] for c in commands))
        self.assertFalse((self.root/'.upgrade.lock').exists())
    def test_build_failure_stops_before_migrations_and_service_updates(self):
        result=self.run_upgrade(UPGRADE_TEST_FAIL='build');self.assertEqual(result.returncode,7)
        self.assertFalse(any(c[0] in ['run','up'] for c in self.commands()))
        self.assertFalse((self.root/'.upgrade.lock').exists())
    def test_gateway_validation_failure_stops_before_service_updates(self):
        result=self.run_upgrade(UPGRADE_TEST_FAIL='run');self.assertEqual(result.returncode,7)
        self.assertFalse(any(c[0]=='up' for c in self.commands()))
        self.assertFalse((self.root/'.upgrade.lock').exists())
    def test_migration_failure_stops_before_application_recreation(self):
        result=self.run_upgrade(UPGRADE_TEST_FAIL='migrate');self.assertEqual(result.returncode,7)
        self.assertEqual([c[-1] for c in self.commands() if c[0]=='up'],['database'])
        self.assertFalse((self.root/'.upgrade.lock').exists())
    def test_unhealthy_api_stops_before_worker_frontend_and_gateway(self):
        result=self.run_upgrade(UPGRADE_TEST_FAIL='api-health');self.assertEqual(result.returncode,7)
        self.assertEqual([c[-1] for c in self.commands() if c[0]=='up'],['database','api'])
        self.assertNotIn('upgrade complete',result.stdout)
    def test_uninstalled_deployment_is_not_created(self):
        self.assertNotEqual(self.run_upgrade(UPGRADE_TEST_NO_DB='1').returncode,0)
        self.assertFalse(any(c[0] in ['build','run','up'] for c in self.commands()))
    def test_existing_lock_is_not_removed(self):
        (self.root/'.upgrade.lock').mkdir()
        self.assertNotEqual(self.run_upgrade().returncode,0)
        self.assertTrue((self.root/'.upgrade.lock').exists())
    def test_release_versions_match(self):
        version=(ROOT/'VERSION').read_text().strip()
        self.assertRegex(version,r'^\d+\.\d+\.\d+$')
        for name in ['package.json','package-lock.json']:
            self.assertEqual(json.loads((ROOT/'dashboard'/name).read_text())['version'],version)
        from powerall import __version__
        self.assertEqual(__version__,version)
