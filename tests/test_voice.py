import base64
import io
import os
import struct
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from flex.config import Settings
from flex.ui.edge_overlay import FlexEdgeOverlay
from flex.ui.main_window import FlexMainWindow, RequestWorker
from flex.voice import (
    SelectionMonitorWorker,
    VOICE_SILENCE_TIMEOUT_SECONDS,
    _SELECTION_SCRIPT,
    _audio_rms,
    _decode_selection_text,
    _pcm_to_whisper_audio,
    _select_microphone_device,
    _transcribe_audio,
    SpeechRecognitionWorker,
    speak_text,
)


@pytest.fixture(autouse=True)
def avoid_starting_windows_selection_process(monkeypatch):
    monkeypatch.setattr(SelectionMonitorWorker, "start", lambda _self: None)


def test_overlay_voice_mode_submits_speech_and_requests_spoken_reply(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(FlexMainWindow, "_start_voice_listener", lambda _self: None)
    monkeypatch.setattr(RequestWorker, "start", lambda _self: None)
    settings = Settings(
        project_root=Path(tmp_path),
        browser_enabled=False,
        terminal_enabled=False,
        memory_enabled=False,
        automation_enabled=False,
    )
    window = FlexMainWindow(settings)
    overlay = window.edge_overlay
    overlay._visible = True
    overlay.show()
    app.processEvents()

    try:
        QTest.mouseClick(overlay.voice_button, Qt.MouseButton.LeftButton)
        assert window._voice_chat_enabled
        assert window.voice_button.isChecked()

        window._handle_voice_transcript("Tell me what time it is")
        worker = window._request_worker
        assert worker is not None
        assert worker.request == "Tell me what time it is"
        assert worker.speak_response
    finally:
        overlay.close()
        window.close()
        app.processEvents()


def test_voice_idle_sleep_command_and_voice_activity_wake(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(FlexMainWindow, "_start_voice_listener", lambda _self: None)
    settings = Settings(
        project_root=Path(tmp_path),
        browser_enabled=False,
        terminal_enabled=False,
        memory_enabled=False,
        automation_enabled=False,
    )
    window = FlexMainWindow(settings)
    overlay = window.edge_overlay

    try:
        window._set_voice_chat_enabled(True)
        assert window._voice_idle_timer.interval() == 20_000
        window._handle_voice_ready()
        assert window._voice_idle_timer.isActive()

        window._handle_voice_idle_timeout()
        assert overlay._eyes._expression == "sleep"

        window._handle_voice_activity()
        assert overlay._eyes._expression == "neutral"
        assert window._voice_idle_timer.isActive()

        window._handle_voice_transcript("Flex, to sleep!")
        assert window._request_worker is None
        assert overlay._eyes._expression == "sleep"
        window._handle_voice_activity()
        assert overlay._eyes._expression == "neutral"

        for phrase in ("sleep", "Flex sleep", "go sleep mode", "Flex, go to sleep mode"):
            window._handle_voice_transcript(phrase)
            assert window._request_worker is None
            assert overlay._eyes._expression == "sleep"
            window._handle_voice_transcript("wake")
            assert window._request_worker is None
            assert overlay._eyes._expression == "neutral"
    finally:
        overlay.close()
        window.close()
        app.processEvents()


def test_clicking_sleeping_notch_wakes_and_preserves_voice_mode(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(FlexMainWindow, "_start_voice_listener", lambda _self: None)
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )
    overlay = window.edge_overlay
    overlay._visible = True
    overlay.show()
    app.processEvents()

    try:
        window._set_voice_chat_enabled(True)
        window._handle_voice_idle_timeout()
        assert overlay._eyes._expression == "sleep"

        QTest.mouseClick(overlay._eyes, Qt.MouseButton.LeftButton)
        app.processEvents()

        assert overlay._eyes._expression == "neutral"
        assert window._voice_chat_enabled
        assert window._voice_idle_timer.isActive()
        assert overlay._eye_mode
    finally:
        overlay.close()
        window.close()
        app.processEvents()


def test_text_submission_wakes_notch_and_thanks_shows_happy_expression(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(FlexMainWindow, "_start_voice_listener", lambda _self: None)
    monkeypatch.setattr(RequestWorker, "start", lambda _self: None)
    settings = Settings(
        project_root=Path(tmp_path),
        browser_enabled=False,
        terminal_enabled=False,
        memory_enabled=False,
        automation_enabled=False,
    )
    window = FlexMainWindow(settings)
    overlay = window.edge_overlay

    try:
        window._set_voice_chat_enabled(True)
        assert window._voice_idle_timer.interval() == 20_000
        overlay.set_expression("sleep")
        window.input_box.setText("Thanks, I appreciate it")
        window._handle_submit()

        assert overlay._eyes._expression == "happy"
        assert window._happy_expression_timer.interval() == 4_000
        assert window._happy_expression_timer.isActive()
        assert not window._voice_idle_timer.isActive()
        window._happy_expression_timer.timeout.emit()
        assert overlay._eyes._expression == "neutral"
    finally:
        overlay.close()
        window.close()
        app.processEvents()


def test_text_admiration_triggers_happy_reaction_without_voice_mode(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(RequestWorker, "start", lambda _self: None)
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        assert not window._voice_chat_enabled
        window.input_box.setText("Flex, you did the task perfectly!")
        window._handle_submit()

        assert window.edge_overlay._eyes._expression == "happy"
        assert window._happy_expression_timer.interval() == 4_000
        assert window._happy_expression_timer.isActive()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_assistant_thank_you_response_triggers_happy_reaction(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        window._handle_response("Thank you! I'm here to help.")

        assert window.edge_overlay._eyes._expression == "happy"
        assert window._happy_expression_timer.interval() == 4_000
        assert window._happy_expression_timer.isActive()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


@pytest.mark.parametrize(
    "utterance",
    ("Make my PC sleep", "Turn on sleep mode in my computer"),
)
def test_pc_sleep_requests_are_not_notch_sleep_commands(
    monkeypatch,
    tmp_path,
    utterance,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(FlexMainWindow, "_start_voice_listener", lambda _self: None)
    monkeypatch.setattr(RequestWorker, "start", lambda _self: None)
    settings = Settings(
        project_root=Path(tmp_path),
        browser_enabled=False,
        terminal_enabled=False,
        memory_enabled=False,
        automation_enabled=False,
    )
    window = FlexMainWindow(settings)

    try:
        window._set_voice_chat_enabled(True)
        window._handle_voice_transcript(utterance)

        assert window._request_worker is not None
        assert window._request_worker.request == utterance
        assert window.edge_overlay._eyes._expression == "neutral"
    finally:
        window.edge_overlay.close()
        window.close()
        app.processEvents()


def test_audio_rms_measures_pcm_level():
    silence = struct.pack("<4h", 0, 0, 0, 0)
    loud = struct.pack("<4h", 16384, -16384, 16384, -16384)

    assert _audio_rms(silence) == 0
    assert _audio_rms(loud) == 0.5


def test_select_microphone_prefers_droidcam():
    sounddevice = SimpleNamespace(
        query_devices=lambda *args: (
            [
                {"name": "Built-in Microphone", "max_input_channels": 1},
                {"name": "Microphone (DroidCam Audio)", "max_input_channels": 1},
                {"name": "Speakers", "max_input_channels": 0},
            ]
            if not args
            else {"name": args[0], "max_input_channels": 1}
        ),
        default=SimpleNamespace(device=(0, 2)),
    )

    assert _select_microphone_device(sounddevice) == 1


def test_transcribe_audio_uses_local_whisper_model(monkeypatch):
    audio = struct.pack("<4h", 123, -123, 456, -456)
    segments = [SimpleNamespace(text=" Hello "), SimpleNamespace(text="there.")]
    model_calls = []

    def fake_transcribe(samples, **kwargs):
        model_calls.append(kwargs)
        return iter(segments), SimpleNamespace()

    model = SimpleNamespace(
        transcribe=fake_transcribe,
    )
    samples_seen = []

    def fake_convert(pcm, sample_rate):
        samples_seen.append((pcm, sample_rate))
        return [0.1, -0.1]

    monkeypatch.setattr("flex.voice._pcm_to_whisper_audio", fake_convert)

    transcript = _transcribe_audio(audio, 44100, model)

    assert transcript == "Hello there."
    assert samples_seen == [(audio, 44100)]
    assert model_calls == [
        {
            "language": "en",
            "beam_size": 5,
            "condition_on_previous_text": False,
        }
    ]


def test_pcm_to_whisper_audio_resamples_droidcam_input():
    audio = struct.pack("<" + "h" * 44100, *([1200, -1200] * 22050))

    samples = _pcm_to_whisper_audio(audio, 44100)

    assert samples.shape == (16000,)
    assert samples.dtype.name == "float32"


def test_speech_worker_reports_local_model_errors(monkeypatch):
    def fail_to_load_model():
        raise RuntimeError("Could not load local model")

    monkeypatch.setattr("flex.voice._load_whisper_model", fail_to_load_model)
    worker = SpeechRecognitionWorker()
    errors = []
    worker.error_occurred.connect(errors.append)

    worker.run()

    assert len(errors) == 1
    assert "local model" in errors[0]


def test_voice_request_speaks_response(monkeypatch):
    spoken = []
    monkeypatch.setattr(
        "flex.ui.main_window.speak_text",
        lambda text: spoken.append(text),
    )
    worker = RequestWorker(
        SimpleNamespace(process_request=lambda _request: "Here is the answer."),
        "What is the answer?",
        speak_response=True,
    )
    responses = []
    worker.response_ready.connect(responses.append)

    worker.run()

    assert responses == ["Here is the answer."]
    assert spoken == ["Here is the answer."]


def test_request_worker_uses_voice_response_mode(monkeypatch):
    spoken = []
    calls = []

    class VoiceAgent:
        def process_request(self, request):
            raise AssertionError("Voice mode should use the concise response path.")

        def process_voice_request(self, request):
            calls.append(request)
            return "Done."

    monkeypatch.setattr(
        "flex.ui.main_window.speak_text",
        lambda text: spoken.append(text),
    )
    worker = RequestWorker(VoiceAgent(), "Open Notepad", speak_response=True)
    responses = []
    worker.response_ready.connect(responses.append)

    worker.run()

    assert calls == ["Open Notepad"]
    assert responses == ["Done."]
    assert spoken == ["Done."]


def test_failed_voice_request_speaks_short_failure(monkeypatch):
    spoken = []
    errors = []

    class FailedAgent:
        def process_request(self, _request):
            raise RuntimeError("detailed failure")

    monkeypatch.setattr(
        "flex.ui.main_window.speak_text",
        lambda text: spoken.append(text),
    )
    worker = RequestWorker(FailedAgent(), "Do something", speak_response=True)
    worker.error_occurred.connect(errors.append)

    worker.run()

    assert errors == ["detailed failure"]
    assert spoken == ["I couldn't complete that request."]


def test_speak_text_uses_windows_system_speech(monkeypatch):
    calls = []
    monkeypatch.setattr("flex.voice._powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(
        "flex.voice.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(returncode=0, stderr=""),
    )

    speak_text("Hello, I am FLEX.")

    args, kwargs = calls[0]
    assert args[0][:4] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]
    assert "System.Speech" in args[0][-1]
    assert "Speak($text)" in args[0][-1]
    assert kwargs["input"] == "Hello, I am FLEX."
    assert kwargs["timeout"] == 120


def test_voice_recognition_waits_two_seconds_of_silence():
    assert VOICE_SILENCE_TIMEOUT_SECONDS == 2


def test_agent_selection_monitor_uses_accessibility_without_clipboard():
    assert "AutomationElement]::FocusedElement" in _SELECTION_SCRIPT
    assert "TextPattern]::Pattern" in _SELECTION_SCRIPT
    assert "GetSelection()" in _SELECTION_SCRIPT
    assert "'AVAILABLE'" in _SELECTION_SCRIPT
    assert "'UNSUPPORTED'" in _SELECTION_SCRIPT
    assert "Get-Clipboard" not in _SELECTION_SCRIPT
    assert "Set-Clipboard" not in _SELECTION_SCRIPT
    assert "'CLEAR'" in _SELECTION_SCRIPT


def test_selection_output_decodes_utf8_text():
    selected_text = "Résumé and line one\nline two"
    encoded = base64.b64encode(selected_text.encode("utf-8")).decode("ascii")

    assert _decode_selection_text(f"TEXT:{encoded}") == selected_text


def test_selection_monitor_keeps_selection_during_unsupported_focus(monkeypatch):
    selected_text = "selected text"
    encoded = base64.b64encode(selected_text.encode("utf-8")).decode("ascii")

    class FakeProcess:
        stdout = io.StringIO(
            f"TEXT:{encoded}\nUNSUPPORTED\nTEXT:{encoded}\nCLEAR\n"
        )

        def wait(self):
            return 0

        def poll(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr("flex.voice.sys.platform", "win32")
    monkeypatch.setattr("flex.voice._powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr("flex.voice.subprocess.CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(
        "flex.voice.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    worker = SelectionMonitorWorker()
    selections = []
    availability = []
    worker.selection_changed.connect(selections.append)
    worker.availability_changed.connect(availability.append)

    worker.run()

    assert selections == [selected_text, selected_text, ""]
    assert availability == [False]


@pytest.mark.parametrize("encoded", ["not-base64", "////"])
def test_selection_output_rejects_invalid_text(encoded):
    with pytest.raises(RuntimeError, match="invalid selected text"):
        _decode_selection_text(f"TEXT:{encoded}")
