package dev.wander.android.opentagviewer.bridge;

import java.util.Base64;
import java.nio.charset.StandardCharsets;
import org.junit.Test;
import static org.junit.Assert.*;

public class BridgeInvitationTest {
    private String code(long expiry,String endpoint){
        String json="{\"pairing_version\":1,\"endpoint\":\""+endpoint+"\",\"certificate_sha256\":\""+"a".repeat(64)+"\",\"secret\":\""+"s".repeat(43)+"\",\"expires_at\":"+expiry+"}";
        return "homefind-pair:"+Base64.getUrlEncoder().withoutPadding().encodeToString(json.getBytes(StandardCharsets.UTF_8));
    }
    @Test public void invitationSuppliesEndpointPinAndEphemeralSecret() throws Exception{
        BridgeInvitation i=BridgeInvitation.parse(code(1300,"https://127.0.0.1:8766"),1000);
        assertEquals("https://127.0.0.1:8766",i.transport.endpoint);
        assertEquals("a".repeat(64),i.transport.certificateSha256);assertEquals(1300,i.expiresAt);
    }
    @Test public void expiredOrUnboundedLifetimeIsRejected(){
        for(long expiry:new long[]{999,1000,1361,Long.MAX_VALUE}){
            try{BridgeInvitation.parse(code(expiry,"https://127.0.0.1"),1000);fail();}catch(Exception expected){}
        }
    }
    @Test public void untrustedFormatsAndEndpointsAreRejected(){
        for(String code:new String[]{"garbage","homefind-pair:%%%","x".repeat(2049),code(1300,"http://127.0.0.1"),code(1300,"https://user@127.0.0.1"),code(1300,"https://127.0.0.1?token=secret")}){
            try{BridgeInvitation.parse(code,1000);fail();}catch(Exception expected){}
        }
    }
    @Test public void responseCannotReplaceScannedEndpointOrPin() throws Exception{
        BridgeInvitation i=BridgeInvitation.parse(code(1300,"https://127.0.0.1:8766"),1000);
        String response=i.transport.encode().replace("s".repeat(43),"t".repeat(43));
        assertEquals("t".repeat(43),BridgeClient.parsePairingResponse(i,response).token);
        for(String changed:new String[]{response.replace("127.0.0.1","127.0.0.2"),response.replace("a".repeat(64),"b".repeat(64))}){
            try{BridgeClient.parsePairingResponse(i,changed);fail();}catch(BridgeClient.BridgeException e){assertEquals(BridgeClient.Failure.PROTOCOL,e.failure);}
        }
    }
    @Test public void confirmationReceiptIsValidatedAndNeverPersisted() throws Exception{
        BridgeInvitation invite=BridgeInvitation.parse(code(1300,"https://127.0.0.1:8766"),1000);
        String response=invite.transport.encode().replace("}",",\"pairing_receipt\":\""+"r".repeat(43)+"\"}");
        BridgeConnection c=BridgeClient.parsePairingResponse(invite,response);
        assertEquals("r".repeat(43),c.pairingReceipt);
        assertFalse(c.encode().contains("pairing_receipt"));
        try{BridgeConnection.parse(response.replace("r".repeat(43),"bad"));fail();}catch(Exception expected){}
    }

}
