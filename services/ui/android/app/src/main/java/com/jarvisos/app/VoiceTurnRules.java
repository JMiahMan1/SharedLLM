package com.jarvisos.app;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * The rules of one spoken turn on the phone: when speech starts, when the turn
 * is over, when a reply may be interrupted, how a long answer is cut into
 * speakable pieces, and what is said when there is nothing else to say.
 *
 * <p>Plain Java, JVM-tested: the service that runs this is Android-bound, the
 * numbers that decide a turn are not. The households' Echo satellites use
 * 16 kHz mono, 30 ms frames and a 300 ms speech start; a phone is held closer
 * to the mouth and its microphone is noisier, so the frame is 20 ms and the
 * floor adapts to the room instead of being a fixed number.
 */
public final class VoiceTurnRules {

    /** 16 kHz mono PCM16: what Whisper takes and what an Echo satellite streams. */
    public static final int RATE = 16000;
    public static final int BYTES_PER_SAMPLE = 2;
    public static final int FRAME_MS = 20;
    public static final int FRAME_BYTES = RATE * BYTES_PER_SAMPLE * FRAME_MS / 1000; // 640

    /** Speech must last this long before the turn is believed: a cough is not a question. */
    public static final int SPEECH_START_MS = 200;
    /** This much quiet ends the turn: short enough to feel instant, long enough for a comma. */
    public static final int SILENCE_END_MS = 700;
    /** Less speech than this in the whole turn is noise, so nothing is sent. */
    public static final int MIN_SPEECH_MS = 300;
    /** Nobody holds the button longer than this, and the server takes 15 s too. */
    public static final int MAX_TURN_MS = 15000;
    /** While a reply is playing, this much sustained loudness means "stop talking". */
    public static final int BARGE_IN_MS = 250;
    /** A room's own noise floor is tracked slowly, and never above this. */
    public static final int FLOOR_CEILING = 4000;
    /** Below this, nothing is speech no matter how quiet the room is. */
    public static final int MIN_SPEECH_RMS = 220;

    /** Why the phone is about to say something anyway. */
    public enum Ack {
        /** The microphone opened and the turn has not ended yet. */
        LISTENING,
        /** A recording is with the server. */
        THINKING,
        /** A long wait, said once so the silence is explained. */
        SLOW,
        /** The turn ended with no words in it. */
        EMPTY,
        /** The house could not be reached at all. */
        OFFLINE,
        /** The words came back but the answer could not be spoken. */
        NO_ANSWER,
    }

    /** Words that end a reply rather than start a new request. */
    private static final List<String> STOP_PHRASES = Arrays.asList(
            "stop", "stop it", "stop talking", "stop please", "cancel", "cancel that",
            "never mind", "nevermind", "be quiet", "quiet", "shut up", "enough", "that's enough");

    private VoiceTurnRules() {}

    /** Whether a frame is speech, judged against the room rather than a fixed number. */
    public static boolean speech(int rms, int floor) {
        return rms > Math.max(MIN_SPEECH_RMS, (int) (floor * 2.5));
    }

    /** The room's noise floor after one frame; it follows noise down and up slowly. */
    public static int nextFloor(int floor, int rms) {
        // Up an eighth of the way, but down a quarter: a floor that climbed while
        // the kettle boiled has to fall back quickly, or the next quiet question
        // is missed while it forgets the noise.
        int moved = rms > floor ? floor + (rms - floor) / 8 : floor - (floor - rms) / 4;
        if (moved < 0) return 0;
        return Math.min(moved, FLOOR_CEILING);
    }

    /** Whether the turn is over: enough speech and then quiet, or the cap reached. */
    public static boolean endOfTurn(int speechMs, int silenceMs, int totalMs) {
        if (totalMs >= MAX_TURN_MS) return true;
        return speechMs >= MIN_SPEECH_MS && silenceMs >= SILENCE_END_MS;
    }

    /** Whether a turn heard nothing worth sending. */
    public static boolean nothingHeard(int speechMs) {
        return speechMs < MIN_SPEECH_MS;
    }

    /** Whether sustained loudness during playback should cut the reply off. */
    public static boolean bargeIn(int loudMs) {
        return loudMs >= BARGE_IN_MS;
    }

    /** Lowercase, no punctuation, single spaces: what wake and stop matching work on. */
    public static String normalize(String text) {
        if (text == null) return "";
        String lowered = text.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9' ]+", " ").trim();
        return lowered.replaceAll("\\s+", " ");
    }

    /** Whether a bare "stop" (and only a bare one) means cut the reply off. */
    public static boolean stopPhrase(String text) {
        String said = normalize(text);
        return !said.isEmpty() && STOP_PHRASES.contains(said);
    }

    /**
     * Whether a transcript begins with the wake phrase, so "Hey Jarvis, what's
     * the weather" and a bare "Hey Jarvis" both count, while "Jarvis said..."
     * does not.
     */
    public static boolean wake(String text, String phrase) {
        String said = normalize(text);
        String wanted = normalize(phrase);
        if (said.isEmpty() || wanted.isEmpty()) return false;
        return said.equals(wanted) || said.startsWith(wanted + " ");
    }

    /** The transcript with its wake phrase removed, or unchanged when it is not there. */
    public static String withoutWake(String text, String phrase) {
        String wanted = normalize(phrase);
        if (wanted.isEmpty() || text == null) return text == null ? "" : text.trim();
        String said = normalize(text);
        if (!said.startsWith(wanted)) return text.trim();
        // Cut by words rather than characters: the transcript keeps its own case.
        String[] words = text.trim().split("\\s+");
        int wantedWords = wanted.split(" ").length;
        if (words.length <= wantedWords) return "";
        return String.join(" ", Arrays.copyOfRange(words, wantedWords, words.length)).replaceAll("^[,;:.!?]+", "").trim();
    }

    /**
     * A reply split into pieces that can be spoken as they arrive: at sentence
     * ends, with very short fragments joined forward ("Yes. It is." reads as one
     * breath) and a long sentence cut at a comma or a space before it runs past
     * {@code maxChars}.
     */
    public static List<String> sentences(String text, int maxChars) {
        List<String> out = new ArrayList<>();
        if (text == null) return out;
        String rest = text.trim();
        if (rest.isEmpty()) return out;
        int limit = Math.max(40, maxChars);

        StringBuilder piece = new StringBuilder();
        for (int i = 0; i < rest.length(); i++) {
            char c = rest.charAt(i);
            piece.append(c);
            boolean end = c == '.' || c == '!' || c == '?' || c == '\n';
            if (!end) continue;
            // "3.5" and "Dr." are not sentence ends worth stopping at.
            if (c == '.' && i + 1 < rest.length() && Character.isDigit(rest.charAt(i + 1))) continue;
            String candidate = piece.toString().trim();
            piece.setLength(0);
            if (candidate.isEmpty()) continue;
            // A three-word fragment is joined to the next sentence so the speech does not stammer.
            if (candidate.length() < 18 && i + 1 < rest.length()) {
                piece.append(candidate).append(' ');
                continue;
            }
            addSplit(out, candidate, limit);
        }
        String tail = piece.toString().trim();
        if (!tail.isEmpty()) addSplit(out, tail, limit);
        return out;
    }

    private static void addSplit(List<String> out, String sentence, int limit) {
        String rest = sentence;
        while (rest.length() > limit) {
            int cut = rest.lastIndexOf(", ", limit);
            if (cut < limit / 2) cut = rest.lastIndexOf(' ', limit);
            if (cut <= 0) cut = limit;
            out.add(rest.substring(0, cut).trim());
            rest = rest.substring(cut).trim();
        }
        if (!rest.isEmpty()) out.add(rest);
    }

    /**
     * What the phone says so a turn is never silent. The big three are all
     * criticised for "ghosting" (an indicator, then nothing); every path here
     * ends in words.
     */
    public static String ack(Ack reason) {
        switch (reason) {
            case LISTENING:
                return "I'm listening.";
            case THINKING:
                return "One moment.";
            case SLOW:
                return "Still thinking.";
            case EMPTY:
                return "I didn't catch that.";
            case OFFLINE:
                return "I can't reach the house right now.";
            case NO_ANSWER:
                return "I heard you but I can't answer out loud just now.";
            default:
                return "One moment.";
        }
    }

    /** How long a reply may be before the phone stops waiting for the rest of it. */
    public static int replyTimeoutMs() {
        // The Echo pipeline records 12 s typical and 33 s worst for a model turn.
        return 60000;
    }
}
