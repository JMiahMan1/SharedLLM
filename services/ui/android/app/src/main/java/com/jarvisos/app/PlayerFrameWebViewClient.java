package com.jarvisos.app;

import android.net.Uri;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebViewClient;

/**
 * Capacitor's WebView client, except that a navigation inside one of the chat's
 * video player iframes stays in that iframe ({@link PlayerFrameRules}). Every
 * other navigation, and every main-frame one, is Capacitor's: the app's own
 * pages load, anything external opens in the phone's browser.
 */
class PlayerFrameWebViewClient extends BridgeWebViewClient {

    PlayerFrameWebViewClient(Bridge bridge) {
        super(bridge);
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        Uri url = request.getUrl();
        if (PlayerFrameRules.keepInFrame(request.isForMainFrame(), url.getScheme(), url.getHost())) {
            return false;
        }
        return super.shouldOverrideUrlLoading(view, request);
    }
}
