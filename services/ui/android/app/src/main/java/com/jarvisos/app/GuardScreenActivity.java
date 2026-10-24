package com.jarvisos.app;

import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;

/**
 * What the screen guard shows instead of a blocked screen.
 *
 * Tamper mode: "A parent needs to sign in" with a parent's own Jarvis login,
 * checked by the server (only this child's parents, five tries then a wait).
 * Locked mode: why the phone is locked (bedtime, school, the day's time, a
 * pause), a way to ask for more time in Jarvis, and the same parent login.
 *
 * Built in code, no layout files, so it stays one small class.
 */
public class GuardScreenActivity extends ComponentActivity {

    static final String EXTRA_MODE = "mode";
    static final String MODE_TAMPER = "tamper";
    static final String MODE_LOCKED = "locked";
    /** One app is off (blocked by a parent, or its own minutes are up); the rest of the phone works. */
    static final String MODE_APP = "app";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        boolean tamper = MODE_TAMPER.equals(getIntent().getStringExtra(EXTRA_MODE));
        boolean appOff = MODE_APP.equals(getIntent().getStringExtra(EXTRA_MODE));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(64, 64, 64, 64);
        root.setBackgroundColor(Color.parseColor("#030711"));

        TextView title = text(tamper ? "A parent needs to sign in" : appOff ? "That app is off for now" : lockedTitle(), 24);
        TextView body = text(tamper
                ? "Removing Jarvis or switching off its protection needs one of your parents' logins."
                : appOff
                ? "Your parents turned this app off, or its time is used up for today. Everything else still works."
                : "Calls, texts and Jarvis still work. You can ask a parent for more time in Jarvis.", 16);
        body.setTextColor(Color.parseColor("#94A3B8"));

        EditText user = new EditText(this);
        user.setHint("Parent's username");
        user.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        EditText pass = new EditText(this);
        pass.setHint("Parent's password");
        pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        for (EditText field : new EditText[] {user, pass}) {
            field.setTextColor(Color.WHITE);
            field.setHintTextColor(Color.parseColor("#64748B"));
        }
        TextView error = text("", 14);
        error.setTextColor(Color.parseColor("#FCD34D"));

        Button unlock = new Button(this);
        unlock.setText("Parent unlock");
        unlock.setOnClickListener(v -> {
            unlock.setEnabled(false);
            String u = user.getText().toString().trim();
            String p = pass.getText().toString();
            new Thread(() -> {
                String problem = GuardApi.parentUnlock(this, u, p);
                runOnUiThread(() -> {
                    unlock.setEnabled(true);
                    pass.setText("");
                    if (problem == null) {
                        finish();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });

        root.addView(title);
        root.addView(body);
        if (!tamper) {
            Button ask = new Button(this);
            ask.setText("Ask for more time");
            ask.setOnClickListener(v -> {
                // Health holds the "Ask for more time" card.
                startActivity(new Intent(this, MainActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                        .putExtra(PushNotifier.EXTRA_OPEN_PATH, "/health"));
                finish();
            });
            root.addView(ask);
        }
        // No connection: a parent's offline code works instead of their login.
        EditText offline = new EditText(this);
        offline.setHint("Parent's offline code");
        offline.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        offline.setTextColor(Color.WHITE);
        offline.setHintTextColor(Color.parseColor("#64748B"));
        Button useCode = new Button(this);
        useCode.setText("Use offline code");
        useCode.setOnClickListener(v -> {
            useCode.setEnabled(false);
            String code = offline.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.offlineUnlock(this, code);
                runOnUiThread(() -> {
                    useCode.setEnabled(true);
                    offline.setText("");
                    if (problem == null) {
                        finish();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });

        root.addView(user);
        root.addView(pass);
        root.addView(unlock);
        root.addView(offline);
        root.addView(useCode);
        root.addView(error);
        setContentView(root, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // Back (button or gesture) would return to the blocked screen; home is the way out.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                finish();
            }
        });
    }

    private String lockedTitle() {
        switch (GuardApi.lockReason(this)) {
            case "bedtime":
                return "It's bedtime";
            case "school":
                return "It's school time";
            case "paused":
                return "A parent paused this phone";
            default:
                return "That's today's screen time";
        }
    }

    private TextView text(String value, int sizeSp) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sizeSp);
        view.setTextColor(Color.WHITE);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, 16, 0, 16);
        return view;
    }
}
