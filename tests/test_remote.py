"""Tests for --remote transcription against a fake Whisper server.

Run: python -m unittest discover -s tests
Needs ffmpeg on PATH (the upload is converted to 16 kHz mono FLAC).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import transcribe  # noqa: E402

SEGMENT_WORDS = {
    "language": "en", "language_probability": 0.98, "duration": 4.2, "text": " Hello there. General Kenobi.",
    "segments": [
        {"id": 1, "start": 0.0, "end": 1.5, "text": " Hello there.", "avg_logprob": -0.21, "no_speech_prob": 0.01,
         "words": [{"word": " Hello", "start": 0.0, "end": 0.6, "probability": 0.99},
                   {"word": " there.", "start": 0.6, "end": 1.5, "probability": 0.95}]},
        {"id": 2, "start": 2.0, "end": 4.2, "text": " General Kenobi.", "avg_logprob": -0.33, "no_speech_prob": 0.02,
         "words": [{"word": " General", "start": 2.0, "end": 2.9, "probability": 0.9},
                   {"word": " Kenobi.", "start": 2.9, "end": 4.2, "probability": 0.8}]},
    ],
}

# Some servers return words only at the top level, and no language_probability.
TOP_LEVEL_WORDS = {
    "language": "english", "duration": 4.2, "text": "Hello there. General Kenobi.",
    "segments": [{"id": 0, "start": 0.0, "end": 1.5, "text": "Hello there.", "avg_logprob": -0.2, "no_speech_prob": 0.0},
                 {"id": 1, "start": 2.0, "end": 4.2, "text": "General Kenobi.", "avg_logprob": -0.3, "no_speech_prob": 0.0}],
    "words": [{"word": "Hello", "start": 0.0, "end": 0.6}, {"word": "there.", "start": 0.6, "end": 1.5},
              {"word": "General", "start": 2.0, "end": 2.9}, {"word": "Kenobi.", "start": 2.9, "end": 4.2}],
}


class FakeServer:
    def __init__(self, payload=None, status=200):
        self.payload, self.status, self.requests = payload, status, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                data = json.dumps(outer.payload).encode() if outer.status == 200 else b'{"detail":"model exploded"}'
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RemoteTranscriptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audio = os.path.join(self.tmp.name, "audio.wav")
        with wave.open(self.audio, "wb") as w:  # 0.5 s of 44.1 kHz stereo silence
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100)
            w.writeframes(b"\x00\x00\x00\x00" * 22050)
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()
        self.tmp.cleanup()

    def server(self, **kw):
        s = FakeServer(**kw)
        self.servers.append(s)
        return s

    def test_thicc_maps_segments_and_words_like_local(self):
        s = self.server(payload=SEGMENT_WORDS)
        result = transcribe.transcribe_remote(self.audio, url=s.url, model="small", thicc=True)
        self.assertEqual(result["full_text"], "Hello there. General Kenobi.")
        self.assertEqual(result["detected_language"], "en")
        self.assertEqual(result["language_probability"], 0.98)
        first = result["segments"][0]
        self.assertEqual(set(first), {"id", "start", "end", "text", "confidence", "no_speech_probability", "words"})
        self.assertEqual(first["text"], "Hello there.")
        self.assertEqual(first["words"][0], {"word": "Hello", "start": 0.0, "end": 0.6, "confidence": 0.99})
        self.assertEqual(result["device_used"], "remote")

    def test_slim_mode_has_no_words_and_skips_word_granularity(self):
        s = self.server(payload=SEGMENT_WORDS)
        result = transcribe.transcribe_remote(self.audio, url=s.url, model="small", thicc=False)
        self.assertTrue(all(seg["words"] == [] for seg in result["segments"]))
        self.assertNotIn(b"timestamp_granularities", s.requests[0]["body"])

    def test_top_level_words_are_assigned_to_segments(self):
        s = self.server(payload=TOP_LEVEL_WORDS)
        result = transcribe.transcribe_remote(self.audio, url=s.url + "/v1", model="whisper-1", thicc=True)
        self.assertEqual([w["word"] for w in result["segments"][1]["words"]], ["General", "Kenobi."])
        self.assertIsNone(result["language_probability"])
        self.assertEqual(s.requests[0]["path"], "/v1/audio/transcriptions")  # /v1 not doubled

    def test_request_sends_16k_mono_flac_and_fields(self):
        s = self.server(payload=SEGMENT_WORDS)
        transcribe.transcribe_remote(self.audio, url=s.url, model="medium", language="nl", thicc=True, api_key="k123")
        req = s.requests[0]
        self.assertEqual(req["headers"]["Authorization"], "Bearer k123")
        body = req["body"]
        for field in (b'name="model"\r\n\r\nmedium', b'name="language"\r\n\r\nnl',
                      b'name="response_format"\r\n\r\nverbose_json', b'name="timestamp_granularities[]"\r\n\r\nword'):
            self.assertIn(field, body)
        self.assertIn(b"Content-Type: audio/flac", body)
        self.assertIn(b"fLaC", body)

    def test_http_error_is_reported(self):
        s = self.server(status=500)
        with self.assertRaisesRegex(RuntimeError, "HTTP 500.*model exploded"):
            transcribe.transcribe_remote(self.audio, url=s.url, model="small")

    def test_unreachable_server_fails_fast_with_hint(self):
        s = self.server(payload=SEGMENT_WORDS)
        url = s.url
        s.close()
        self.servers.remove(s)
        with self.assertRaisesRegex(RuntimeError, "could not reach.*drop --remote"):
            transcribe.transcribe_remote(self.audio, url=url, model="small")


if __name__ == "__main__":
    unittest.main()
