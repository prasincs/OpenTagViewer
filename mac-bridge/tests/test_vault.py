import base64
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vault import VaultStore,VaultLocked,LegacyVault,LocalVaultLease,SecureEnclave,PREFIX,TTL,b64,unb64
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec,rsa,padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

class SoftwareMac:
    """Synthetic test-only stand-in. Production never substitutes software for SE."""
    def __init__(self):self.key=ec.generate_private_key(ec.SECP256R1())
    def public(self):return self.key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    def seal(self,raw,aad):
        peer=ec.generate_private_key(ec.SECP256R1());shared=peer.exchange(ec.ECDH(),self.key.public_key())
        key=HKDF(algorithm=hashes.SHA256(),length=32,salt=b'',info=aad).derive(shared)
        nonce=b'n'*12
        return {'ephemeral_public':b64(peer.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)),
                'sealed':b64(nonce+AESGCM(key).encrypt(nonce,raw,aad))}
    def open(self,value,aad):
        peer=ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),unb64(value['ephemeral_public']))
        shared=self.key.exchange(ec.ECDH(),peer)
        key=HKDF(algorithm=hashes.SHA256(),length=32,salt=b'',info=aad).derive(shared)
        raw=unb64(value['sealed']);return AESGCM(key).decrypt(raw[:12],raw[12:],aad)

def private():return rsa.generate_private_key(public_exponent=65537,key_size=2048)
def public(key):return key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
def unwrap(android,wrapped):return android.decrypt(unb64(wrapped),padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),algorithm=hashes.SHA256(),label=None))

class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.android=private();self.mac=SoftwareMac();self.store=VaultStore(self.path,False,self.mac)
        self.store.enable(public(self.android))
    def tearDown(self):self.store.lock();self.tmp.cleanup()
    def approve(self):
        request=self.store.challenge();key=unwrap(self.android,request['wrapped_key'])
        self.store.unlock(request['request_id'],key);return key
    def test_mac_only_files_are_insufficient_and_restart_starts_locked(self):
        key=self.approve();self.store.write({'account':{'account':{'password':None}},'keys':'SYNTHETIC_TAG_KEY'})
        self.assertNotIn(b'SYNTHETIC_TAG_KEY',(self.path/'apple-state.enc').read_bytes())
        self.assertNotIn(b64(key),(self.path/'vault.json').read_text())
        restarted=VaultStore(self.path,False,self.mac)
        with self.assertRaises(VaultLocked):restarted.read()
        self.assertFalse(restarted.unlocked())
    def test_both_devices_are_required_and_wrong_android_cannot_unwrap(self):
        request=self.store.challenge()
        with self.assertRaises(ValueError):unwrap(private(),request['wrapped_key'])
        wrong=VaultStore(self.path,False,SoftwareMac())
        with self.assertRaises(Exception):wrong.challenge()
    def test_correct_android_unlock_roundtrip_and_lock(self):
        self.approve();value={'account':{'account':{'password':None}},'selected':['synthetic']}
        self.store.write(value);self.assertEqual(self.store.read(),value)
        self.store.lock()
        with self.assertRaises(VaultLocked):self.store.read()
    def test_challenge_is_single_use_and_old_challenge_is_replaced(self):
        first=self.store.challenge();second=self.store.challenge()
        key=unwrap(self.android,second['wrapped_key'])
        with self.assertRaises(VaultLocked):self.store.unlock(first['request_id'],key)
        self.store.unlock(second['request_id'],key)
        with self.assertRaises(VaultLocked):self.store.unlock(second['request_id'],key)
    def test_expired_challenge_and_wrong_key_do_not_unlock(self):
        request=self.store.challenge();key=unwrap(self.android,request['wrapped_key'])
        with patch('vault.time.monotonic',return_value=time.monotonic()+61):
            with self.assertRaises(VaultLocked):self.store.unlock(request['request_id'],key)
        request=self.store.challenge()
        with self.assertRaises(VaultLocked):self.store.unlock(request['request_id'],b'x'*32)
        self.assertFalse(self.store.unlocked())
    def test_timeout_wipes_retained_key_and_relocks(self):
        self.approve();reference=self.store.key
        with patch('vault.time.monotonic',return_value=self.store.deadline+1):self.assertFalse(self.store.unlocked())
        self.assertEqual(bytes(reference),b'\0'*32);self.assertIsNone(self.store.key)
    def test_auto_timer_locks_without_requests(self):
        with patch('vault.TTL',0.05):self.approve();time.sleep(.15)
        self.assertIsNone(self.store.key)
    def test_tampering_and_password_persistence_are_rejected(self):
        self.approve()
        with self.assertRaises(ValueError):self.store.write({'account':{'account':{'password':'SYNTHETIC'}}})
        self.store.write({'account':{'account':{'password':None}}})
        path=self.path/'apple-state.enc';raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
        with self.assertRaises(Exception):self.store.read()
    def test_no_legacy_fallback_or_recipient_replacement(self):
        with self.assertRaises(ValueError):self.store.enable(public(private()))
        (self.path/'apple-state.enc').write_bytes(b'legacy ciphertext')
        with self.assertRaises(LegacyVault):self.store.read()
    def test_owner_only_local_setup_borrows_only_approved_memory_lease(self):
        lease=LocalVaultLease(self.store)
        try:
            consumer=VaultStore(self.path,True,self.mac)
            with self.assertRaises(VaultLocked):consumer._key()
            self.approve();self.store.write({'account':{'account':{'password':None}},'test':'fixture'})
            self.assertEqual(consumer.read()['test'],'fixture')
            self.assertEqual((self.path/'vault.sock').stat().st_mode&0o777,0o600)
            self.store.lock()
            with self.assertRaises(VaultLocked):consumer.read()
            consumer.lock()
        finally:lease.close()
        self.assertFalse(self.store.unlocked())
    def test_crypto_metadata_is_private_and_contains_no_clear_key(self):
        self.assertEqual((self.path/'vault.json').stat().st_mode&0o777,0o600)
        self.assertEqual(set(self.store.challenge()),{'version','request_id','wrapped_key','android_fingerprint','expires_at'})

class NativeSecureEnclaveTests(unittest.TestCase):
    def test_actual_secure_enclave_outer_wrap_and_android_inner_unwrap(self):
        if sys.platform!='darwin':self.skipTest('macOS hardware test')
        with tempfile.TemporaryDirectory() as tmp:
            key=private();store=VaultStore(Path(tmp),False)
            try:
                store.enable(public(key));challenge=store.challenge()
                secret=unwrap(key,challenge['wrapped_key']);store.unlock(challenge['request_id'],secret)
                store.write({'account':{'account':{'password':None}},'keys':'SYNTHETIC_ONLY'})
                self.assertEqual(store.read()['keys'],'SYNTHETIC_ONLY')
                self.assertEqual((Path(tmp)/'vault-se-keyref').stat().st_mode&0o777,0o600)
                store.lock()
                with self.assertRaises(VaultLocked):store.read()
            finally:store.lock()
