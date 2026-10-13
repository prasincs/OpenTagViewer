package dev.wander.android.opentagviewer.bridge;

import android.content.Context;
import android.content.pm.PackageManager;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.os.Build;
import android.os.CancellationSignal;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyInfo;
import android.security.keystore.KeyProperties;
import androidx.appcompat.app.AppCompatActivity;
import java.security.KeyFactory;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.spec.MGF1ParameterSpec;
import java.security.spec.RSAKeyGenParameterSpec;
import javax.crypto.Cipher;
import javax.crypto.spec.OAEPParameterSpec;
import javax.crypto.spec.PSource;
import dev.wander.android.opentagviewer.R;

/** No software-key fallback. The private key stays in Android secure hardware. */
public class AndroidVaultKey {
    private static final String ALIAS="home-find-vault-android-v2";
    public static final class Info {
        public final byte[] publicKey;
        public final String fingerprint,securityLevel;
        Info(byte[] publicKey,String level)throws Exception{
            this.publicKey=publicKey;this.securityLevel=level;
            StringBuilder text=new StringBuilder();
            for(byte b:MessageDigest.getInstance("SHA-256").digest(publicKey))text.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
            fingerprint=text.toString();
        }
    }
    public Info prepare(Context context)throws Exception{
        if(Build.VERSION.SDK_INT<31)throw new IllegalStateException("Android 12 required");
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias(ALIAS)){
            boolean strong=context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE);
            try{generate(strong);}catch(java.security.ProviderException e){if(!strong)throw e;generate(false);}
        }
        PrivateKey key=(PrivateKey)store.getKey(ALIAS,null);
        KeyInfo info=KeyFactory.getInstance("RSA","AndroidKeyStore").getKeySpec(key,KeyInfo.class);
        int level=info.getSecurityLevel();
        if(level!=KeyProperties.SECURITY_LEVEL_STRONGBOX&&level!=KeyProperties.SECURITY_LEVEL_TRUSTED_ENVIRONMENT)
            throw new IllegalStateException("Hardware protection required");
        if(!info.isUserAuthenticationRequired()||info.getUserAuthenticationValidityDurationSeconds()!=30)
            throw new IllegalStateException("Phone authentication required");
        return new Info(store.getCertificate(ALIAS).getPublicKey().getEncoded(),level==KeyProperties.SECURITY_LEVEL_STRONGBOX?"strongbox":"tee");
    }
    private void generate(boolean strong)throws Exception{
        KeyPairGenerator generator=KeyPairGenerator.getInstance("RSA","AndroidKeyStore");
        KeyGenParameterSpec spec=new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_DECRYPT)
            .setAlgorithmParameterSpec(new RSAKeyGenParameterSpec(2048,RSAKeyGenParameterSpec.F4))
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP)
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(true)
            .setUserAuthenticationParameters(30,KeyProperties.AUTH_BIOMETRIC_STRONG|KeyProperties.AUTH_DEVICE_CREDENTIAL)
            .setIsStrongBoxBacked(strong).build();
        generator.initialize(spec);generator.generateKeyPair();
    }
    public byte[] unwrap(byte[] ciphertext)throws Exception{
        if(ciphertext.length!=256)throw new IllegalArgumentException();
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        Cipher cipher=Cipher.getInstance("RSA/ECB/OAEPPadding");
        cipher.init(Cipher.DECRYPT_MODE,store.getKey(ALIAS,null),new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
        byte[] key=cipher.doFinal(ciphertext);
        if(key.length!=32)throw new IllegalArgumentException();
        return key;
    }
    public CancellationSignal authenticate(AppCompatActivity activity,Runnable approved,Runnable denied){
        CancellationSignal signal=new CancellationSignal();
        if(Build.VERSION.SDK_INT<31){denied.run();return signal;}
        new BiometricPrompt.Builder(activity).setTitle(activity.getString(R.string.vault_auth_title))
            .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG|BiometricManager.Authenticators.DEVICE_CREDENTIAL)
            .build().authenticate(signal,activity.getMainExecutor(),new BiometricPrompt.AuthenticationCallback(){
                @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult result){approved.run();}
                @Override public void onAuthenticationError(int code,CharSequence error){denied.run();}
            });
        return signal;
    }
}
