"""The phone's native turn must not burn the CPU, delay the answer, or die in the
matrix of "a background component may not start a microphone service".

Three bugs had shipped together, none visible from the web bundle:

* the speaker thread looped `while (alive || !interrupted)`, so once the service
  went away the loop never waited again - a busy thread at 100% CPU, and a phone
  that eventually reported "Jarvis OS isn't responding";
* the "one moment" ack was a `Thread.sleep(900)` submitted *before* the chat call
  on the same single-thread executor, so the answer was always 900 ms late and
  the ack always fired first;
* the widget and the shortcut broadcast asked a background receiver to start the
  microphone foreground service, which Android 12+ refuses.
"""
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "services/ui/android/app/src/main/java/com/jarvisos/app"
SERVICE = (APP / "AssistService.java").read_text()
WIDGET = (APP / "VoiceAssistWidget.java").read_text()
RECEIVER = (APP / "AssistTriggerReceiver.java").read_text()


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def test_the_speaker_thread_only_lives_while_the_service_does():
    assert "while (alive.get() && !Thread.currentThread().isInterrupted())" in SERVICE
    assert "alive.get() || !Thread.currentThread().isInterrupted()" not in SERVICE


def test_the_speaker_is_started_by_a_turn_not_by_oncreate():
    """Created before `alive` was set, the loop spun from the first millisecond."""
    assert "startSpeaker();" not in _between(SERVICE, "public void onCreate()", "public int onStartCommand")
    assert "startSpeaker();" in _between(SERVICE, "public int onStartCommand", "public void onDestroy")


def test_destroying_the_service_wakes_and_stops_the_speaker():
    destroy = _between(SERVICE, "public void onDestroy()", "public IBinder onBind")
    assert "sentenceLock.notifyAll();" in destroy, "a thread waiting on the lock never sees the service go away"
    assert "speaker.interrupt();" in destroy
    assert "ack.shutdownNow();" in destroy


def test_the_one_moment_ack_no_longer_blocks_the_answer():
    ask = _between(SERVICE, "private void ask(String question)", "/** One sentence ready to say")

    assert "Thread.sleep(900)" not in ask, "sleeping on the chat executor delayed every answer by 900 ms"
    assert "ack.schedule(" in ask
    assert "900, TimeUnit.MILLISECONDS" in ask
    # Cancelled the moment an answer exists, so a fast answer is not preceded by it.
    assert ask.count("cancelAck();") >= 3


def test_the_widget_opens_the_app_instead_of_reaching_for_the_microphone_in_the_background():
    assert "PendingIntent.getActivity(" in WIDGET
    assert "AssistActivity.class" in WIDGET
    assert "getBroadcast(" not in WIDGET


def test_a_shortcut_that_cannot_start_the_service_still_reaches_the_assistant():
    assert RECEIVER.count("try {") >= 2, "both entry points must survive a refused foreground service"
    assert RECEIVER.count("startAssist(context)") >= 2
    assert "AssistActivity.class" in RECEIVER


VOICE_CLIENT = (APP / "VoiceClient.java").read_text()


def test_a_fast_path_answer_is_spoken_instead_of_dropped():
    """The time, the weather and other fast paths answer once, with the words in
    ``message.content`` and no ``reply``. Reading only ``reply`` meant the phone
    transcribed the question, received a 200 and stayed silent."""
    chat = _between(VOICE_CLIENT, "void chat(", "byte[] speak(")

    assert 'optJSONObject("message")' in chat
    assert 'optString("content", "")' in chat
    assert "if (!spoken)" in chat, "the finished object's words must be spoken only when nothing streamed"
    assert "spoken = true;" in chat
