package dev.wander.android.opentagviewer.bridge;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLException;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

public class BridgeClient {
    public enum Failure { CONNECTION, CERTIFICATE, AUTH, PROTOCOL }
    public static final class BridgeException extends Exception {
        public final Failure failure;
        public BridgeException(Failure failure) { super(failure.name()); this.failure = failure; }
    }
    public static final class Item {
        public final String id, name;
        public final Double latitude, longitude;
        public final long timestamp;
        Item(String id, String name, Double lat, Double lon, long timestamp) {
            this.id=id; this.name=name; this.latitude=lat; this.longitude=lon; this.timestamp=timestamp;
        }
    }
    public static final class Snapshot {
        public final String status;
        public final List<Item> items;
        public Snapshot(String status, List<Item> items) { this.status=status; this.items=items; }
    }
    /** Pin the exact Mac certificate. Hostname and expiration checks remain required. */
    public static X509TrustManager trustManager(String expected) {
        return new X509TrustManager() {
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            public void checkClientTrusted(X509Certificate[] chain, String auth) throws CertificateException {
                throw new CertificateException("Client certificate not supported");
            }
            public void checkServerTrusted(X509Certificate[] chain, String auth) throws CertificateException {
                if (chain == null || chain.length != 1) throw new CertificateException("Unexpected certificate chain");
                chain[0].checkValidity();
                try {
                    byte[] hash=MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());
                    byte[] pin=new byte[32];
                    for(int i=0;i<32;i++) pin[i]=(byte)Integer.parseInt(expected.substring(i*2,i*2+2),16);
                    if (!MessageDigest.isEqual(hash,pin)) throw new CertificateException("Mac certificate mismatch");
                } catch(CertificateException e) { throw e; }
                catch(Exception e) { throw new CertificateException("Invalid certificate pin",e); }
            }
        };
    }
    /** Exchange a one-time invitation only after verifying its out-of-band certificate pin. */
    public BridgeConnection pair(BridgeInvitation invitation) throws BridgeException {
        HttpsURLConnection connection=null;
        try {
            if(invitation.expiresAt<=System.currentTimeMillis()/1000)throw new BridgeException(Failure.AUTH);
            BridgeConnection transport=invitation.transport;
            SSLContext tls=SSLContext.getInstance("TLS");
            tls.init(null,new TrustManager[]{trustManager(transport.certificateSha256)},null);
            connection=(HttpsURLConnection)new URL(transport.endpoint+"/v1/pair").openConnection();
            connection.setSSLSocketFactory(tls.getSocketFactory());
            connection.setInstanceFollowRedirects(false);connection.setUseCaches(false);
            connection.setConnectTimeout(8000);connection.setReadTimeout(10000);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Authorization","Pairing "+transport.token);
            connection.setRequestProperty("Accept","application/json");
            int status=connection.getResponseCode();
            if(status==401||status==403||status==429)throw new BridgeException(Failure.AUTH);
            if(status!=200)throw new BridgeException(Failure.PROTOCOL);
            try(InputStream in=connection.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                byte[] buffer=new byte[1024];int count;
                while((count=in.read(buffer))!=-1){if(out.size()+count>8192)throw new BridgeException(Failure.PROTOCOL);out.write(buffer,0,count);}
                return parsePairingResponse(invitation,new String(out.toByteArray(),StandardCharsets.UTF_8));
            }
        }catch(BridgeException e){throw e;}
        catch(SSLException e){throw new BridgeException(Failure.CERTIFICATE);}
        catch(Exception e){throw new BridgeException(Failure.CONNECTION);}
        finally{if(connection!=null)connection.disconnect();}
    }
    /** Confirm only after the phone verified the API and saved its bridge credential. */
    public void confirm(BridgeConnection config,boolean success) throws BridgeException {
        if(config.pairingReceipt.isEmpty())return; // Legacy file/one-shot invitation.
        HttpsURLConnection connection=null;
        try{
            SSLContext tls=SSLContext.getInstance("TLS");
            tls.init(null,new TrustManager[]{trustManager(config.certificateSha256)},null);
            connection=(HttpsURLConnection)new URL(config.endpoint+"/v1/pair/confirm").openConnection();
            connection.setSSLSocketFactory(tls.getSocketFactory());connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);connection.setConnectTimeout(8000);connection.setReadTimeout(10000);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Authorization","Receipt "+config.pairingReceipt);
            connection.setRequestProperty("X-Pairing-Result",success?"connected":"failed");
            if(connection.getResponseCode()!=200)throw new BridgeException(Failure.AUTH);
        }catch(BridgeException e){throw e;}
        catch(SSLException e){throw new BridgeException(Failure.CERTIFICATE);}
        catch(Exception e){throw new BridgeException(Failure.CONNECTION);}
        finally{if(connection!=null)connection.disconnect();}
    }
    public static BridgeConnection parsePairingResponse(BridgeInvitation invitation,String raw) throws BridgeException {
        try{
            BridgeConnection paired=BridgeConnection.parse(raw);
            if(!paired.endpoint.equals(invitation.transport.endpoint)||
                !paired.certificateSha256.equals(invitation.transport.certificateSha256))throw new IllegalArgumentException();
            return paired;
        }catch(Exception e){throw new BridgeException(Failure.PROTOCOL);}
    }
    public Snapshot fetch(BridgeConnection config) throws BridgeException {
        HttpsURLConnection connection=null;
        try {
            SSLContext tls=SSLContext.getInstance("TLS");
            tls.init(null,new TrustManager[]{trustManager(config.certificateSha256)},null);
            connection=(HttpsURLConnection)new URL(config.endpoint+"/v1/items").openConnection();
            connection.setSSLSocketFactory(tls.getSocketFactory());
            // Keep the platform hostname verifier; never follow a credential-bearing redirect.
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(8000); connection.setReadTimeout(10000);
            connection.setRequestProperty("Authorization","Bearer "+config.token);
            connection.setRequestProperty("Accept","application/json");
            connection.setUseCaches(false);
            int status=connection.getResponseCode();
            if(status==401||status==403) throw new BridgeException(Failure.AUTH);
            if(status!=200) throw new BridgeException(Failure.PROTOCOL);
            try(InputStream input=connection.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                byte[] buffer=new byte[4096]; int count;
                while((count=input.read(buffer))!=-1) {
                    if(out.size()+count>256*1024) throw new BridgeException(Failure.PROTOCOL);
                    out.write(buffer,0,count);
                }
                return parseSnapshot(new String(out.toByteArray(),StandardCharsets.UTF_8));
            }
        } catch(BridgeException e) { throw e; }
        catch(SSLException e) { throw new BridgeException(Failure.CERTIFICATE); }
        catch(Exception e) { throw new BridgeException(Failure.CONNECTION); }
        finally { if(connection!=null) connection.disconnect(); }
    }
    public static Snapshot parseSnapshot(String raw) throws BridgeException {
        try {
            if(raw.length()>256*1024) throw new IllegalArgumentException();
            JsonNode root=new ObjectMapper().readTree(raw);
            String status=root.path("status").asText();
            if(root.path("schema").asInt()!=1 || !List.of("ready","setup_required","mac_error").contains(status)
                    || !root.path("items").isArray() || root.path("items").size()>500)
                throw new IllegalArgumentException();
            List<Item> items=new ArrayList<>();
            for(JsonNode item:root.path("items")) {
                String id=item.path("id").asText(),name=item.path("name").asText();
                if(id.isEmpty()||id.length()>128||name.isEmpty()||name.length()>120)throw new IllegalArgumentException();
                JsonNode loc=item.path("location");Double lat=null,lon=null;long timestamp=0;
                if(!loc.isNull()&&!loc.isMissingNode()) {
                    if(!loc.path("latitude").isNumber()||!loc.path("longitude").isNumber())throw new IllegalArgumentException();
                    lat=loc.path("latitude").asDouble();lon=loc.path("longitude").asDouble();
                    timestamp=loc.path("timestamp").asLong();
                    if(!Double.isFinite(lat)||!Double.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180||timestamp<0)
                        throw new IllegalArgumentException();
                }
                items.add(new Item(id,name,lat,lon,timestamp));
            }
            return new Snapshot(status,items);
        }catch(Exception e){throw new BridgeException(Failure.PROTOCOL);}
    }
}
