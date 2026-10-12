package dev.wander.android.opentagviewer.bridge;
import java.security.MessageDigest;
import java.security.cert.CertificateFactory;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import org.junit.Test;
import static org.junit.Assert.*;
public class BridgeCertificateTest {
    private X509Certificate cert()throws Exception{return (X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(getClass().getResourceAsStream("/bridge-test-certificate.pem"));}
    private String pin(X509Certificate c)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(c.getEncoded()))out.append(String.format("%02x",b&255));return out.toString();}
    @Test public void exactCertificateIsAccepted()throws Exception{X509Certificate c=cert();BridgeClient.trustManager(pin(c)).checkServerTrusted(new X509Certificate[]{c},"EC");}
    @Test public void differentCertificatePinIsRejected()throws Exception{try{BridgeClient.trustManager("0".repeat(64)).checkServerTrusted(new X509Certificate[]{cert()},"EC");fail();}catch(CertificateException expected){}}
    @Test public void unexpectedChainsAndEmptyChainsAreRejected()throws Exception{for(X509Certificate[] chain:new X509Certificate[][]{new X509Certificate[0],new X509Certificate[]{cert(),cert()}}){try{BridgeClient.trustManager(pin(cert())).checkServerTrusted(chain,"EC");fail();}catch(CertificateException expected){}}}
}
