package dev.wander.android.opentagviewer.bridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import dev.wander.android.opentagviewer.util.android.AppCryptographyUtil;

public final class BridgePreferences {
    private static final String STORE="mac_bridge", ALIAS="home-find-mac-bridge";
    private final SharedPreferences prefs;
    public BridgePreferences(Context context){prefs=context.getSharedPreferences(STORE,Context.MODE_PRIVATE);}
    public static boolean isDirectMode(Context context){return new BridgePreferences(context).prefs.getBoolean("direct_mode",false);}
    public static void setDirectMode(Context context,boolean direct){
        if(!new BridgePreferences(context).prefs.edit().putBoolean("direct_mode",direct).commit())
            throw new IllegalStateException("Could not save mode");
    }
    public BridgeConnection read() throws Exception {
        String value=prefs.getString("credential",null);if(value==null)return null;
        byte[] plain=new AppCryptographyUtil().decrypt(
            AppCryptographyUtil.AppEncryptedData.fromFlattened(Base64.decode(value,Base64.NO_WRAP)),ALIAS);
        return BridgeConnection.parse(new String(plain,StandardCharsets.UTF_8));
    }
    public void save(BridgeConnection connection) throws Exception {
        byte[] encrypted=new AppCryptographyUtil().encrypt(connection.encode().getBytes(StandardCharsets.UTF_8),ALIAS).flatten();
        if(!prefs.edit().putString("credential",Base64.encodeToString(encrypted,Base64.NO_WRAP))
                .putBoolean("direct_mode",false).commit())throw new IllegalStateException("Could not save connection");
    }
    public void forget(){prefs.edit().remove("credential").putBoolean("direct_mode",false).commit();}
}
