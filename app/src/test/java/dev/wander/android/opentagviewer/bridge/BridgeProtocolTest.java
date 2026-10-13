package dev.wander.android.opentagviewer.bridge;

import org.junit.Test;
import static org.junit.Assert.*;

public class BridgeProtocolTest {
    private static final String VALID="{\"version\":1,\"endpoint\":\"https://127.0.0.1:8766\",\"certificate_sha256\":\""+"a".repeat(64)+"\",\"token\":\""+"b".repeat(43)+"\"}";
    @Test public void connectionFileRoundTrips() throws Exception {
        BridgeConnection c=BridgeConnection.parse(VALID);
        assertEquals(c.endpoint,BridgeConnection.parse(c.encode()).endpoint);
        assertEquals(c.token,BridgeConnection.parse(c.encode()).token);
    }
    @Test public void rejectsHttpUserInfoQueriesAndFragments() throws Exception {
        for(String url:new String[]{"http://127.0.0.1:8766","https://user:pass@127.0.0.1","https://127.0.0.1?token=secret","https://127.0.0.1/#x","https://127.0.0.1/path"}) {
            try{BridgeConnection.parse(VALID.replace("https://127.0.0.1:8766",url));fail(url);}catch(IllegalArgumentException expected){}
        }
    }
    @Test public void rejectsInvalidPinsTokensAndOversizedFiles() throws Exception {
        for(String value:new String[]{VALID.replace("a".repeat(64),"a".repeat(63)),VALID.replace("b".repeat(43),"bad")," ".repeat(8193)}) {
            try{BridgeConnection.parse(value);fail();}catch(IllegalArgumentException expected){}
        }
    }
    @Test public void acceptsMissingLocationsAndExplicitSetupStatus() throws Exception {
        assertEquals("setup_required",BridgeClient.parseSnapshot("{\"schema\":1,\"status\":\"setup_required\",\"items\":[]}").status);
        BridgeClient.Snapshot result=BridgeClient.parseSnapshot("{\"schema\":1,\"status\":\"ready\",\"items\":[{\"id\":\"test\",\"name\":\"Bag\",\"location\":null}]}");
        assertNull(result.items.get(0).latitude);
    }
    @Test public void rejectsCoordinatesOutsideEarthAndUnsupportedSchemas() throws Exception {
        for(String raw:new String[]{"{\"schema\":2,\"status\":\"ready\",\"items\":[]}","{\"schema\":1,\"status\":\"ready\",\"items\":[{\"id\":\"x\",\"name\":\"Bag\",\"location\":{\"latitude\":100,\"longitude\":0,\"timestamp\":1}}]}"}) {
            try{BridgeClient.parseSnapshot(raw);fail();}catch(BridgeClient.BridgeException expected){assertEquals(BridgeClient.Failure.PROTOCOL,expected.failure);}
        }
    }
    @org.junit.Test public void lockedVaultStatusCanNeverCarryLocationRows() throws Exception {
        BridgeClient.Snapshot snapshot=BridgeClient.parseSnapshot("{\"schema\":1,\"status\":\"vault_locked\",\"items\":[]}");
        org.junit.Assert.assertEquals("vault_locked",snapshot.status);org.junit.Assert.assertTrue(snapshot.items.isEmpty());
        try{BridgeClient.parseSnapshot("{\"schema\":1,\"status\":\"vault_locked\",\"items\":[{\"id\":\"x\",\"name\":\"unexpected\",\"location\":null}]}");org.junit.Assert.fail();}
        catch(BridgeClient.BridgeException expected){org.junit.Assert.assertEquals(BridgeClient.Failure.PROTOCOL,expected.failure);}
    }

}
