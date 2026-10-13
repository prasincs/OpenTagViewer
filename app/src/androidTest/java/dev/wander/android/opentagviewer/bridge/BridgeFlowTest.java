package dev.wander.android.opentagviewer.bridge;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import com.chaquo.python.Python;
import dev.wander.android.opentagviewer.BuildConfig;
import dev.wander.android.opentagviewer.R;
import dev.wander.android.opentagviewer.Eventually;
import static org.junit.Assert.*;
import static org.junit.Assume.assumeTrue;
import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.*;
import static androidx.test.espresso.assertion.ViewAssertions.matches;
import static androidx.test.espresso.matcher.ViewMatchers.*;

@RunWith(AndroidJUnit4.class)
public class BridgeFlowTest {
    private ActivityScenario<MacBridgeActivity> scenario;
    private final AtomicInteger calls=new AtomicInteger();
    private static final String CODE="{\"version\":1,\"endpoint\":\"https://127.0.0.1:8766\",\"certificate_sha256\":\""+"a".repeat(64)+"\",\"token\":\""+"b".repeat(43)+"\"}";
    @Before public void setup(){
        assumeTrue(BuildConfig.BRIDGE_UI_TESTS);
        new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).forget();
        BridgeDependencies.map=()->null;
        BridgeDependencies.client=()->new BridgeClient(){@Override public Snapshot fetch(BridgeConnection c){calls.incrementAndGet();return new Snapshot("setup_required",List.of());}};
        scenario=ActivityScenario.launch(MacBridgeActivity.class);
    }
    @After public void cleanup(){if(scenario!=null)scenario.close();BridgeDependencies.reset();}
    @Test public void macIsFirstAndNeverStartsPython(){
        onView(withId(R.id.bridge_connect)).check(matches(isDisplayed()));assertFalse(Python.isStarted());
    }
    @Test public void invalidPairingIsExplainedAndNeverCallsTheNetwork(){
        onView(withId(R.id.bridge_pairing)).perform(replaceText("not a connection file"),closeSoftKeyboard());
        onView(withId(R.id.bridge_connect)).perform(click());
        onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_invalid)));assertEquals(0,calls.get());
    }
    @Test public void verifiedConnectionExplainsMacSetupAndStoresOnlyBridgeCredential() throws Exception {
        onView(withId(R.id.bridge_pairing)).perform(replaceText(CODE),closeSoftKeyboard());
        onView(withId(R.id.bridge_connect)).perform(click());
        Eventually.check(()->onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_setup_required))));
        assertNotNull(new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).read());
        assertFalse(Python.isStarted());
    }
    @Test public void readyReportsShowNamesWithoutStartingAppleCode(){
        BridgeDependencies.client=()->new BridgeClient(){@Override public Snapshot fetch(BridgeConnection c){return new Snapshot("ready",List.of(new Item("test","Synthetic backpack",12.5,34.5,System.currentTimeMillis()-60000)));}};
        onView(withId(R.id.bridge_pairing)).perform(replaceText(CODE),closeSoftKeyboard());
        onView(withId(R.id.bridge_connect)).perform(click());
        Eventually.check(()->onView(withText("Synthetic backpack")).check(matches(isDisplayed())));
        assertFalse(Python.isStarted());
        onView(withId(R.id.bridge_disconnect)).perform(click());
        onView(withId(R.id.bridge_connect)).check(matches(isDisplayed()));
        try{assertNull(new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).read());}catch(Exception e){throw new AssertionError(e);}
    }
    @Test public void rejectedCertificateIsShownAndCredentialIsNotStored(){
        BridgeDependencies.client=()->new BridgeClient(){@Override public Snapshot fetch(BridgeConnection c)throws BridgeException{throw new BridgeException(Failure.CERTIFICATE);}};
        onView(withId(R.id.bridge_pairing)).perform(replaceText(CODE),closeSoftKeyboard());
        onView(withId(R.id.bridge_connect)).perform(click());
        Eventually.check(()->onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_certificate_error))));
        try{assertNull(new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).read());}catch(Exception e){throw new AssertionError(e);}
    }
    private String invitation(long expiry){
        String raw="{\"pairing_version\":1,\"endpoint\":\"https://127.0.0.1:8766\",\"certificate_sha256\":\""+"a".repeat(64)+"\",\"secret\":\""+"s".repeat(43)+"\",\"expires_at\":"+expiry+"}";
        return "homefind-pair:"+java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }
    @Test public void qrSetupIsOfferedAndExpiredInvitationNeverCallsNetwork(){
        onView(withId(R.id.bridge_scan)).check(matches(isDisplayed()));
        scenario.onActivity(activity->activity.acceptInvitation(invitation(1)));
        onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_invitation_invalid)));
        assertEquals(0,calls.get());assertFalse(Python.isStarted());
    }
    @Test public void qrExchangeStoresOnlyReturnedCredentialAfterVerifiedFetch() throws Exception{
        AtomicInteger exchanges=new AtomicInteger();
        BridgeDependencies.client=()->new BridgeClient(){
            @Override public BridgeConnection pair(BridgeInvitation i){
                exchanges.incrementAndGet();
                try{return BridgeConnection.parse(CODE);}catch(Exception e){throw new AssertionError(e);}
            }
            @Override public Snapshot fetch(BridgeConnection c){return new Snapshot("setup_required",List.of());}
        };
        scenario.onActivity(activity->activity.acceptInvitation(invitation(System.currentTimeMillis()/1000+300)));
        Eventually.check(()->onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_setup_required))));
        assertEquals(1,exchanges.get());
        BridgeConnection saved=new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).read();
        assertEquals("b".repeat(43),saved.token);assertNotEquals("s".repeat(43),saved.token);
        assertFalse(Python.isStarted());
    }
    @Test public void qrCertificateFailureNeverStoresConnection(){
        BridgeDependencies.client=()->new BridgeClient(){
            @Override public BridgeConnection pair(BridgeInvitation i)throws BridgeException{throw new BridgeException(Failure.CERTIFICATE);}
        };
        scenario.onActivity(activity->activity.acceptInvitation(invitation(System.currentTimeMillis()/1000+300)));
        Eventually.check(()->onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_certificate_error))));
        try{assertNull(new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).read());}catch(Exception e){throw new AssertionError(e);}
        assertFalse(Python.isStarted());
    }

    @Test public void scannerIsPrivateAndSharesTheIsolatedLauncherProcess() throws Exception {
        android.content.Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        android.content.pm.PackageManager pm=context.getPackageManager();
        android.content.pm.ActivityInfo scanner=pm.getActivityInfo(new android.content.ComponentName(context,MacPairScannerActivity.class),0);
        android.content.pm.ActivityInfo launcher=pm.getActivityInfo(new android.content.ComponentName(context,MacBridgeActivity.class),0);
        assertFalse(scanner.exported);assertEquals(launcher.processName,scanner.processName);
    }

    @Test public void confirmationFailureRemovesSavedCredentialAndShowsFailure(){
        BridgeDependencies.client=()->new BridgeClient(){
            @Override public BridgeConnection pair(BridgeInvitation i){try{return BridgeConnection.parse(CODE);}catch(Exception e){throw new AssertionError(e);}}
            @Override public Snapshot fetch(BridgeConnection c){return new Snapshot("setup_required",List.of());}
            @Override public void confirm(BridgeConnection c,boolean success)throws BridgeException{throw new BridgeException(Failure.AUTH);}
        };
        scenario.onActivity(a->a.acceptInvitation(invitation(System.currentTimeMillis()/1000+300)));
        Eventually.check(()->onView(withId(R.id.bridge_status)).check(matches(withText(R.string.bridge_pairing_failed))));
        try{assertNull(new BridgePreferences(InstrumentationRegistry.getInstrumentation().getTargetContext()).read());}catch(Exception e){throw new AssertionError(e);}
    }

}
