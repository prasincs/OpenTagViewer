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
}
