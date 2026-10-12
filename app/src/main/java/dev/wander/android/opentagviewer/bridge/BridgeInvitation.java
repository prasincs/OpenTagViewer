package dev.wander.android.opentagviewer.bridge;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import com.fasterxml.jackson.core.Base64Variants;
import java.util.LinkedHashMap;
import java.util.Map;

/** Out-of-band trust from a Mac-displayed QR; never accept network-provided pins. */
public final class BridgeInvitation {
    public final BridgeConnection transport;
    public final long expiresAt;
    private BridgeInvitation(BridgeConnection transport,long expiresAt){this.transport=transport;this.expiresAt=expiresAt;}
    public static BridgeInvitation parse(String code,long nowSeconds) throws Exception {
        if(code==null||code.length()>2048||!code.startsWith("homefind-pair:"))throw new IllegalArgumentException();
        String payload=code.substring("homefind-pair:".length());
        if(!payload.matches("[A-Za-z0-9_-]+"))throw new IllegalArgumentException();
        JsonNode root=new ObjectMapper().readTree(new String(Base64Variants.MODIFIED_FOR_URL.decode(payload),StandardCharsets.UTF_8));
        if(root==null||root.path("pairing_version").asInt()!=1||!root.path("expires_at").isIntegralNumber())throw new IllegalArgumentException();
        long expiry=root.path("expires_at").asLong();
        if(expiry<=nowSeconds||expiry-nowSeconds>360)throw new IllegalArgumentException();
        Map<String,Object> connection=new LinkedHashMap<>();
        connection.put("version",1);connection.put("endpoint",root.path("endpoint").asText());
        connection.put("certificate_sha256",root.path("certificate_sha256").asText());
        connection.put("token",root.path("secret").asText());
        return new BridgeInvitation(BridgeConnection.parse(new ObjectMapper().writeValueAsString(connection)),expiry);
    }
}
