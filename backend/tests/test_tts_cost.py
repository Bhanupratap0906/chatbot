"""Item 20 part 3: TTS cost counted only after audio returned."""

from __future__ import annotations

from unittest.mock import MagicMock

from modules.tts.deepgram_audio_generator import DeepgramAudioGenerator


def test_cost_not_counted_when_no_audio() -> None:
    tracker = MagicMock()
    gen = DeepgramAudioGenerator(client=MagicMock(), api_key="k", cost_tracker=tracker)
    # Force WAV path with empty chunks
    gen._raw_pcm_supported = False
    gen._client.speak.v2.audio.generate.return_value = iter([])
    chunks = list(gen.generate_audio_chunks("hello", "voice"))
    assert chunks == []
    tracker.add_tts_text.assert_not_called()


def test_cost_counted_after_audio() -> None:
    tracker = MagicMock()
    gen = DeepgramAudioGenerator(client=MagicMock(), api_key="k", cost_tracker=tracker)
    gen._raw_pcm_supported = False
    gen._client.speak.v2.audio.generate.return_value = iter([b"\x01\x02"])
    chunks = list(gen.generate_audio_chunks("hello", "voice"))
    assert chunks == [b"\x01\x02"]
    tracker.add_tts_text.assert_called_once_with("hello", "voice")
