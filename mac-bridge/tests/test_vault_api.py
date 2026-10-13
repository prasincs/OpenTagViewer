import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import ssl
import hashlib
import urllib.request
import urllib.error
import sys
sys.path.insert(0,str(Path(__file__).parent))
from test_vault import SoftwareMac,private,public,unwrap
from test_bridge import bridge
from vault import VaultStore,b64
import unittest

class GateApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.connection=bridge.create_pairing(self.path,'127.0.0.1',8766)
        self.store=VaultStore(self.path,False,SoftwareMac())
        self.snapshot=bridge.Snapshot(bridge.AppleSource(self.store))
        self.server=bridge.BridgeServer(('127.0.0.1',0),self.snapshot,hashlib.sha256(self.connection['token'].encode()).hexdigest(),self.path)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(self.path/'server.pem',self.path/'server-key.pem');self.server.tls_context=tls
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.local=bridge.LocalPairingServer(self.path,self.snapshot)
        self.local_thread=threading.Thread(target=self.local.serve_forever,daemon=True);self.local_thread.start()
        self.capability=bridge.start_pairing_flow(self.path)
        self.context=ssl.create_default_context(cafile=str(self.path/'server.pem'))
        self.android=private();self.der=public(self.android)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.local.shutdown();self.local.server_close();self.local_thread.join();self.store.lock();self.tmp.cleanup()
    def phone(self,path,body=None,authorized=True):
        req=urllib.request.Request('https://127.0.0.1:'+str(self.server.server_port)+path,
             data=json.dumps(body).encode() if body is not None else None)
        if authorized:req.add_header('Authorization','Bearer '+self.connection['token'])
        with urllib.request.urlopen(req,context=self.context,timeout=5) as response:return json.load(response)
    def control(self,path='/v1/pairing-status',fingerprint=None):
        req=urllib.request.Request(self.local.origin+path,method='POST' if fingerprint else 'GET',
             headers={'Authorization':'Setup '+self.capability})
        if fingerprint:req.add_header('X-Vault-Fingerprint',fingerprint)
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
    def enroll(self):
        self.phone('/v1/vault/enroll',{'public_key':b64(self.der),'security_level':'tee'})
        state=self.control();self.control('/v1/vault/approve',state['vault']['fingerprint'])
    def test_remote_token_cannot_approve_recipient_and_local_fingerprint_is_required(self):
        result=self.phone('/v1/vault/enroll',{'public_key':b64(self.der),'security_level':'tee'})
        self.assertEqual(result['status'],'approval_needed');self.assertIsNone(self.store.metadata())
        with self.assertRaises(urllib.error.HTTPError):self.phone('/v1/vault/approve',{})
        with self.assertRaises(urllib.error.HTTPError):self.control('/v1/vault/approve','x'*64)
        self.assertIsNone(self.store.metadata());self.enroll();self.assertEqual(self.store.status(),'locked')
    def test_wrong_device_and_anonymous_requests_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):self.phone('/v1/vault/enroll',{'public_key':b64(self.der),'security_level':'software'})
        self.enroll()
        with self.assertRaises(urllib.error.HTTPError):self.phone('/v1/vault/challenge',authorized=False)
        with self.assertRaises(urllib.error.HTTPError):self.phone('/v1/vault/enroll',{'public_key':b64(public(private())),'security_level':'tee'})
    def test_approved_android_unlock_is_single_use_and_explicit_lock_hides_cache(self):
        self.enroll();challenge=self.phone('/v1/vault/challenge')
        key=unwrap(self.android,challenge['wrapped_key'])
        request={'request_id':challenge['request_id'],'key':b64(key)}
        self.assertEqual(self.phone('/v1/vault/unlock',request)['status'],'unlocked')
        with self.assertRaises(urllib.error.HTTPError):self.phone('/v1/vault/unlock',request)
        self.snapshot.value={'schema':1,'status':'ready','items':[{'id':'synthetic','name':'fixture','location':None}],'fetched_at':1}
        self.phone('/v1/vault/lock',{})
        state=self.phone('/v1/items');self.assertEqual(state['status'],'vault_locked');self.assertEqual(state['items'],[])
        self.assertNotIn('key',self.control()['vault'])
