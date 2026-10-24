package io.github.pvandenboer_alt.crate;

import android.content.Intent;
import android.graphics.Bitmap;
import android.util.Base64;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.spotify.android.appremote.api.ConnectionParams;
import com.spotify.android.appremote.api.Connector;
import com.spotify.android.appremote.api.SpotifyAppRemote;
import com.spotify.android.appremote.api.error.UserNotAuthorizedException;
import com.spotify.protocol.client.CallResult;
import com.spotify.protocol.client.Subscription;
import com.spotify.protocol.types.Empty;
import com.spotify.protocol.types.Image;
import com.spotify.protocol.types.PlayerContext;
import com.spotify.protocol.types.PlayerState;
import com.spotify.protocol.types.Track;
import com.spotify.sdk.android.auth.AuthorizationClient;
import com.spotify.sdk.android.auth.AuthorizationRequest;
import com.spotify.sdk.android.auth.AuthorizationResponse;

import java.io.ByteArrayOutputStream;

/**
 * Crate's remote control for the Spotify app (Spotify's App Remote SDK): Spotify plays the music, Crate
 * tells it what to play and shows what's playing. Needs the Spotify app installed and logged in.
 */
@CapacitorPlugin(name = "SpotifyRemote")
public class SpotifyRemotePlugin extends Plugin {
    private SpotifyAppRemote remote;
    private ConnectionParams params;
    private String clientId = "", redirectUri = "";
    private boolean authorizing = false;   // Spotify's login screen is up: don't reconnect underneath it
    private Subscription<PlayerState> playerSub;
    private boolean wanted = false;   // connected before the app went to the background: reconnect on return
    private Subscription<PlayerContext> contextSub;
    private String imageKey = "", contextTitle = "", libraryAsked = "", savedUri = "";
    private boolean saved = false, canSave = false;
    private PlayerState last;

    private boolean connected() {
        return remote != null && remote.isConnected();
    }

    @PluginMethod
    public void status(PluginCall call) {
        JSObject r = new JSObject();
        r.put("installed", SpotifyAppRemote.isSpotifyInstalled(getContext()));
        r.put("connected", connected());
        call.resolve(r);
    }

    @PluginMethod
    public void connect(PluginCall call) {
        String id = call.getString("clientId", ""), redirect = call.getString("redirectUri", "");
        if (id.isEmpty()) {
            call.reject("No Spotify client ID in this build", "NoClientId");
            return;
        }
        if (!SpotifyAppRemote.isSpotifyInstalled(getContext())) {
            call.reject("The Spotify app isn't installed", "CouldNotFindSpotifyApp");
            return;
        }
        clientId = id;
        redirectUri = redirect;
        params = new ConnectionParams.Builder(id).setRedirectUri(redirect).showAuthView(true).build();
        wanted = true;
        open(call, true);
    }

    /**
     * Recent Spotify apps no longer show App Remote's own approval screen, so a first connection fails
     * with UserNotAuthorizedException. Spotify's login (its auth library) asks you once to allow Crate to
     * control playback; after that, connecting works.
     */
    private void authorize(PluginCall call) {
        AuthorizationRequest req = new AuthorizationRequest.Builder(clientId, AuthorizationResponse.Type.CODE, redirectUri)
                .setScopes(new String[]{"app-remote-control"}).build();
        Intent intent = AuthorizationClient.createLoginActivityIntent(getActivity(), req);
        authorizing = true;
        startActivityForResult(call, intent, "authorized");
    }

    @ActivityCallback
    private void authorized(PluginCall call, ActivityResult result) {
        authorizing = false;
        if (call == null) return;
        AuthorizationResponse r = AuthorizationClient.getResponse(result.getResultCode(), result.getData());
        switch (r.getType()) {
            case CODE:
            case TOKEN:
                open(call, false);
                break;
            case ERROR:
                // The Spotify app's own login can refuse (AUTHENTICATION_SERVICE_UNAVAILABLE). Spotify's web
                // login does the same approval in the browser; coming back to Crate then connects.
                AuthorizationRequest req = new AuthorizationRequest.Builder(clientId, AuthorizationResponse.Type.CODE, redirectUri)
                        .setScopes(new String[]{"app-remote-control"}).setShowDialog(true).build();
                AuthorizationClient.openLoginInBrowser(getActivity(), req);
                call.reject("Spotify login in the app failed (" + r.getError() + "): continue in the browser", "BrowserLogin");
                break;
            default:
                call.reject("Spotify login was cancelled", "AuthorizationCancelled");
        }
    }

    private void open(PluginCall call) {
        open(call, false);
    }

    private void open(PluginCall call, boolean mayAuthorize) {
        if (connected()) {
            if (call != null) call.resolve();
            return;
        }
        getActivity().runOnUiThread(() -> SpotifyAppRemote.connect(getContext(), params, new Connector.ConnectionListener() {
            @Override
            public void onConnected(SpotifyAppRemote r) {
                remote = r;
                subscribe();
                connection(true, null);
                if (call != null) call.resolve();
            }

            @Override
            public void onFailure(Throwable t) {
                remote = null;
                if (call != null && mayAuthorize && t instanceof UserNotAuthorizedException) {
                    authorize(call);
                    return;
                }
                connection(false, t);
                if (call != null) call.reject(String.valueOf(t.getMessage()), t.getClass().getSimpleName());
            }
        }));
    }

    @PluginMethod
    public void disconnect(PluginCall call) {
        wanted = false;
        close();
        call.resolve();
    }

    private void close() {
        if (playerSub != null) playerSub.cancel();
        if (contextSub != null) contextSub.cancel();
        playerSub = null;
        contextSub = null;
        imageKey = "";
        libraryAsked = "";
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        remote = null;
    }

    @PluginMethod
    public void play(PluginCall call) {
        String uri = call.getString("uri", "");
        if (!uri.matches("spotify:(track|album|artist|playlist):[A-Za-z0-9]{10,40}")) {
            call.reject("Not a Spotify link", "BadUri");
            return;
        }
        if (!connected()) {
            call.reject("Not connected to Spotify", "NotConnected");
            return;
        }
        done(remote.getPlayerApi().play(uri), call);
    }

    @PluginMethod
    public void pause(PluginCall call) {
        if (connected()) done(remote.getPlayerApi().pause(), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    @PluginMethod
    public void resume(PluginCall call) {
        if (connected()) done(remote.getPlayerApi().resume(), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    @PluginMethod
    public void next(PluginCall call) {
        if (connected()) done(remote.getPlayerApi().skipNext(), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    @PluginMethod
    public void previous(PluginCall call) {
        if (connected()) done(remote.getPlayerApi().skipPrevious(), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    private void done(CallResult<Empty> result, PluginCall call) {
        result.setResultCallback(e -> call.resolve())
              .setErrorCallback(t -> call.reject(String.valueOf(t.getMessage()), t.getClass().getSimpleName()));
    }

    private void subscribe() {
        if (playerSub != null) playerSub.cancel();
        if (contextSub != null) contextSub.cancel();
        playerSub = remote.getPlayerApi().subscribeToPlayerState();
        playerSub.setEventCallback(this::emit);
        contextSub = remote.getPlayerApi().subscribeToPlayerContext();
        contextSub.setEventCallback(c -> {
            contextTitle = c == null || c.title == null ? "" : c.title;
            if (last != null) emit(last);
        });
    }

    private void connection(boolean on, Throwable t) {
        JSObject r = new JSObject();
        r.put("connected", on);
        if (t != null) r.put("error", t.getClass().getSimpleName() + ": " + t.getMessage());
        notifyListeners("connection", r);
    }

    /**
     * Sends what's playing to the web app. The cover (large, for the full-screen player) goes once per
     * album in a separate "cover" event, and whether the song is in your Spotify library follows when
     * Spotify answers.
     */
    private void emit(PlayerState ps) {
        last = ps;
        JSObject r = new JSObject();
        Track t = ps == null ? null : ps.track;
        if (t == null) {
            r.put("active", false);
            notifyListeners("playerState", r);
            return;
        }
        r.put("active", true);
        r.put("name", t.name);
        r.put("artist", t.artist != null ? t.artist.name : "");
        r.put("artistUri", t.artist != null ? t.artist.uri : "");
        r.put("album", t.album != null ? t.album.name : "");
        r.put("albumUri", t.album != null ? t.album.uri : "");
        r.put("uri", t.uri);
        r.put("duration", t.duration);
        r.put("position", ps.playbackPosition);
        r.put("paused", ps.isPaused);
        r.put("context", contextTitle);
        if (ps.playbackOptions != null) {
            r.put("shuffle", ps.playbackOptions.isShuffling);
            r.put("repeat", ps.playbackOptions.repeatMode);   // 0 off, 1 this song, 2 all
        }
        if (ps.playbackRestrictions != null) {
            r.put("canNext", ps.playbackRestrictions.canSkipNext);
            r.put("canPrev", ps.playbackRestrictions.canSkipPrev);
            r.put("canSeek", ps.playbackRestrictions.canSeek);
            r.put("canShuffle", ps.playbackRestrictions.canToggleShuffle);
            r.put("canRepeat", ps.playbackRestrictions.canRepeatContext || ps.playbackRestrictions.canRepeatTrack);
        }
        String key = t.imageUri != null ? t.imageUri.raw : "";
        r.put("imageKey", key);
        if (t.uri != null && t.uri.equals(savedUri)) {
            r.put("saved", saved);
            r.put("canSave", canSave);
        }
        notifyListeners("playerState", r);
        if (!connected()) return;
        if (!key.isEmpty() && !key.equals(imageKey)) {
            imageKey = key;
            remote.getImagesApi().getImage(t.imageUri, Image.Dimension.LARGE).setResultCallback(bmp -> {
                JSObject c = new JSObject();
                c.put("key", key);
                c.put("data", dataUrl(bmp));
                notifyListeners("cover", c);
            });
        }
        if (t.uri != null && !t.uri.equals(libraryAsked)) {
            libraryAsked = t.uri;
            remote.getUserApi().getLibraryState(t.uri).setResultCallback(ls -> {
                savedUri = ls.uri;
                saved = ls.isAdded;
                canSave = ls.canAdd;
                if (last == ps) emit(ps);
            });
        }
    }

    @PluginMethod
    public void seek(PluginCall call) {
        if (connected()) done(remote.getPlayerApi().seekTo(call.getLong("ms", 0L)), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    @PluginMethod
    public void shuffle(PluginCall call) {
        if (connected()) done(remote.getPlayerApi().setShuffle(Boolean.TRUE.equals(call.getBoolean("on", false))), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    @PluginMethod
    public void repeat(PluginCall call) {
        int mode = call.getInt("mode", 0);
        if (mode < 0 || mode > 2) mode = 0;
        if (connected()) done(remote.getPlayerApi().setRepeat(mode), call); else call.reject("Not connected to Spotify", "NotConnected");
    }

    /** Adds the song to (or removes it from) your Liked Songs in Spotify. */
    @PluginMethod
    public void save(PluginCall call) {
        String uri = call.getString("uri", "");
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", true));
        if (!uri.matches("spotify:track:[A-Za-z0-9]{10,40}")) {
            call.reject("Not a Spotify song", "BadUri");
            return;
        }
        if (!connected()) {
            call.reject("Not connected to Spotify", "NotConnected");
            return;
        }
        CallResult<Empty> r = on ? remote.getUserApi().addToLibrary(uri) : remote.getUserApi().removeFromLibrary(uri);
        r.setResultCallback(e -> {
            savedUri = uri;
            saved = on;
            if (last != null) emit(last);
            call.resolve();
        }).setErrorCallback(t -> call.reject(String.valueOf(t.getMessage()), t.getClass().getSimpleName()));
    }

    private static String dataUrl(Bitmap bmp) {
        if (bmp == null) return "";
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.JPEG, 82, out);
        return "data:image/jpeg;base64," + Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
    }

    // Spotify asks apps to let go of the connection while in the background.
    @Override
    protected void handleOnStop() {
        boolean keep = wanted;
        close();
        wanted = keep;
        super.handleOnStop();
    }

    @Override
    protected void handleOnStart() {
        super.handleOnStart();
        if (wanted && params != null && !authorizing) open(null);
    }
}
