from services.video_intelligence import _google_speech_model, _resolve_transcript_languages


def test_bangla_ui_language_uses_bangladesh_locale():
    assert _resolve_transcript_languages("bn-BD") == ("bn-BD", [])


def test_auto_language_detection_includes_bangladesh_bangla(monkeypatch):
    monkeypatch.setenv("VIDEO_TRANSCRIBE_LANGUAGE_CODE", "en-US")
    primary, alternatives = _resolve_transcript_languages("auto")

    assert primary == "en-US"
    assert "bn-BD" in alternatives
    assert "bn-IN" not in alternatives


def test_old_bn_in_jobs_fall_back_to_compatible_model(monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_MODEL", "latest_long")
    assert _google_speech_model("bn-IN") == "default"


def test_bn_bd_keeps_long_form_model(monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_MODEL", "latest_long")
    assert _google_speech_model("bn-BD") == "latest_long"
