package io.github.pvandenboer_alt.crate;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(SpotifyRemotePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
