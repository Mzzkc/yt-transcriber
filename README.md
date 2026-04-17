# yt-transcribe

Transcribe YouTube videos to JSON using [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Install

```bash
git clone https://github.com/Mzzkc/yt-transcriber
cd yt-transcriber
source setup.sh
```

## Usage

```bash
yt-transcribe https://www.youtube.com/watch?v=VIDEO_ID
yt-transcribe https://youtu.be/VIDEO_ID --model medium
yt-transcribe URL --model large-v3 --language ja
yt-transcribe URL --thicc        # word-level timestamps & confidence
yt-transcribe URL --save-audio   # keep the extracted audio alongside the JSON
```

## Models

| Model | VRAM | Speed | Quality |
|-------|------|-------|---------|
| `tiny` | ~1GB | fastest | Decent |
| `base` | ~1GB | faster | Good |
| **`small`** | ~2GB | fast | **Great (default)** |
| `medium` | ~5GB | slow | Excellent |
| `large-v3` | ~10GB | slowest | Best |

CPU works too — just slower. GPU auto-detected.

## Output

Slim by default. Optimized for feeding into AI pipelines.

```json
{
  "meta": { "video": { "title": "...", "channel": "..." }, "transcription": { "detected_language": "en", "word_count": 6842 } },
  "content": {
    "full_text": "entire transcript as one string...",
    "segments": [{ "id": 0, "start": 0.0, "end": 3.5, "text": "..." }]
  }
}
```

Use `--thicc` for word-level timestamps and confidence scores.

## Requirements

- Python 3.8+
- ffmpeg (installer handles this)
- NVIDIA GPU optional but recommended

## License

MIT
