from __future__ import annotations

import base64
import math
import shutil
import subprocess
import sys
import time
from array import array
from collections import deque
from functools import lru_cache
from queue import Empty, Full, Queue
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import numpy as np

VOICE_MODEL_NAME = "small.en"
VOICE_SILENCE_TIMEOUT_SECONDS = 2
VOICE_SAMPLE_SECONDS = 0.1
VOICE_SILENCE_THRESHOLD = 0.008
VOICE_MAX_NOISE_THRESHOLD = 0.025
VOICE_TRANSCRIPTION_SAMPLE_RATE = 16_000

_SPEECH_SCRIPT = """
$ErrorActionPreference = 'Stop'
try {
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    Add-Type -AssemblyName System.Speech
    $text = [Console]::In.ReadToEnd()
    $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synthesizer.Speak($text)
    }
    finally {
        $synthesizer.Dispose()
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

_SELECTION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $hasPrevious = $false
    $previous = ''
    $previouslySupported = $null
    while ($true) {
        $selectedText = ''
        $selectionSupported = $false
        try {
            $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
            if ($null -ne $focused) {
                $pattern = $null
                if ($focused.TryGetCurrentPattern(
                    [System.Windows.Automation.TextPattern]::Pattern,
                    [ref]$pattern
                )) {
                    $selectionSupported = $true
                    $ranges = $pattern.GetSelection()
                    foreach ($range in $ranges) {
                        $part = $range.GetText(100000)
                        if (-not [string]::IsNullOrWhiteSpace($part)) {
                            if ($selectedText.Length -gt 0) {
                                $selectedText += [Environment]::NewLine
                            }
                            $selectedText += $part
                        }
                    }
                }
            }
        }
        catch [System.Runtime.InteropServices.COMException] {
            $selectedText = ''
        }

        if ($selectionSupported -ne $previouslySupported) {
            if ($selectionSupported) {
                [Console]::Out.WriteLine('AVAILABLE')
            }
            else {
                [Console]::Out.WriteLine('UNSUPPORTED')
            }
            [Console]::Out.Flush()
            $previouslySupported = $selectionSupported
            $hasPrevious = $false
        }

        if ($selectionSupported -and
            (-not $hasPrevious -or $selectedText -cne $previous)) {
            if ([string]::IsNullOrWhiteSpace($selectedText)) {
                [Console]::Out.WriteLine('CLEAR')
                [Console]::Out.Flush()
                $previous = ''
            }
            else {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($selectedText)
                [Console]::Out.WriteLine(
                    'TEXT:' + [Convert]::ToBase64String($bytes)
                )
                [Console]::Out.Flush()
                $previous = $selectedText
            }
            $hasPrevious = $true
        }
        Start-Sleep -Milliseconds 200
    }
}
catch {
    [Console]::Out.WriteLine('ERROR:' + $_.Exception.Message)
    [Console]::Out.Flush()
    exit 1
}
"""


def _powershell_executable() -> str:
    if sys.platform != "win32":
        raise RuntimeError(
            "Windows text-to-speech and selection monitoring require Windows."
        )
    executable = shutil.which("powershell.exe")
    if executable is None:
        raise RuntimeError("Windows PowerShell was not found.")
    return executable


def _select_microphone_device(sounddevice_module) -> int:
    devices = sounddevice_module.query_devices()
    for index, device in enumerate(devices):
        if device["max_input_channels"] > 0 and "droidcam" in device["name"].casefold():
            return index

    default_input = sounddevice_module.default.device[0]
    if default_input is not None and default_input >= 0:
        device = sounddevice_module.query_devices(default_input, "input")
        if device["max_input_channels"] > 0:
            return default_input

    available = ", ".join(
        device["name"]
        for device in devices
        if device["max_input_channels"] > 0
    )
    raise RuntimeError(
        "No microphone input is available. Connect DroidCam Audio or choose "
        f"a default input device in Windows. Available inputs: {available or 'none'}"
    )


def _audio_rms(audio: bytes) -> float:
    samples = array("h")
    samples.frombytes(audio)
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768


@lru_cache(maxsize=1)
def _load_whisper_model() -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Local voice recognition requires faster-whisper. Install the "
            "project dependencies with 'python -m pip install -r requirements.txt'."
        ) from exc

    return WhisperModel(VOICE_MODEL_NAME, device="cpu", compute_type="int8")


def _pcm_to_whisper_audio(audio: bytes, sample_rate: int) -> np.ndarray:
    import av
    import numpy as np

    if sample_rate <= 0:
        raise ValueError("The microphone reported an invalid sample rate.")
    if not audio:
        return np.empty(0, dtype=np.float32)

    samples = np.frombuffer(audio, dtype=np.int16)
    if sample_rate == VOICE_TRANSCRIPTION_SAMPLE_RATE:
        return samples.astype(np.float32) / 32768

    frame = av.AudioFrame.from_ndarray(
        samples.reshape(1, -1),
        format="s16",
        layout="mono",
    )
    frame.sample_rate = sample_rate
    resampler = av.AudioResampler(
        format="flt",
        layout="mono",
        rate=VOICE_TRANSCRIPTION_SAMPLE_RATE,
    )
    frames = resampler.resample(frame)
    frames.extend(resampler.resample(None))
    if not frames:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(
        [resampled.to_ndarray().reshape(-1) for resampled in frames]
    )


def _transcribe_audio(audio: bytes, sample_rate: int, model: Any) -> str:
    whisper_audio = _pcm_to_whisper_audio(audio, sample_rate)
    if len(whisper_audio) == 0:
        return ""
    segments, _info = model.transcribe(
        whisper_audio,
        language="en",
        beam_size=5,
        condition_on_previous_text=False,
    )
    return " ".join(
        segment.text.strip()
        for segment in segments
        if segment.text.strip()
    ).strip()


def _decode_selection_text(line: str) -> str:
    try:
        return base64.b64decode(
            line.removeprefix("TEXT:"),
            validate=True,
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "Windows UI Automation returned invalid selected text."
        ) from exc


class SelectionMonitorWorker(QThread):
    selection_changed = Signal(str)
    availability_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = Event()
        self._process: subprocess.Popen[str] | None = None
        self._process_lock = Lock()

    def run(self) -> None:
        process: subprocess.Popen[str] | None = None
        try:
            if sys.platform != "win32":
                raise RuntimeError(
                    "Selected-text monitoring requires Windows UI Automation."
                )
            self._process = subprocess.Popen(
                [
                    _powershell_executable(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _SELECTION_SCRIPT,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            process = self._process
            with self._process_lock:
                if self._stop_requested.is_set():
                    process.terminate()
            if self._process.stdout is None:
                raise RuntimeError("Could not read Windows selection monitor output.")

            reported_error = False
            for output_line in self._process.stdout:
                if self._stop_requested.is_set():
                    break
                line = output_line.rstrip("\r\n")
                if line == "CLEAR":
                    self.selection_changed.emit("")
                elif line == "AVAILABLE":
                    self.availability_changed.emit(True)
                elif line == "UNSUPPORTED":
                    self.availability_changed.emit(False)
                elif line.startswith("TEXT:"):
                    self.selection_changed.emit(_decode_selection_text(line))
                elif line.startswith("ERROR:"):
                    reported_error = True
                    self.error_occurred.emit(line.removeprefix("ERROR:").strip())
                    break

            return_code = self._process.wait()
            if not self._stop_requested.is_set() and not reported_error:
                self.error_occurred.emit(
                    "Windows UI Automation selection monitoring stopped "
                    f"(exit code {return_code})."
                )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if not self._stop_requested.is_set():
                self.error_occurred.emit(str(exc))
        finally:
            with self._process_lock:
                self._process = None
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    process.wait()
                if process.stdout is not None:
                    process.stdout.close()

    def stop(self) -> None:
        self._stop_requested.set()
        with self._process_lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()


class SpeechRecognitionWorker(QThread):
    ready = Signal()
    speech_activity = Signal()
    transcript_recognized = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = Event()
        self._audio_queue: Queue[bytes] = Queue(maxsize=100)

    def run(self) -> None:
        try:
            model = _load_whisper_model()
            if self._stop_requested.is_set():
                return
            import sounddevice

            device = _select_microphone_device(sounddevice)
            device_info = sounddevice.query_devices(device, "input")
            sample_rate = int(device_info["default_samplerate"])
            if sample_rate <= 0:
                raise RuntimeError(
                    "DroidCam Audio reported an invalid microphone sample rate."
                )
            frames_per_block = max(1, round(sample_rate * VOICE_SAMPLE_SECONDS))

            def receive_audio(indata, frames, time_info, status) -> None:
                del frames, time_info
                if status:
                    return
                try:
                    self._audio_queue.put_nowait(bytes(indata))
                except Full:
                    try:
                        self._audio_queue.get_nowait()
                    except Empty:
                        pass
                    try:
                        self._audio_queue.put_nowait(bytes(indata))
                    except Full:
                        pass

            with sounddevice.RawInputStream(
                device=device,
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frames_per_block,
                callback=receive_audio,
            ):
                if self._stop_requested.is_set():
                    return
                self.ready.emit()
                pre_roll: deque[bytes] = deque(maxlen=5)
                segment: list[bytes] = []
                speaking = False
                last_voice_time = 0.0
                noise_level = 0.003

                while not self._stop_requested.is_set():
                    try:
                        audio_block = self._audio_queue.get(timeout=0.1)
                    except Empty:
                        continue

                    level = _audio_rms(audio_block)
                    threshold = max(
                        VOICE_SILENCE_THRESHOLD,
                        min(VOICE_MAX_NOISE_THRESHOLD, noise_level * 2.5),
                    )
                    now = time.monotonic()
                    if level >= threshold:
                        self.speech_activity.emit()
                        if not speaking:
                            segment = list(pre_roll)
                            speaking = True
                        segment.append(audio_block)
                        last_voice_time = now
                    elif speaking:
                        segment.append(audio_block)
                        if now - last_voice_time >= VOICE_SILENCE_TIMEOUT_SECONDS:
                            audio = b"".join(segment)
                            if len(audio) >= sample_rate:
                                transcript = _transcribe_audio(
                                    audio,
                                    sample_rate,
                                    model,
                                )
                                if transcript:
                                    self.transcript_recognized.emit(transcript)
                            segment = []
                            pre_roll.clear()
                            speaking = False
                    else:
                        noise_level = noise_level * 0.98 + level * 0.02
                        pre_roll.append(audio_block)
        except Exception as exc:
            if not self._stop_requested.is_set():
                self.error_occurred.emit(str(exc))

    def stop(self) -> None:
        self._stop_requested.set()


def speak_text(text: str) -> None:
    if not text.strip():
        return
    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _SPEECH_SCRIPT,
        ],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        error = result.stderr.strip() or "Windows text-to-speech failed."
        raise RuntimeError(error)
