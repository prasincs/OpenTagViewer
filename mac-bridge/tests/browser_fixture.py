"""Synthetic local UI fixture; communicates only with its test parent via stdio."""
import importlib.util
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import sys
sys.path.insert(0,str(Path(__file__).parent))
from test_vault import SoftwareMac,private,public
from vault import VaultStore,b64

spec=importlib.util.spec_from_file_location('test_bridge',Path(__file__).resolve().parents[1]/'bridge.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
with tempfile.TemporaryDirectory() as tmp:
    directory=Path(tmp)
    b.create_pairing(directory,'127.0.0.1',8766)
    snapshot=b.Snapshot(None)
    server=b.LocalPairingServer(directory,snapshot)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    token=b.start_pairing_flow(directory)
    print(json.dumps({'origin':server.origin,'token':token}),flush=True)
    receipt=None
    try:
        for line in sys.stdin:
            command=json.loads(line)['command']
            if command=='quit':break
            result={}
            if command=='new':
                (directory/'apple-setup-status.json').unlink(missing_ok=True)
                token=b.start_pairing_flow(directory);receipt=None
                result={'token':token}
            elif command=='expire':
                with b.pairing_lock(directory):
                    path=directory/'pairing-flow.json';flow=json.loads(path.read_text())
                    flow['expires_at']=0;flow['last_seen']=time.time()
                    b.atomic_private(path,json.dumps(flow).encode())
            elif command=='claim':
                flow=b.pairing_flow_status(directory,token)
                raw=flow['code'].split(':',1)[1]
                secret=json.loads(base64.urlsafe_b64decode(raw+'='*((-len(raw))%4)))['secret']
                receipt=b.redeem_invitation(directory,secret)['pairing_receipt']
            elif command in ('positive','negative'):
                assert b.confirm_pairing(directory,receipt,command=='positive')
            elif command=='apple-ready':snapshot.value['status']='ready'
            elif command=='apple-failed':b.record_apple_setup(directory,'failed')
            elif command=='vault-pending':
                (directory/'apple-setup-status.json').unlink(missing_ok=True)
                snapshot.source=b.AppleSource(VaultStore(directory,False,SoftwareMac()))
                android=private();raw=public(android)
                import hashlib
                b.atomic_private(directory/'pending-android-key.json',json.dumps({'public_key':b64(raw),'fingerprint':hashlib.sha256(raw).hexdigest(),'reported_security_level':'tee'}).encode())
            else:raise ValueError('Unknown fixture command')
            print(json.dumps(result),flush=True)
    finally:server.shutdown();server.server_close();thread.join()
