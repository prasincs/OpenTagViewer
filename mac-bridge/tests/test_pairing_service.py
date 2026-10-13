import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.error
import urllib.request

spec=importlib.util.spec_from_file_location('bridge_service',Path(__file__).resolve().parents[1]/'bridge.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

def secret(code):
    raw=code.split(':',1)[1]
    return json.loads(base64.urlsafe_b64decode(raw+'='*((-len(raw))%4)))['secret']

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.connection=b.create_pairing(self.directory,'127.0.0.1',8766)
        self.token=b.start_pairing_flow(self.directory,now=1000)
    def tearDown(self):self.tmp.cleanup()
    def state(self,now=1000,cancel=False):return b.pairing_flow_status(self.directory,self.token,now=now,cancel=cancel)
    def claim(self,now=1000):return b.redeem_invitation(self.directory,secret(self.state(now)['code']),now=now)
    def test_expired_qr_refreshes_while_active_and_previous_code_is_invalid(self):
        first=self.state()['code']
        self.state(1100);self.state(1200);new=self.state(1300)
        self.assertEqual(new['status'],'waiting');self.assertEqual(new['expires_at'],1600)
        self.assertNotEqual(first,new['code'])
        self.assertIsNone(b.redeem_invitation(self.directory,secret(first),now=1300))
        self.assertIsNotNone(b.redeem_invitation(self.directory,secret(new['code']),now=1300))
    def test_claim_is_not_success_until_phone_confirms(self):
        claim=self.claim()
        self.assertEqual(self.state()['status'],'confirming');self.assertIsNone(self.state()['code'])
        self.assertFalse(b.confirm_pairing(self.directory,'x'*43,True,now=1001))
        self.assertTrue(b.confirm_pairing(self.directory,claim['pairing_receipt'],True,now=1001))
        self.assertEqual(self.state(1001)['status'],'paired')
        self.assertIsNone(self.state(1400)['code'])
    def test_phone_failure_is_terminal_and_never_renews(self):
        claim=self.claim()
        self.assertTrue(b.confirm_pairing(self.directory,claim['pairing_receipt'],False,now=1001))
        self.assertEqual(self.state(1001)['status'],'failed');self.assertIsNone(self.state(2000)['code'])
    def test_cancel_revokes_pending_qr(self):
        old=self.state()['code'];self.assertEqual(self.state(cancel=True)['status'],'cancelled')
        self.assertIsNone(b.redeem_invitation(self.directory,secret(old),now=1001))
        self.assertIsNone(self.state(2000)['code'])
    def test_cancellation_during_confirmation_rejects_phone_ack(self):
        claim=self.claim();self.state(cancel=True)
        self.assertFalse(b.confirm_pairing(self.directory,claim['pairing_receipt'],True,now=1001))
    def test_closed_window_does_not_reauthorize_access(self):
        old=self.state()['code'];state=self.state(1121)
        self.assertEqual(state['status'],'cancelled');self.assertEqual(state['reason'],'window_closed')
        self.assertIsNone(b.redeem_invitation(self.directory,secret(old),now=1121))
    def test_unconfirmed_claim_times_out_without_new_invitation(self):
        self.claim();self.state(1100)
        state=self.state(1120);self.assertEqual(state['status'],'failed')
        self.assertEqual(state['reason'],'confirmation_timeout');self.assertIsNone(state['code'])
    def test_untrusted_ui_token_cannot_cancel_or_read(self):
        self.assertIsNone(b.pairing_flow_status(self.directory,'x'*43,now=1000,cancel=True))
        self.assertEqual(self.state()['status'],'waiting')
    def test_new_window_invalidates_old_controller_and_receipt(self):
        claim=self.claim();new=b.start_pairing_flow(self.directory,now=1001)
        self.assertIsNone(self.state(1001))
        self.assertFalse(b.confirm_pairing(self.directory,claim['pairing_receipt'],True,now=1001))
        self.assertEqual(b.pairing_flow_status(self.directory,new,now=1001)['status'],'waiting')
    def test_state_is_private_and_has_no_permanent_location_token(self):
        path=self.directory/'pairing-flow.json'
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertNotIn(self.connection['token'],path.read_text())

class LocalUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.connection=b.create_pairing(self.directory,'127.0.0.1',8766)
        self.snapshot=b.Snapshot(None)
        self.server=b.LocalPairingServer(self.directory,self.snapshot)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.token=b.start_pairing_flow(self.directory)
        b.atomic_private(self.directory/'pairing-ui.json',json.dumps({'port':self.server.server_port}).encode())
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path='/v1/pairing-status',token=None,method='GET',origin=None,host=None):
        req=urllib.request.Request(self.server.origin+path,method=method)
        if token:req.add_header('Authorization','Setup '+token)
        if origin:req.add_header('Origin',origin)
        if host:req.add_header('Host',host)
        return urllib.request.urlopen(req,timeout=5)
    def test_status_has_live_qr_and_apple_readiness_but_no_account_or_token(self):
        with self.request(token=self.token) as response:
            raw=response.read().decode();state=json.loads(raw)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        self.assertEqual(state['status'],'waiting');self.assertEqual(state['apple_status'],'setup_required')
        self.assertIn('<svg',state['qr_svg']);self.assertNotIn(self.connection['token'],raw)
        self.assertNotIn('ui_token',raw);self.assertNotIn('code',state)
    def test_unauthorized_and_cross_origin_controls_are_rejected(self):
        for token,origin,host,expected in [(None,None,None,401),(self.token,'https://evil.example',None,403),(self.token,None,'evil.example',403)]:
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(token=token,origin=origin,host=host)
            self.assertEqual(e.exception.code,expected)
    def test_cancel_stops_flow(self):
        with self.request('/v1/pairing-cancel',self.token,'POST') as response:state=json.load(response)
        self.assertEqual(state['status'],'cancelled');self.assertIsNone(state['qr_svg'])
    def test_browser_shell_has_no_secrets_or_external_resources(self):
        for path in ['/','/pairing-ui.js','/pairing-ui.css']:
            with self.request(path) as response:raw=response.read().decode()
            self.assertNotIn(self.connection['token'],raw);self.assertNotIn(self.token,raw)
            self.assertNotIn('https://',raw)
    def test_invite_command_opens_service_and_reuses_existing_flow(self):
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0)) as opened:b.show_invitation(self.directory,reuse=True)
        args=opened.call_args.args[0]
        self.assertEqual(args,['open',self.server.origin+'/#'+self.token])

    def test_reopen_after_closed_window_creates_new_authorization(self):
        with b.pairing_lock(self.directory):
            path=self.directory/'pairing-flow.json';flow=json.loads(path.read_text());flow['last_seen']=0
            b.atomic_private(path,json.dumps(flow).encode())
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0)) as opened:b.show_invitation(self.directory,reuse=True)
        new_token=opened.call_args.args[0][1].split('#')[1]
        self.assertNotEqual(new_token,self.token)
        self.assertIsNone(b.pairing_flow_status(self.directory,self.token))
        self.assertEqual(b.pairing_flow_status(self.directory,new_token)['status'],'waiting')

    def test_apple_signin_failure_is_negative_and_cancels_pending_qr(self):
        b.record_apple_setup(self.directory,'failed')
        with self.request(token=self.token) as response:state=json.load(response)
        self.assertEqual(state['apple_status'],'setup_failed');self.assertEqual(state['status'],'cancelled')
        self.assertIsNone(state['qr_svg'])
        b.record_apple_setup(self.directory,'running')
        self.assertEqual(b.apple_setup_status(self.directory,self.snapshot),'setup_running')
        b.record_apple_setup(self.directory,'ready')
        self.assertEqual(b.apple_setup_status(self.directory,self.snapshot),'setup_required')
