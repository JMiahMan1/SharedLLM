from services.execution.handlers.media import detect_media_type


def test_detect_music_default():
    assert detect_media_type("The Beatles") == "music"
    assert detect_media_type("play some jazz") == "music"
    assert detect_media_type("") == "music"

def test_detect_music_hint():
    assert detect_media_type("anything", "music") == "music"

def test_detect_video_url():
    assert detect_media_type("https://youtube.com/watch?v=abc123") == "video"
    assert detect_media_type("https://youtu.be/abc123") == "video"
    assert detect_media_type("https://vimeo.com/12345") == "video"
    assert detect_media_type("https://rumble.com/video") == "video"

def test_detect_video_hint():
    assert detect_media_type("anything", "video") == "video"

def test_detect_podcast_url():
    assert detect_media_type("https://itunes.apple.com/podcast/xyz") == "podcast"

def test_detect_podcast_keywords():
    assert detect_media_type("the daily podcast episode 5") == "podcast"

def test_detect_url_generic():
    assert detect_media_type("https://example.com/stream.mp3") == "url"
    assert detect_media_type("http://192.168.1.100:8080/audio.wav") == "url"

def test_detect_audiobook_keywords():
    assert detect_media_type("audiobook The Hobbit narrated by Andy Serkis") == "audiobook"
    assert detect_media_type("read by Stephen Fry chapter 3") == "audiobook"
