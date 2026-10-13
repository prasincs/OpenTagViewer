package dev.wander.android.opentagviewer.bridge;

import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.format.DateUtils;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanOptions;
import com.google.android.material.button.MaterialButton;
import com.google.android.material.textfield.TextInputEditText;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import dev.wander.android.opentagviewer.AppleLoginActivity;
import dev.wander.android.opentagviewer.R;
import dev.wander.android.opentagviewer.ui.compat.WindowPaddingUtil;
import dev.wander.android.opentagviewer.ui.maps.IMapProvider;
import dev.wander.android.opentagviewer.ui.maps.MapMarker;

/** The first screen; only a Mac-scoped location token enters this mode. */
public class MacBridgeActivity extends AppCompatActivity {
    private BridgePreferences preferences;
    private BridgeConnection config;
    private TextView status;
    private TextInputEditText pairing;
    private LinearLayout items;
    private IMapProvider map;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private boolean resumed=false,loading=false;
    private int generation=0;
    private final Runnable poll=()->refresh(false);
    private final ActivityResultLauncher<String[]> importFile=registerForActivityResult(
        new ActivityResultContracts.OpenDocument(),uri->{
            if(uri==null)return;
            try(InputStream in=getContentResolver().openInputStream(uri);ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                if(in==null)throw new IllegalArgumentException();
                byte[] buffer=new byte[1024];int n;
                while((n=in.read(buffer))!=-1){if(out.size()+n>8192)throw new IllegalArgumentException();out.write(buffer,0,n);}
                pairing.setText(new String(out.toByteArray(),StandardCharsets.UTF_8));
                connect();
            }catch(Exception e){status.setText(R.string.bridge_invalid);}
        });
    private final ActivityResultLauncher<ScanOptions> scan=registerForActivityResult(new ScanContract(),result->{
        if(result.getContents()!=null)acceptInvitation(result.getContents());
    });
    void acceptInvitation(String code){
        final BridgeInvitation invitation;
        try{invitation=BridgeInvitation.parse(code,System.currentTimeMillis()/1000);}
        catch(Exception e){status.setText(R.string.bridge_invitation_invalid);return;}
        if(loading)return;
        loading=true;int requestGeneration=++generation;handler.removeCallbacks(poll);
        status.setText(R.string.bridge_loading);
        worker.submit(()->{
            BridgeClient client=BridgeDependencies.client.get();
            BridgeConnection claimed=null;
            try{
                claimed=client.pair(invitation);
                BridgeClient.Snapshot snapshot=client.fetch(claimed);
                BridgeConnection paired=claimed;
                handler.post(()->{if(isDestroyed())return;
                    if(generation!=requestGeneration){
                        worker.submit(()->{try{client.confirm(paired,false);}catch(Exception ignored){}});return;}
                    try{preferences.save(paired);}catch(Exception e){
                        loading=false;status.setText(R.string.bridge_invalid);
                        worker.submit(()->{try{client.confirm(paired,false);}catch(Exception ignored){}});return;}
                    worker.submit(()->{
                        try{client.confirm(paired,true);
                            handler.post(()->{if(isDestroyed()||generation!=requestGeneration)return;
                                loading=false;config=paired;pairing.setText("");showDashboard();render(snapshot);schedule();});
                        }catch(Exception e){handler.post(()->{if(isDestroyed()||generation!=requestGeneration)return;
                            loading=false;preferences.forget();status.setText(R.string.bridge_pairing_failed);});}
                    });
                });
            }catch(Exception e){
                if(claimed!=null)try{client.confirm(claimed,false);}catch(Exception ignored){}
                handler.post(()->{if(isDestroyed()||generation!=requestGeneration)return;
                    loading=false;
                    status.setText(e instanceof BridgeClient.BridgeException&&((BridgeClient.BridgeException)e).failure==BridgeClient.Failure.CERTIFICATE?
                        R.string.bridge_certificate_error:R.string.bridge_pairing_failed);});
            }
        });
    }

    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        setContentView(R.layout.activity_mac_bridge);
        WindowPaddingUtil.insetForSystemBarsAndKeyboard(findViewById(R.id.bridge_root));
        if(getSupportActionBar()!=null)getSupportActionBar().hide();
        preferences=new BridgePreferences(this);status=findViewById(R.id.bridge_status);
        pairing=findViewById(R.id.bridge_pairing);items=findViewById(R.id.bridge_items);
        findViewById(R.id.bridge_scan).setOnClickListener(v->{
            ScanOptions options=new ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setCaptureActivity(MacPairScannerActivity.class).setOrientationLocked(true)
                .setBeepEnabled(false).setBarcodeImageEnabled(false).setPrompt(getString(R.string.bridge_scan_prompt));
            scan.launch(options);
        });
        findViewById(R.id.bridge_connect).setOnClickListener(v->connect());
        findViewById(R.id.bridge_import).setOnClickListener(v->importFile.launch(new String[]{"application/json","text/plain"}));
        findViewById(R.id.bridge_refresh).setOnClickListener(v->refresh(false));
        findViewById(R.id.bridge_disconnect).setOnClickListener(v->{
            generation++;loading=false;config=null;preferences.forget();handler.removeCallbacks(poll);items.removeAllViews();
            if(map!=null)map.clear();findViewById(R.id.bridge_dashboard).setVisibility(View.GONE);
            findViewById(R.id.bridge_setup).setVisibility(View.VISIBLE);status.setText("");
        });
        findViewById(R.id.bridge_direct).setOnClickListener(v->{
            BridgePreferences.setDirectMode(this,true);startActivity(new Intent(this,AppleLoginActivity.class));
        });
        try{config=preferences.read();}catch(Exception ignored){status.setText(R.string.bridge_invalid);}
        if(config!=null)showDashboard();
    }
    private void connect(){
        if(loading)return;
        try {BridgeConnection next=BridgeConnection.parse(pairing.getText()==null?"":pairing.getText().toString());
            generation++;config=next;refresh(true);
        }catch(Exception e){status.setText(R.string.bridge_invalid);}
    }
    private void showDashboard(){
        findViewById(R.id.bridge_setup).setVisibility(View.GONE);
        findViewById(R.id.bridge_dashboard).setVisibility(View.VISIBLE);
    }
    private void refresh(boolean pairingNew){
        if(config==null||loading)return;loading=true;handler.removeCallbacks(poll);
        status.setText(R.string.bridge_loading);int requestGeneration=generation;BridgeConnection request=config;
        worker.submit(()->{
            try{BridgeClient.Snapshot snapshot=BridgeDependencies.client.get().fetch(request);

                handler.post(()->{if(isDestroyed()||generation!=requestGeneration)return;
                    loading=false;
                    if(pairingNew)try{preferences.save(request);}catch(Exception e){status.setText(R.string.bridge_invalid);return;}
                    pairing.setText("");showDashboard();render(snapshot);schedule();});
            }catch(Exception error){handler.post(()->{if(isDestroyed()||generation!=requestGeneration)return;
                loading=false;
                BridgeClient.Failure failure=error instanceof BridgeClient.BridgeException?
                    ((BridgeClient.BridgeException)error).failure:BridgeClient.Failure.CONNECTION;
                int message=failure==BridgeClient.Failure.AUTH?R.string.bridge_auth_error:
                    failure==BridgeClient.Failure.CERTIFICATE?R.string.bridge_certificate_error:
                    failure==BridgeClient.Failure.PROTOCOL?R.string.bridge_protocol_error:R.string.bridge_connection_error;
                status.setText(message);schedule();});}
        });
    }
    private void schedule(){if(resumed&&config!=null)handler.postDelayed(poll,60000);}
    private void render(BridgeClient.Snapshot snapshot){
        status.setText("setup_required".equals(snapshot.status)?R.string.bridge_setup_required:
            "mac_error".equals(snapshot.status)?R.string.bridge_mac_error:R.string.bridge_connected);
        items.removeAllViews();
        boolean located=snapshot.items.stream().anyMatch(item->item.latitude!=null);
        findViewById(R.id.bridge_map).setVisibility(located?View.VISIBLE:View.GONE);
        if(located&&map==null){map=BridgeDependencies.map.get();if(map!=null){
            map.initialize(this,R.id.bridge_map,ready->drawMarkers(snapshot));if(resumed)map.onResume();}}
        else if(map!=null)drawMarkers(snapshot);
        if(snapshot.items.isEmpty()&&"ready".equals(snapshot.status)){
            TextView empty=new TextView(this);empty.setText(R.string.bridge_no_items);items.addView(empty);
        }
        for(BridgeClient.Item item:snapshot.items){
            View card=getLayoutInflater().inflate(R.layout.bridge_item,items,false);
            ((TextView)card.findViewById(R.id.bridge_item_name)).setText(item.name);
            TextView detail=card.findViewById(R.id.bridge_item_detail);
            detail.setText(item.latitude==null?getString(R.string.bridge_no_location):
                getString(R.string.bridge_updated,DateUtils.getRelativeTimeSpanString(item.timestamp,
                    System.currentTimeMillis(),DateUtils.MINUTE_IN_MILLIS).toString()));
            if(item.latitude!=null)card.setOnClickListener(v->{if(map!=null)map.moveCamera(item.latitude,item.longitude,map.initialZoom());});
            items.addView(card);
        }
    }
    private void drawMarkers(BridgeClient.Snapshot snapshot){
        if(map==null)return;map.clear();boolean first=true;
        for(BridgeClient.Item item:snapshot.items)if(item.latitude!=null){
            map.addMarker(MapMarker.builder().id(item.id).title(item.name).position(item.latitude,item.longitude).useDefaultIcon(true).build());
            if(first){map.moveCamera(item.latitude,item.longitude,map.initialZoom());first=false;}
        }
    }
    @Override public void onResume(){super.onResume();resumed=true;if(map!=null)map.onResume();refresh(false);}
    @Override public void onPause(){resumed=false;handler.removeCallbacks(poll);if(map!=null)map.onPause();super.onPause();}
    @Override public void onDestroy(){generation++;handler.removeCallbacksAndMessages(null);worker.shutdownNow();if(map!=null)map.onDestroy();super.onDestroy();}
}
