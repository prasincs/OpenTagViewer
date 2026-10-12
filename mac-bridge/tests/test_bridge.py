import asyncio
import hashlib
import importlib.util
import json
import os
import ssl
import tempfile
import threading
import unittest
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

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();directory=Path(self.tmp.name)
        self.pair=bridge.create_pairing(directory,'127.0.0.1',8766)
        self.snapshot=bridge.Snapshot(None)
        self.snapshot.value={'schema':1,'status':'ready','items':[{'id':'test','name':'Synthetic tag','location':None}],'fetched_at':1}
        self.server=bridge.BridgeServer(('127.0.0.1',0),self.snapshot,hashlib.sha256(self.pair['token'].encode()).hexdigest())
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

if __name__=='__main__':unittest.main()
