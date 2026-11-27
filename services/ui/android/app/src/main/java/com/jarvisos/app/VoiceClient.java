package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.util.Base64;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

/**
 * The three things the server offers a voice client (services/gateway/main.py):
 * {@code /api/voice/listen} (a recording becomes words), {@code /api/chat} with
 * a phone context and {@code speech_stream} (whole sentences of the answer as
 * they are written), and {@code /api/voice/speak} (a sentence in this person's
 * own voice). Nothing here talks to the Echo path, which is internal-only.
 *
 * Plain OkHttp on purpose: the credentials the phone already stores are read
 * straight from the mirrored SharedPreferences, so this works while the WebView
 * is asleep, and there is no bridge in the audio path.
 */
final class VoiceClient {

    private static final String TAG = "VoiceClient";
    static final String PREFS = "jarvis_widget";
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");

    /** One sentence of the answer, as it was spoken, plus the whole reply at the end. */
    interface Stream {
        void sentence(String text);
        void done(String reply);
        void failed(String reason);
    }

    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .build();
    private final Context context;

    VoiceClient(Context context) {
        this.context = context.getApplicationContext();
    }

    private SharedPreferences prefs() {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Where this phone's server is, or empty when it has not signed in yet. */
    String base() {
        String url = prefs().getString("jarvis_server_url", "");
        return url == null ? "" : url.trim().replaceAll("/+$", "");
    }

    String token() {
        String key = prefs().getString("jarvis_api_key", "");
        return key == null ? "" : key.trim();
    }

    boolean signedIn() {
        return !base().isEmpty() && !token().isEmpty();
    }

    /** The person this phone belongs to, so the server can answer as them. */
    String person() {
        return prefs().getString("jarvis_username", "");
    }

    /** A recording of one turn becomes what was said. Empty when it heard nothing. */
    String listen(byte[] wav) throws IOException {
        JSONObject body = new JSONObject();
        try {
            body.put("audio_b64", Base64.getEncoder().encodeToString(wav));
            body.put("mime", "audio/wav");
        } catch (Exception e) {
            throw new IOException("could not package the recording", e);
        }
        JSONObject answer = post("/api/voice/listen", body);
        return answer.optString("text", "").trim();
    }

    /**
     * Ask the house, and hear the answer as it is written: the server gates whole
     * sentences (services/gateway/speech_stream.py) so this never speaks half a
     * clause.
     */
    void chat(String question, String conversationId, Stream stream) {
        JSONObject body = new JSONObject();
        try {
            body.put("query", question);
            body.put("stream", true);
            body.put("speech_stream", true);
            JSONObject voice = new JSONObject();
            voice.put("source", "android");
            voice.put("device_name", deviceName());
            voice.put("conversation_id", conversationId);
            body.put("voice_context", voice);
        } catch (Exception e) {
            stream.failed(e.getMessage());
            return;
        }
        Request request = new Request.Builder()
                .url(base() + "/api/chat")
                .header("Authorization", "Bearer " + token())
                .header("Accept", "application/x-ndjson")
                // The house answers voice before anything else, as it does for a satellite.
                .header("X-Request-Source", "voice/assist")
                .post(RequestBody.create(body.toString(), JSON))
                .build();
        try (Response response = http.newCall(request).execute()) {
            if (!response.isSuccessful() || response.body() == null) {
                stream.failed("HTTP " + response.code());
                return;
            }
            BufferedReader reader = new BufferedReader(response.body().charStream());
            String reply = "";
            boolean spoken = false;
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) continue;
                JSONObject chunk;
                try {
                    chunk = new JSONObject(line);
                } catch (Exception ignored) {
                    continue;          // a partial or an SSE comment: skip, never fail the turn
                }
                if (chunk.optBoolean("speech") && chunk.optJSONObject("message") != null) {
                    String said = chunk.optJSONObject("message").optString("content", "");
                    if (!said.trim().isEmpty()) {
                        stream.sentence(said.trim());
                        spoken = true;
                    }
                }
                if (chunk.optBoolean("done")) {
                    reply = chunk.optString("reply", reply);
                    String rest = chunk.optString("speech_rest", "");
                    if (!rest.trim().isEmpty()) {
                        stream.sentence(rest.trim());
                        spoken = true;
                    } else if (reply.trim().isEmpty()) {
                        // A fast path (the time, the weather, a sentence trigger) answers with one
                        // finished object: done true and the words in message.content, with no reply
                        // and no sentence envelopes. Without this the phone transcribed the question,
                        // got a 200 and stayed silent.
                        JSONObject message = chunk.optJSONObject("message");
                        String content = message == null ? "" : message.optString("content", "");
                        if (!content.trim().isEmpty()) {
                            reply = content.trim();
                            if (!spoken) {
                                stream.sentence(reply);
                                spoken = true;
                            }
                        }
                    }
                }
            }
            stream.done(reply);
        } catch (IOException e) {
            stream.failed(e.getMessage() == null ? "no connection" : e.getMessage());
        }
    }

    /** One sentence in this person's voice. Empty when the house cannot speak. */
    byte[] speak(String text) throws IOException {
        JSONObject body = new JSONObject();
        try {
            body.put("text", text);
        } catch (Exception e) {
            throw new IOException("could not package the line", e);
        }
        JSONObject answer = post("/api/voice/speak", body);
        String audio = answer.optString("audio_b64", "");
        return audio.isEmpty() ? new byte[0] : Base64.getDecoder().decode(audio);
    }

    private JSONObject post(String path, JSONObject body) throws IOException {
        Request request = new Request.Builder()
                .url(base() + path)
                .header("Authorization", "Bearer " + token())
                .post(RequestBody.create(body.toString(), JSON))
                .build();
        try (Response response = http.newCall(request).execute()) {
            String text = response.body() == null ? "" : response.body().string();
            if (!response.isSuccessful()) {
                String reason = "";
                try {
                    reason = new JSONObject(text).optString("detail", "");
                } catch (Exception ignored) {
                    // not JSON: the status is the only fact
                }
                throw new IOException(reason.isEmpty() ? "HTTP " + response.code() : reason);
            }
            try {
                return new JSONObject(text);
            } catch (Exception e) {
                throw new IOException("the house did not answer in JSON", e);
            }
        }
    }

    /** "Kaleb's Pixel 8" when the phone knows, else the model, else "this phone". */
    private String deviceName() {
        String owner = person();
        String model = (Build.MANUFACTURER + " " + Build.MODEL).trim();
        if (owner.isEmpty()) return model.isEmpty() ? "this phone" : model;
        return owner + "'s " + (model.isEmpty() ? "phone" : model);
    }

    static String newConversation() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 24);
    }

    static void log(String message) {
        Log.d(TAG, message);
    }

    /** Kept for callers that want to know whether the last failure was a sign-in one. */
    static boolean signInProblem(String reason) {
        return reason != null && reason.contains("Sign in");
    }
}
