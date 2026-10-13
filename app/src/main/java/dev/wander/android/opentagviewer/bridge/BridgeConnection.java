package dev.wander.android.opentagviewer.bridge;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

/** A Mac-issued, read-only location credential. Never an Apple credential. */
public final class BridgeConnection {
    public final String endpoint;
    public final String certificateSha256;
    public final String token;
    public final String pairingReceipt;
    private BridgeConnection(String endpoint, String pin, String token, String receipt) {
        this.endpoint = endpoint; this.certificateSha256 = pin; this.token = token; this.pairingReceipt = receipt;
    }
    public static BridgeConnection parse(String value) throws Exception {
        if (value == null || value.getBytes(StandardCharsets.UTF_8).length > 8192)
            throw new IllegalArgumentException("Connection file too large");
        JsonNode node = new ObjectMapper().readTree(value);
        if (node == null || node.path("version").asInt() != 1)
            throw new IllegalArgumentException("Unsupported connection file");
        URI uri = new URI(node.path("endpoint").asText());
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
                || uri.getQuery() != null || uri.getFragment() != null
                || !(uri.getPath().isEmpty() || "/".equals(uri.getPath()))
                || uri.getPort() == 0 || uri.getPort() > 65535)
            throw new IllegalArgumentException("An HTTPS Mac endpoint is required");
        String pin = node.path("certificate_sha256").asText();
        String token = node.path("token").asText();
        if (!pin.matches("[a-fA-F0-9]{64}") || !token.matches("[A-Za-z0-9_-]{43}"))
            throw new IllegalArgumentException("Invalid Mac connection credential");
        String receipt = node.path("pairing_receipt").asText("");
        if (!receipt.isEmpty() && !receipt.matches("[A-Za-z0-9_-]{43}"))
            throw new IllegalArgumentException("Invalid pairing receipt");
        String endpoint = uri.toString().replaceAll("/$", "");
        return new BridgeConnection(endpoint, pin.toLowerCase(java.util.Locale.ROOT), token, receipt);
    }
    public String encode() throws Exception {
        Map<String,Object> value = new LinkedHashMap<>();
        value.put("version", 1); value.put("endpoint", endpoint);
        value.put("certificate_sha256", certificateSha256); value.put("token", token);
        return new ObjectMapper().writeValueAsString(value);
    }
}
