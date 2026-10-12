package dev.wander.android.opentagviewer.bridge;

import android.os.Bundle;
import android.view.WindowManager;
import com.journeyapps.barcodescanner.CaptureActivity;

/** Camera-only scanner in the isolated bridge process. No barcode images are saved. */
public class MacPairScannerActivity extends CaptureActivity {
    @Override protected void onCreate(Bundle state){
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    }
}
