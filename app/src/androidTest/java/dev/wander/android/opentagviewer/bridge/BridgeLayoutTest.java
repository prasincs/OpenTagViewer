package dev.wander.android.opentagviewer.bridge;

import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.LayoutInflater;
import android.view.View;
import android.widget.TextView;
import androidx.appcompat.view.ContextThemeWrapper;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.File;
import java.io.FileOutputStream;
import dev.wander.android.opentagviewer.R;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class BridgeLayoutTest {
    @Test public void connectionScreenInflatesInBothThemesAndHasUsableControls() throws Exception {
        Context base=InstrumentationRegistry.getInstrumentation().getTargetContext();
        for(boolean dark:new boolean[]{false,true}) {
            Configuration config=new Configuration(base.getResources().getConfiguration());
            config.uiMode=(config.uiMode&~Configuration.UI_MODE_NIGHT_MASK)|(dark?Configuration.UI_MODE_NIGHT_YES:Configuration.UI_MODE_NIGHT_NO);
            Context context=new ContextThemeWrapper(base.createConfigurationContext(config),R.style.Theme_OpenTagViewer);
            InstrumentationRegistry.getInstrumentation().runOnMainSync(()->{
                View view=LayoutInflater.from(context).inflate(R.layout.activity_mac_bridge,null,false);
                for(int id:new int[]{R.id.bridge_scan,R.id.bridge_import,R.id.bridge_connect,R.id.bridge_pairing,R.id.bridge_direct,R.id.bridge_status})assertNotNull(view.findViewById(id));
                view.measure(View.MeasureSpec.makeMeasureSpec(1080,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(1800,View.MeasureSpec.EXACTLY));
                view.layout(0,0,1080,1800);
                assertTrue(view.findViewById(R.id.bridge_connect).getHeight()>0);
                assertTrue(view.findViewById(R.id.bridge_import).getHeight()>0);
                assertEquals(context.getString(R.string.bridge_connect),((TextView)view.findViewById(R.id.bridge_connect)).getText().toString());
                Bitmap bitmap=Bitmap.createBitmap(1080,1800,Bitmap.Config.ARGB_8888);view.draw(new Canvas(bitmap));
                String target=InstrumentationRegistry.getArguments().getString("additionalTestOutputDir");
                if(target!=null)try(FileOutputStream out=new FileOutputStream(new File(target,"bridge-connect-"+(dark?"dark":"light")+".png"))){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}catch(Exception e){throw new AssertionError(e);}
            });
        }
    }
}
