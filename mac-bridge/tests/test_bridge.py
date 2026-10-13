import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

spec=importlib.util.spec_from_file_location("home_bridge",Path(__file__).resolve().parents[1]/"bridge.py")
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class MemoryKeyStore(bridge.SecureStore):
    def _key(self,create=False):return b"k"*32

class VaultTests(unittest.TestCase):
    def test_secrets_are_encrypted_and_tamper_detection_is_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=MemoryKeyStore(Path(tmp));state={"account":{"account":{"password":None}},"accessories":[{"master_key":"SYNTHETIC_PRIVATE_KEY"}]}
            store.write(state);p=Path(tmp)/"apple-state.enc"
            self.assertNotIn(b"SYNTHETIC_PRIVATE_KEY",p.read_bytes())
            self.assertEqual(store.read(),state);self.assertEqual(p.stat().st_mode&0o777,0o600)
            raw=bytearray(p.read_bytes());raw[-1]^=1;p.write_bytes(raw)
            with self.assertRaises(Exception):store.read()
    def test_original_apple_password_is_never_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=MemoryKeyStore(Path(tmp))
            with self.assertRaises(ValueError):store.write({"account":{"account":{"password":"SYNTHETIC_PASSWORD"}}})
            self.assertFalse((Path(tmp)/"apple-state.enc").exists())
    def test_account_export_removes_password_but_keeps_session(self):
        account=SimpleNamespace(to_json=lambda:{"account":{"password":"SYNTHETIC_PASSWORD"},"login":{"data":{"token":"SESSION"}}})
        result=bridge.session_without_password(account)
        self.assertIsNone(result['account']['password']);self.assertEqual(result['login']['data']['token'],'SESSION')
    def test_public_response_cannot_include_keys_serial_or_raw_report(self):
        accessory=SimpleNamespace(identifier='private-identifier',name='Test tag')
        report=SimpleNamespace(latitude=12.5,longitude=34.5,timestamp=datetime(2026,1,1,tzinfo=timezone.utc),horizontal_accuracy=5)
        result=bridge.public_item(accessory,report)
        self.assertEqual(set(result),{'id','name','location'})
        self.assertEqual(set(result['location']),{'latitude','longitude','timestamp','accuracy'})
        self.assertNotIn('private-identifier',json.dumps(result))
    def test_invalid_coordinates_are_not_published(self):
        a=SimpleNamespace(identifier='test',name='Test')
        r=SimpleNamespace(latitude=float('nan'),longitude=10)
        self.assertIsNone(bridge.public_item(a,r)['location'])
    def test_missing_mac_setup_is_explicit_and_never_fake_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=asyncio.run(bridge.AppleSource(MemoryKeyStore(Path(tmp))).fetch())
            self.assertEqual(result['status'],'setup_required');self.assertEqual(result['items'],[])

class ApiFixture:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();directory=Path(self.tmp.name)
        self.pair=bridge.create_pairing(directory,'127.0.0.1',8766)
        self.snapshot=bridge.Snapshot(None)
        self.snapshot.value={'schema':1,'status':'ready','items':[{'id':'test','name':'Synthetic tag','location':None}],'fetched_at':1}
        self.server=bridge.BridgeServer(('127.0.0.1',0),self.snapshot,hashlib.sha256(self.pair['token'].encode()).hexdigest(),directory)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(directory/'server.pem',directory/'server-key.pem');self.server.tls_context=tls
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.context=ssl.create_default_context(cafile=str(directory/'server.pem'))
        self.url='https://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def get(self,path='/v1/items',token=None,context=None):
        req=urllib.request.Request(self.url+path)
        if token is not None:req.add_header('Authorization','Bearer '+token)
        return urllib.request.urlopen(req,context=context or self.context,timeout=5)
class ApiTests(ApiFixture, unittest.TestCase):
    def test_anonymous_and_wrong_token_requests_are_denied(self):
        for token in [None,'x'*43]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.get(token=token)
            self.assertEqual(error.exception.code,401)
    def test_authenticated_api_is_minimal_and_uncacheable(self):
        with self.get(token=self.pair['token']) as response:
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(json.load(response),self.snapshot.value)
    def test_no_key_or_account_export_endpoints_exist(self):
        for path in ['/v1/keys','/v1/account','/v1/items?token='+self.pair['token']]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.get(path,self.pair['token'])
            self.assertEqual(error.exception.code,404)
    def test_write_requests_are_rejected(self):
        req=urllib.request.Request(self.url+'/v1/items',data=b'{}',method='POST')
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req,context=self.context)
        self.assertEqual(error.exception.code,405)
    def test_untrusted_certificate_is_rejected(self):
        with self.assertRaises(urllib.error.URLError):self.get(token=self.pair['token'],context=ssl.create_default_context())
    def test_paired_certificate_hash_is_exact(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert=x509.load_pem_x509_certificate((Path(self.tmp.name)/'server.pem').read_bytes())
        self.assertEqual(cert.fingerprint(hashes.SHA256()).hex(),self.pair['certificate_sha256'])
    def test_authenticated_polling_is_rate_limited(self):
        self.get(token=self.pair['token']).close()
        with self.assertRaises(urllib.error.HTTPError) as error:self.get(token=self.pair['token'])
        self.assertEqual(error.exception.code,429)

class PairingTests(ApiFixture, unittest.TestCase):
    def invitation(self, now=None):
        code=bridge.make_invitation(Path(self.tmp.name),now=now)
        payload=code.split(":",1)[1]
        return json.loads(base64.urlsafe_b64decode(payload+"="*((-len(payload))%4)))
    def post(self, secret, path="/v1/pair", data=None):
        req=urllib.request.Request(self.url+path,method="POST",data=data)
        req.add_header("Authorization","Pairing "+secret)
        return urllib.request.urlopen(req,context=self.context,timeout=5)
    def test_qr_is_temporary_and_never_contains_long_term_token(self):
        invite=self.invitation()
        self.assertNotEqual(invite['secret'],self.pair['token'])
        self.assertNotIn(self.pair['token'],json.dumps(invite))
        self.assertEqual(invite['certificate_sha256'],self.pair['certificate_sha256'])
        stored=(Path(self.tmp.name)/'pairing-invitation.json').read_text()
        self.assertNotIn(invite['secret'],stored)
    def test_authenticated_pairing_is_single_use_and_uncacheable(self):
        invite=self.invitation()
        with self.post(invite['secret']) as response:
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(json.load(response),self.pair)
        with self.assertRaises(urllib.error.HTTPError) as err:self.post(invite['secret'])
        self.assertEqual(err.exception.code,401)
    def test_wrong_secret_does_not_consume_invitation(self):
        invite=self.invitation()
        with self.assertRaises(urllib.error.HTTPError):self.post('x'*43)
        with self.post(invite['secret']) as response:self.assertEqual(json.load(response),self.pair)
    def test_expired_invitation_cannot_be_redeemed(self):
        invite=self.invitation(now=1)
        with self.assertRaises(urllib.error.HTTPError) as err:self.post(invite['secret'])
        self.assertEqual(err.exception.code,401)
    def test_new_invitation_invalidates_previous_one(self):
        old=self.invitation();new=self.invitation()
        with self.assertRaises(urllib.error.HTTPError):self.post(old['secret'])
        self.post(new['secret']).close()
    def test_simultaneous_claims_release_only_one_token(self):
        invite=self.invitation()
        def redeem(_):return bridge.redeem_invitation(Path(self.tmp.name),invite['secret'])
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(redeem,range(4)))
        self.assertEqual(sum(result is not None for result in results),1)
    def test_pairing_rejects_payload_and_query_credentials(self):
        invite=self.invitation()
        for path,data,expected in [('/v1/pair',b'{}',400),('/v1/pair?secret='+invite['secret'],None,405)]:
            with self.assertRaises(urllib.error.HTTPError) as err:self.post(invite['secret'],path,data)
            self.assertEqual(err.exception.code,expected)
        self.post(invite['secret']).close()
    def test_pairing_is_rate_limited_without_consuming_invitation(self):
        invite=self.invitation()
        for _ in range(5):
            with self.assertRaises(urllib.error.HTTPError) as err:self.post('x'*43)
            self.assertEqual(err.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as err:self.post(invite['secret'])
        self.assertEqual(err.exception.code,429)
        self.assertIsNotNone(bridge.redeem_invitation(Path(self.tmp.name),invite['secret']))


class ConfirmationTests(ApiFixture, unittest.TestCase):
    def claim_flow(self):
        self.ui_token=bridge.start_pairing_flow(Path(self.tmp.name))
        code=bridge.pairing_flow_status(Path(self.tmp.name),self.ui_token)['code'].split(':',1)[1]
        invite=json.loads(base64.urlsafe_b64decode(code+'='*((-len(code))%4)))
        req=urllib.request.Request(self.url+'/v1/pair',method='POST',headers={'Authorization':'Pairing '+invite['secret']})
        with urllib.request.urlopen(req,context=self.context) as response:return json.load(response)
    def confirm(self,receipt,result='connected',data=None):
        req=urllib.request.Request(self.url+'/v1/pair/confirm',method='POST',data=data,
            headers={'Authorization':'Receipt '+receipt,'X-Pairing-Result':result})
        return urllib.request.urlopen(req,context=self.context)
    def test_positive_and_negative_receipts_drive_terminal_results(self):
        for result,status in [('connected','paired'),('failed','failed')]:
            claim=self.claim_flow()
            self.assertIn('pairing_receipt',claim)
            self.assertEqual(bridge.pairing_flow_status(Path(self.tmp.name),self.ui_token)['status'],'confirming')
            self.confirm(claim['pairing_receipt'],result).close()
            self.assertEqual(bridge.pairing_flow_status(Path(self.tmp.name),self.ui_token)['status'],status)
    def test_wrong_receipt_cannot_confirm_and_payload_is_rejected(self):
        claim=self.claim_flow()
        for receipt,result,data,expected in [('x'*43,'connected',None,401),(claim['pairing_receipt'],'unknown',None,401),(claim['pairing_receipt'],'connected',b'{}',400)]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.confirm(receipt,result,data)
            self.assertEqual(error.exception.code,expected)
        self.assertEqual(bridge.pairing_flow_status(Path(self.tmp.name),self.ui_token)['status'],'confirming')
    def test_cancelled_flow_rejects_late_confirmation(self):
        claim=self.claim_flow();bridge.pairing_flow_status(Path(self.tmp.name),self.ui_token,cancel=True)
        with self.assertRaises(urllib.error.HTTPError) as error:self.confirm(claim['pairing_receipt'])
        self.assertEqual(error.exception.code,401)

if __name__=='__main__':unittest.main()
