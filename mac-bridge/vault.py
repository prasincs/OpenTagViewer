"""Two-device vault: Mac Secure Enclave outer wrap, Android RSA-OAEP inner wrap.

No persisted Mac-only data key. A 30-minute memory lease requires Android unwrap.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import threading
import time
import ctypes
import sys
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX=b'HFV2\0'
TTL=1800
MAX_STATE=8*1024*1024
class VaultLocked(Exception):pass
class LegacyVault(Exception):pass

def b64(raw):return base64.b64encode(raw).decode()
def unb64(raw):return base64.b64decode(raw,validate=True)
def private_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(path.parent,0o700)
    temporary=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
    temporary.replace(path);path.chmod(0o600)

def android_public(raw):
    if len(raw)>1024:raise ValueError('Invalid public key')
    key=serialization.load_der_public_key(raw)
    if not isinstance(key,rsa.RSAPublicKey) or key.key_size!=2048 or key.public_numbers().e!=65537:
        raise ValueError('Hardware RSA-2048 public key required')
    return key

class SecureEnclave:
    def __init__(self,directory):
        self.reference=directory/'vault-se-keyref'
        self.binary=Path(__file__).resolve().parents[1]/'.toolchain/vault-helper'
    def call(self,command,value=None):
        result=subprocess.run([str(self.binary),command,str(self.reference)],
            input=json.dumps(value).encode() if value is not None else b'',capture_output=True,timeout=15)
        if result.returncode!=0:raise RuntimeError('Secure Enclave operation unavailable')
        return result.stdout
    def public(self):return unb64(self.call('public' if self.reference.exists() else 'init').decode().strip())
    def seal(self,raw,aad):return json.loads(self.call('seal',{'plaintext':b64(raw),'aad':b64(aad)}))
    def open(self,value,aad):return unb64(json.loads(self.call('open',dict(value,aad=b64(aad))))['plaintext'])

class VaultStore:
    def __init__(self,directory:Path,allow_borrow=True,hardware=None):
        self.directory=directory;self.allow_borrow=allow_borrow
        self.hardware=hardware or SecureEnclave(directory)
        self.mutex=threading.RLock();self.key=None;self.deadline=0;self.challenges={};self.timer=None
    @property
    def metadata_path(self):return self.directory/'vault.json'
    def metadata(self):
        if not self.metadata_path.exists():return None
        if self.metadata_path.stat().st_size>8192:raise ValueError('Invalid vault')
        value=json.loads(self.metadata_path.read_text())
        if value['version']!=2:raise ValueError('Invalid vault')
        return value
    def context(self,meta):
        return ('HomeFindVault/v2|'+meta['vault_id']+'|'+meta['android_fingerprint']+'|'+meta['mac_fingerprint']).encode()
    def enable(self,public_der):
        android=android_public(public_der)
        with self.mutex:
            if self.metadata_path.exists():raise ValueError('Vault already enrolled')
            if (self.directory/'apple-state.enc').exists():raise LegacyVault('Legacy state must be migrated explicitly')
            self.directory.mkdir(parents=True,exist_ok=True,mode=0o700);self.directory.chmod(0o700)
            mac=self.hardware.public()
            meta={'version':2,'vault_id':secrets.token_hex(16),'android_public':b64(public_der),
                  'android_fingerprint':hashlib.sha256(public_der).hexdigest(),
                  'mac_fingerprint':hashlib.sha256(mac).hexdigest()}
            aad=self.context(meta);key=bytearray(secrets.token_bytes(32))
            try:
                inner=android.encrypt(bytes(key),padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),algorithm=hashes.SHA256(),label=None))
                meta['wrapped']=self.hardware.seal(inner,aad)
                meta['key_check']=hmac.new(bytes(key),aad,hashlib.sha256).hexdigest()
                private_write(self.metadata_path,json.dumps(meta).encode())
            finally:
                for i in range(len(key)):key[i]=0
            self.lock()
    def lock(self):
        with self.mutex:
            if self.key is not None:
                for i in range(len(self.key)):self.key[i]=0
            if self.timer is not None:self.timer.cancel()
            self.timer=None;self.key=None;self.deadline=0;self.challenges.clear()
    def unlocked(self):
        with self.mutex:
            if self.key is not None and time.monotonic()>=self.deadline:self.lock()
            return self.key is not None
    def status(self):return 'unenrolled' if self.metadata() is None else ('unlocked' if self.unlocked() else 'locked')
    def challenge(self):
        with self.mutex:
            meta=self.metadata()
            if meta is None:raise VaultLocked('Enroll the Android key first')
            inner=self.hardware.open(meta['wrapped'],self.context(meta))
            request_id=secrets.token_urlsafe(32)
            self.challenges={request_id:time.monotonic()+60}
            return {'version':2,'request_id':request_id,'wrapped_key':b64(inner),
                    'android_fingerprint':meta['android_fingerprint'],'expires_at':int(time.time()+60)}
    def unlock(self,request_id,key):
        with self.mutex:
            deadline=self.challenges.pop(request_id,0)
            if deadline<=time.monotonic() or len(key)!=32:raise VaultLocked('Unlock request expired')
            meta=self.metadata()
            expected=hmac.new(key,self.context(meta),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(meta['key_check'],expected):raise VaultLocked('Wrong Android unwrap')
            self.lock();self.key=bytearray(key);self.deadline=time.monotonic()+TTL
            self.timer=threading.Timer(TTL,self.lock);self.timer.daemon=True;self.timer.start()
    def _key(self):
        if not self.allow_borrow:
            with self.mutex:
                if self.unlocked():return bytes(self.key)
            raise VaultLocked('Approve on Android first')
        # Local setup rechecks the daemon for every operation; no retained local lease key.
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(3);conn.connect(str(self.directory/'vault.sock'))
            conn.sendall(b'borrow\n');raw=b''
            while block:=conn.recv(1024):
                raw+=block
                if len(raw)>4096:raise VaultLocked('Invalid local lease')
        lease=json.loads(raw)
        if lease.get('status')!='unlocked':raise VaultLocked('Approve on Android first')
        if not 0<lease['remaining']<=TTL:raise VaultLocked('Invalid local lease')
        key=unb64(lease['key']);meta=self.metadata()
        if len(key)!=32 or not hmac.compare_digest(meta['key_check'],hmac.new(key,self.context(meta),hashlib.sha256).hexdigest()):
            raise VaultLocked('Invalid lease key')
        return key
    def read(self):
        path=self.directory/'apple-state.enc'
        if not path.exists():return None
        if path.stat().st_size>MAX_STATE:raise ValueError('State too large')
        blob=path.read_bytes()
        if not blob.startswith(PREFIX):raise LegacyVault('Mac-only state is not accepted')
        raw=blob[len(PREFIX):]
        return json.loads(AESGCM(self._key()).decrypt(raw[:12],raw[12:],self.context(self.metadata())))
    def write(self,value):
        if value.get('account',{}).get('account',{}).get('password') is not None:raise ValueError('Password persistence forbidden')
        raw=json.dumps(value,separators=(',',':')).encode()
        if len(raw)>MAX_STATE:raise ValueError('State too large')
        key=self._key();nonce=secrets.token_bytes(12)
        private_write(self.directory/'apple-state.enc',PREFIX+nonce+AESGCM(key).encrypt(nonce,raw,self.context(self.metadata())))

class LocalVaultLease:
    """Owner-only Unix socket: local setup can borrow an already Android-approved RAM lease."""
    def __init__(self,store):
        self.store=store;self.path=store.directory/'vault.sock'
        if self.path.exists():self.path.unlink()
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.server.bind(str(self.path));self.path.chmod(0o600);self.server.listen(4);self.server.settimeout(1)
        self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:conn,_=self.server.accept()
            except socket.timeout:continue
            except OSError:return
            with conn:
                try:
                    conn.settimeout(2)
                    if sys.platform!='darwin':continue
                    uid,gid=ctypes.c_uint(),ctypes.c_uint()
                    peer=ctypes.CDLL(None).getpeereid
                    peer.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
                    peer.restype=ctypes.c_int
                    if peer(conn.fileno(),ctypes.byref(uid),ctypes.byref(gid))!=0 or uid.value!=os.getuid():continue
                    if conn.recv(32)!=b'borrow\n':continue
                    with self.store.mutex:
                        value={'status':'locked'}
                        if self.store.unlocked():value={'status':'unlocked','key':b64(bytes(self.store.key)),'remaining':max(0,self.store.deadline-time.monotonic())}
                    conn.sendall(json.dumps(value).encode())
                except (OSError,ValueError):pass
    def close(self):
        self.stop.set();self.server.close();self.thread.join(timeout=2)
        if self.path.exists():self.path.unlink()
        self.store.lock()
