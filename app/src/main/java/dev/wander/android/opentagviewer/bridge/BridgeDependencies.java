package dev.wander.android.opentagviewer.bridge;

import dev.wander.android.opentagviewer.ui.maps.IMapProvider;
import dev.wander.android.opentagviewer.ui.maps.OsmMapProvider;
import java.util.function.Supplier;

/** Fakes are installed by device tests; real mode has no Apple or Anisette dependency. */
public final class BridgeDependencies {
    public static Supplier<BridgeClient> client=BridgeClient::new;
    public static Supplier<IMapProvider> map=OsmMapProvider::new;
    public static void reset(){client=BridgeClient::new;map=OsmMapProvider::new;}
}
