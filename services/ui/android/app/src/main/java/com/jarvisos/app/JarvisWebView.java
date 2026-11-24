package com.jarvisos.app;

import android.content.Context;
import android.util.AttributeSet;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;

import androidx.core.view.inputmethod.EditorInfoCompat;
import androidx.core.view.inputmethod.InputConnectionCompat;

import com.getcapacitor.CapacitorWebView;

/**
 * Capacitor's WebView, plus the keyboard's GIFs and stickers in chat boxes.
 *
 * Android's WebView leaves EditorInfo.contentMimeTypes empty, so a keyboard
 * cannot insert a picture anywhere in the app and says so. For a message box
 * ({@link KeyboardMedia#acceptsMedia}) this names the picture types and wraps
 * the WebView's own InputConnection: text goes on exactly as before, and a
 * picture the keyboard commits goes to {@link KeyboardMediaPlugin}. Swapped in
 * for Capacitor's view by res/layout/capacitor_bridge_layout_main.xml.
 */
public class JarvisWebView extends CapacitorWebView {
    public JarvisWebView(Context context, AttributeSet attrs) {
        super(context, attrs);
    }

    @Override
    @SuppressWarnings("deprecation")
    public InputConnection onCreateInputConnection(EditorInfo outAttrs) {
        InputConnection connection = super.onCreateInputConnection(outAttrs);
        if (connection == null || !KeyboardMedia.acceptsMedia(outAttrs.inputType)) return connection;
        EditorInfoCompat.setContentMimeTypes(outAttrs, KeyboardMedia.MIME_TYPES);
        return InputConnectionCompat.createWrapper(connection, outAttrs,
                (info, flags, opts) -> KeyboardMediaPlugin.receive(getContext(), info, flags));
    }
}
