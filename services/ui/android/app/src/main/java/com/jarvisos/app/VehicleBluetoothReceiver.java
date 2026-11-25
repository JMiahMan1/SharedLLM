package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Bluetooth connections coming and going, for telling which linked vehicle a
 * trip is in ({@link VehicleBluetooth}). Declared in the manifest: Android
 * delivers ACL connect and disconnect broadcasts to it even while the app is
 * not running (they are exempt from the background broadcast limits), and
 * only to an app holding Nearby devices (BLUETOOTH_CONNECT) on Android 12+.
 */
public class VehicleBluetoothReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        VehicleBluetooth.onBroadcast(context, intent);
    }
}
