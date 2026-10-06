import json
from decimal import Decimal
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from powerall.analytics import day_summary, dashboard
from powerall.bright import Bright
from powerall.providers import Meter, ProviderError
from powerall.server import make_handler, load_env
from powerall.storage import Store
from powerall.sync import SyncService
from powerall.tariffs import manual_tariff, tariff_at, interval_rate, usage_cost, TariffError
from powerall.timeutils import date_bounds, parse_instant, HALF_HOUR, UK


def tariff(**changes):
    result = {'id': 't', 'meter_id': 'm', 'source': 'manual', 'label': 'Test',
        'effective_from': 0, 'effective_to': None, 'rate': '30', 'standing': '50',
        'windows': [{'start': '00:30', 'end': '07:30', 'rate': '10'}],
        'clock': 'Europe/London', 'vat': 'included', 'warning': None}
    result.update(changes)
    return result

class ClockTests(unittest.TestCase):
    def test_uk_days_have_46_48_or_50_periods(self):
        for day, count in [('2026-03-29', 46), ('2026-10-25', 50), ('2026-10-05', 48)]:
            start, end = date_bounds(day)
            self.assertEqual((end-start)//HALF_HOUR, count)
            readings = {s: '0.1' for s in range(start,end,HALF_HOUR)}
            result = day_summary(date.fromisoformat(day), readings, [tariff()], end+1)
            self.assertEqual(result['received'], count)
            self.assertEqual(result['missing'], 0)
            self.assertEqual(len(result['slots']), count)
            self.assertAlmostEqual(result['kwh'], count*.1)

    def test_fall_repeated_hour_is_distinct(self):
        start,end=date_bounds('2026-10-25')
        result=day_summary(date(2026,10,25), {}, [], end+1)
        repeated=[s for s in result['slots'] if s['time']=='01:00']
        self.assertEqual(len(repeated),2)
        self.assertEqual({s['zone'] for s in repeated},{'BST','GMT'})
        self.assertNotEqual(repeated[0]['start'],repeated[1]['start'])

    def test_uk_vs_utc_tariff_windows(self):
        instant=parse_instant('2026-07-01T06:30:00Z') # 07:30 BST
        self.assertEqual(interval_rate(tariff(),instant)[0],Decimal('30'))
        self.assertEqual(interval_rate(tariff(clock='UTC'),instant)[0],Decimal('10'))

    def test_window_crossing_midnight(self):
        t=tariff(windows=[{'start':'23:30','end':'04:30','rate':'7'}])
        for instant in ('2026-01-02T23:30:00Z','2026-01-03T00:00:00Z','2026-01-03T04:00:00Z'):
            self.assertEqual(interval_rate(t,parse_instant(instant))[0],Decimal('7'))
        self.assertEqual(interval_rate(t,parse_instant('2026-01-03T04:30:00Z'))[0],Decimal('30'))

class CostTests(unittest.TestCase):
    def test_cost_and_standing_charge(self):
        start,end=date_bounds('2026-01-02')
        result=day_summary(date(2026,1,2),{s:'1' for s in range(start,end,HALF_HOUR)},[tariff()],end+1)
        # 14 off-peak and 34 standard intervals, one standing charge.
        self.assertEqual(result['kwh'],48)
        self.assertEqual(result['usage_cost'],11.6)
        self.assertEqual(result['estimated_cost'],12.1)
        self.assertEqual(result['off_peak_kwh'],14)

    def test_one_daily_standing_charge_on_clock_changes(self):
        for day in ('2026-03-29','2026-10-25'):
            start,end=date_bounds(day)
            result=day_summary(date.fromisoformat(day),{s:'0' for s in range(start,end,HALF_HOUR)},[tariff()],end+1)
            self.assertEqual(result['estimated_cost'],.5)

    def test_missing_reading_is_not_zero(self):
        start,end=date_bounds('2026-01-02')
        readings={s:'0' for s in range(start,end,HALF_HOUR)}
        del readings[start]
        result=day_summary(date(2026,1,2),readings,[tariff()],end+1)
        self.assertEqual(result['missing'],1)
        self.assertIsNone(result['estimated_cost'])
        self.assertEqual(result['slots'][0]['kwh'],None)
        self.assertEqual(result['slots'][1]['kwh'],0)

    def test_unknown_tariff_never_uses_latest_rate_for_past(self):
        start,end=date_bounds('2026-01-02')
        result=day_summary(date(2026,1,2),{s:'1' for s in range(start,end,HALF_HOUR)},[tariff(effective_from=end)],end+1)
        self.assertEqual(result['unpriced'],48)
        self.assertIsNone(result['standing'])
        self.assertIsNone(result['estimated_cost'])
        self.assertEqual(result['known_cost'],0)

    def test_vat_applied_once(self):
        instant=parse_instant('2026-01-02T12:00:00Z')
        self.assertEqual(usage_cost(tariff(vat='excluded'),instant,'1')[0],Decimal('.315'))
        self.assertEqual(usage_cost(tariff(vat='included'),instant,'1')[0],Decimal('.30'))

    def test_manual_override_ends_exclusively(self):
        bright=tariff(source='bright',rate='40')
        manual=tariff(effective_from=100,effective_to=200,rate='20')
        self.assertEqual(tariff_at([bright,manual],99)['rate'],'40')
        self.assertEqual(tariff_at([bright,manual],100)['rate'],'20')
        self.assertEqual(tariff_at([bright,manual],200)['rate'],'40')

    def test_future_intervals_are_not_missing(self):
        start,end=date_bounds('2026-01-02')
        now=start+2*HALF_HOUR+600
        result=day_summary(date(2026,1,2),{start:'1',start+HALF_HOUR:'1'},[tariff()],now)
        self.assertEqual(result['expected'],2)
        self.assertEqual(result['missing'],0)
        self.assertTrue(result['in_progress'])
        self.assertEqual(result['slots'][2]['state'],'future')
        self.assertEqual(result['standing'],.5)

    def test_latest_provider_tariff_wins_after_effective_date(self):
        older=tariff(source='bright',effective_from=100,rate='30')
        newer=tariff(source='bright',effective_from=200,rate='40')
        self.assertEqual(tariff_at([newer,older],199)['rate'],'30')
        self.assertEqual(tariff_at([newer,older],200)['rate'],'40')

class ValidationTests(unittest.TestCase):
    def payload(self,**updates):
        item={'label':'Historical','from':'2026-07-01','to':'2026-10-05','rate':'32.1','standing':'43.61','windows':[]}
        item.update(updates)
        return item
    def test_manual_midnight_uses_uk_offset(self):
        t=manual_tariff(self.payload(),'m')
        self.assertEqual(t['effective_from'],parse_instant('2026-06-30T23:00:00Z'))
    def test_bad_money_and_dates_rejected(self):
        for payload in [self.payload(rate='NaN'),self.payload(standing='-1'),self.payload(rate='Infinity'),self.payload(to='2026-06-30'),self.payload(clock='Mars'),self.payload(vat='maybe')]:
            with self.assertRaises(TariffError): manual_tariff(payload,'m')
    def test_overlapping_windows_rejected(self):
        with self.assertRaises(TariffError):
            manual_tariff(self.payload(windows=[{'start':'23:00','end':'02:00','rate':'5'},{'start':'01:30','end':'04:00','rate':'7'}]),'m')
    def test_off_grid_window_rejected(self):
        with self.assertRaises(TariffError):
            manual_tariff(self.payload(windows=[{'start':'00:15','end':'04:00','rate':'5'}]),'m')
    def test_env_never_executes_shell_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.env'
            path.write_text('export BRIGHT_USERNAME="a@example.com"\nBRIGHT_PASSWORD=\'$(echo secret)#x=1\'\nPOWERALL_PORT=8765 # comment\n')
            result=load_env(path)
            self.assertEqual(result['BRIGHT_PASSWORD'],'$(echo secret)#x=1')
            self.assertEqual(result['POWERALL_PORT'],'8765')

class BrightTests(unittest.TestCase):
    def test_new_structure_preserves_off_peak_legacy_omits(self):
        class Fixture(Bright):
            def _request(self,path,*args,**kwargs):
                if path.endswith('tariff-list'):
                    return {'status':'OK','data':[{'effectiveDate':'2026-10-05 00:00:00','plan':[{'planDetail':[{'standing':43.61},{'rate':32.1}]}]}]}
                return {'status':'OK','data':[{'from':'2026-10-05 00:00:00','type':'tou','structure':[{'planDetail':[{'standing':43.61},{'rate':32.1},{'tourate':13.74,'time':'00:30-07:30'}]}]}]}
        tariffs,warnings=Fixture('unused','unused').tariffs(Meter('e','r','c','Home'))
        self.assertEqual(warnings,[])
        self.assertEqual(len(tariffs),1)
        self.assertEqual(tariffs[0]['windows'],[{'start':'00:30','end':'07:30','rate':'13.74'}])
        self.assertEqual(tariffs[0]['vat'],'unconfirmed')
    def test_dynamic_tariff_is_not_flattened(self):
        class Fixture(Bright):
            def _request(self,*args,**kwargs):
                return {'status':'OK','data':[{'from':'2026-10-05 00:00:00','plan':[{'planDetail':[{'standing':30},{'dynamic':'AGILE'}]}]}]}
        tariffs,warnings=Fixture('unused','unused').tariffs(Meter('e','r',None,'Home'))
        self.assertEqual(tariffs,[])
        self.assertTrue(warnings)
    def test_invalid_readings_and_wh_conversion(self):
        class Fixture(Bright):
            def _request(self,*args,**kwargs):
                return {'status':'OK','data':[[0,1000],[1800,None],[3600,-1],[5400,'NaN'],[7201,2],[7200,500],[9000,50],[True,2]]}
        result=Fixture('unused','unused').readings(Meter('e','r',None,'Home','Wh'),0,9000)
        self.assertEqual(result,[(0,'1'),(7200,'0.5')])
    def test_token_expiry_and_401_reauthenticate(self):
        import urllib.error
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"status":"OK","data":[]}'
        provider=Bright('u','p')
        provider._token='old'
        provider._expires=int(time.time())+3600
        def authenticate():
            provider._token='new';provider._expires=int(time.time())+3600
        with patch.object(provider,'_authenticate',side_effect=authenticate) as auth, patch('urllib.request.urlopen',side_effect=[urllib.error.HTTPError('safe',401,'Denied',{},None),Response()]):
            self.assertEqual(provider._request('/virtualentity')['status'],'OK')
            self.assertEqual(auth.call_count,1)
        provider._expires=0
        with patch.object(provider,'_authenticate',side_effect=authenticate) as auth, patch('urllib.request.urlopen',return_value=Response()):
            provider._request('/virtualentity')
            self.assertEqual(auth.call_count,1)

class StoredTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.folder.name)/'private/db.sqlite3')
        self.store.execute("INSERT INTO meters(id,provider,provider_entity,resource_id,name) VALUES('m','bright','e','r','Home')")
    def tearDown(self):self.folder.cleanup()
    def fill(self,start,end):
        with self.store.connection() as db:
            db.executemany('INSERT INTO readings VALUES(?,?,?,?)',[('m',s,'1',0) for s in range(start,end,HALF_HOUR)])
    def test_current_week_matches_elapsed_previous_week(self):
        now=parse_instant('2026-10-06T12:00:00Z')
        self.fill(date_bounds('2026-09-28')[0],now//1800*1800)
        self.store.upsert_tariff(tariff())
        result=dashboard(self.store,'m','week','2026-10-06',now)
        self.assertTrue(result['comparison']['matched_elapsed_period'])
        self.assertEqual(result['summary']['expected'],result['comparison']['summary']['expected'])
        self.assertEqual(result['comparison']['usage_change_percent'],0)
        self.assertEqual(result['summary']['missing'],0)
    def test_missing_previous_data_suppresses_percent_change(self):
        now=parse_instant('2026-10-06T12:00:00Z')
        self.fill(date_bounds('2026-10-05')[0],now//1800*1800)
        result=dashboard(self.store,'m','day','2026-10-05',now)
        self.assertIsNone(result['comparison']['usage_change_percent'])
    def test_month_boundaries_handle_leap_year(self):
        result=dashboard(self.store,'m','month','2028-02-29',parse_instant('2028-03-05T00:00Z'))
        self.assertEqual(len(result['days']),29)
        self.assertEqual(result['from'],'2028-02-01')
        self.assertEqual(result['to'],'2028-03-01')

class SyncTests(unittest.TestCase):
    def test_chunking_deduplication_correction_and_failure_retention(self):
        class Fake:
            name='bright'
            def __init__(self):self.calls=[];self.value='1';self.fail=False
            def discover(self):
                if self.fail:raise ProviderError('Connection failed.')
                return [Meter('e','r',None,'Home')]
            def tariffs(self,meter):return [],[]
            def readings(self,meter,start,end):
                self.calls.append((start,end))
                return [(start,self.value)]
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'private/db.sqlite3');provider=Fake();sync=SyncService(store,provider,initial_days=21)
            sync._lock.acquire();sync._run(21)
            self.assertEqual(len(provider.calls),3)
            self.assertTrue(all(end-start<=10*86400 for start,end in provider.calls))
            count=store.rows('SELECT COUNT(*) AS count FROM readings')[0]['count']
            provider.value='2';provider.calls=[]
            sync._lock.acquire();sync._run(21)
            self.assertEqual(store.rows('SELECT COUNT(*) AS count FROM readings')[0]['count'],count)
            self.assertTrue(all(r['kwh']=='2' for r in store.rows('SELECT kwh FROM readings')))
            successful=store.sync_state()['last_success']
            provider.fail=True;sync._lock.acquire();sync._run(21)
            self.assertEqual(store.sync_state()['run']['status'],'failed')
            self.assertEqual(store.sync_state()['last_success'],successful)
            self.assertEqual(store.rows('SELECT COUNT(*) AS count FROM readings')[0]['count'],count)
    def test_interrupted_import_is_recovered_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'private/db.sqlite3')
            store.execute("INSERT INTO sync_runs(started,status,phase) VALUES(0,'running','Importing')")
            SyncService(store,None)
            state=store.sync_state()['run']
            self.assertEqual(state['status'],'failed')
            self.assertIn('restart',state['error'])

    def test_scheduler_respects_refresh_interval_and_failed_backoff(self):
        # A fake stop event allows exactly one scheduler iteration.
        class Once:
            def __init__(self):self.stopped=False
            def is_set(self):return self.stopped
            def wait(self,seconds):self.stopped=True
        for status,age,should_trigger in [('success',700,True),('failed',700,False),('failed',3700,True),('running',3700,False)]:
            with tempfile.TemporaryDirectory() as folder:
                store=Store(Path(folder)/'private/db.sqlite3')
                sync=SyncService(store,None,refresh_hours=.1)
                now=int(time.time())
                store.execute('INSERT INTO sync_runs(started,finished,status,phase) VALUES(?,?,?,?)',(now-age,now-age,status,'Test'))
                sync._stop=Once()
                finished=threading.Event()
                with patch.object(sync,'trigger',side_effect=lambda: finished.set()):
                    sync.schedule()
                    limit=time.time()+2
                    while not sync._stop.stopped and time.time()<limit:time.sleep(.005)
                    self.assertTrue(sync._stop.stopped)
                    self.assertEqual(finished.is_set(),should_trigger)

    def test_simultaneous_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            sync=SyncService(Store(Path(folder)/'private/db.sqlite3'),None)
            sync._lock.acquire()
            self.assertFalse(sync.trigger())
            sync._lock.release()

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.store=Store(Path(self.folder.name)/'private/db.sqlite3')
        self.store.execute("INSERT INTO meters(id,provider,provider_entity,resource_id,name) VALUES('m','bright','e','r','Home')")
        class FakeSync:
            def trigger(self,days=None):return True
        self.http=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store,FakeSync(),'session',0))
        port=self.http.server_address[1]
        self.http.RequestHandlerClass=make_handler(self.store,FakeSync(),'session',port)
        self.url=f'http://127.0.0.1:{port}'
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True);self.worker.start()
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.worker.join();self.folder.cleanup()
    def call(self,path,body=None,token=True,origin=None,host=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Powerall-Token']='session'
        if origin:headers['Origin']=origin
        if host:headers['Host']=host
        req=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as error:
            try:return error.code,json.load(error)
            finally:error.close()
    def test_status_does_not_expose_credentials_or_resource_ids(self):
        code,result=self.call('/api/status');self.assertEqual(code,200)
        self.assertNotIn('resource_id',result['meters'][0])
        self.assertNotIn('password',json.dumps(result));self.assertNotIn('username',json.dumps(result))
    def test_origin_csrf_and_dns_rebinding_guards(self):
        self.assertEqual(self.call('/api/sync',{},token=False)[0],403)
        self.assertEqual(self.call('/api/status',origin='https://evil.example')[0],403)
        self.assertEqual(self.call('/api/status',host='evil.example')[0],403)
        self.assertEqual(self.call('/api/sync',{'days':30})[0],202)
    def test_tariff_save_overlap_recalculate_delete(self):
        payload={'meter':'m','label':'Test history','from':'2026-01-01','to':'2026-02-01','rate':'30','standing':'50','windows':[]}
        code,result=self.call('/api/tariffs',payload);self.assertEqual(code,201)
        self.assertEqual(self.call('/api/tariffs',payload)[0],400)
        start,end=date_bounds('2026-01-02')
        with self.store.connection() as db:
            db.executemany('INSERT INTO readings VALUES(?,?,?,?)',[('m',s,'1',0) for s in range(start,end,HALF_HOUR)])
        code,report=self.call('/api/dashboard?meter=m&view=day&date=2026-01-02')
        self.assertEqual(code,200);self.assertEqual(report['summary']['estimated_cost'],14.9)
        self.assertEqual(self.call('/api/tariffs/delete',{'id':result['id']})[0],200)
        self.assertEqual(self.call('/api/dashboard?meter=m&view=day&date=2026-01-02')[1]['summary']['unpriced'],48)
    def test_invalid_requests_rejected(self):
        for body in ({'days':True},{'days':0},{'days':731},{'days':'90'}):
            self.assertEqual(self.call('/api/sync',body)[0],400)
        self.assertEqual(self.call('/api/dashboard?meter=m&date=invalid')[0],400)
        self.assertEqual(self.call('/api/dashboard?meter=m&view=all&date=2026-01-01')[0],400)

if __name__=='__main__':unittest.main()
