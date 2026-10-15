#!/usr/bin/env python3
"""
YouTube Video Transcriber
=========================
Transcribes YouTube videos to structured JSON using faster-whisper + yt-dlp.

Usage:
    python transcribe.py <youtube_url> [--model small] [--language en] [--device auto]
    python transcribe.py <youtube_url> --remote   # transcribe on a Whisper server

Output:
    JSON file in current directory with collision-free naming:
    transcript_<video_id>_<timestamp>_<short_hash>.json

The JSON output is structured for downstream AI processing with:
    - Full text concatenation
    - Per-segment timestamps, text, and confidence
    - Rich metadata (model, language, duration, etc.)
"""

import subprocess
import sys
import os
import json
import hashlib
import platform
import shutil
import re
import argparse
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ============================================================================
# Dependency Management
# ============================================================================

PYTHON_DEPS = {
    "faster_whisper": "faster-whisper",
    "yt_dlp": "yt-dlp",
}

def get_os_info():
    """Detect OS and package manager."""
    system = platform.system().lower()
    info = {"system": system, "distro": None, "pkg_manager": None}

    if system == "linux":
        # Try to detect distro
        try:
            with open("/etc/os-release") as f:
                release = f.read()
            if "ubuntu" in release.lower() or "debian" in release.lower():
                info["distro"] = "debian"
                info["pkg_manager"] = "apt"
            elif "fedora" in release.lower() or "rhel" in release.lower() or "centos" in release.lower():
                info["distro"] = "redhat"
                info["pkg_manager"] = "dnf"
            elif "arch" in release.lower():
                info["distro"] = "arch"
                info["pkg_manager"] = "pacman"
            elif "opensuse" in release.lower() or "suse" in release.lower():
                info["distro"] = "suse"
                info["pkg_manager"] = "zypper"
        except FileNotFoundError:
            pass

        # WSL detection
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    info["wsl"] = True
        except FileNotFoundError:
            info["wsl"] = False

    elif system == "darwin":
        info["distro"] = "macos"
        info["pkg_manager"] = "brew"

    return info


def check_ffmpeg():
    """Check if ffmpeg is available."""
    return shutil.which("ffmpeg") is not None


def print_ffmpeg_install_instructions(os_info):
    """Print OS-specific ffmpeg install instructions."""
    print("\n" + "=" * 60)
    print("⚠️  ffmpeg is required but not found!")
    print("=" * 60)

    instructions = {
        "debian": "sudo apt update && sudo apt install -y ffmpeg",
        "redhat": "sudo dnf install -y ffmpeg",
        "arch": "sudo pacman -S ffmpeg",
        "suse": "sudo zypper install ffmpeg",
        "macos": "brew install ffmpeg",
    }

    distro = os_info.get("distro")
    if distro and distro in instructions:
        print(f"\nDetected: {distro}")
        print(f"Run this:\n")
        print(f"    {instructions[distro]}")
    else:
        print("\nCould not detect your OS. Install ffmpeg via your package manager:")
        for name, cmd in instructions.items():
            print(f"  {name:>10}: {cmd}")

    if os_info.get("system") == "windows":
        print("\nWindows: Download from https://ffmpeg.org/download.html")
        print("  Or via scoop:  scoop install ffmpeg")
        print("  Or via choco:  choco install ffmpeg")

    print("=" * 60 + "\n")


def ensure_python_deps(skip=()):
    """Install missing Python dependencies without sudo."""
    missing = []
    for import_name, pip_name in PYTHON_DEPS.items():
        if import_name in skip:
            continue
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return True

    print(f"📦 Installing missing Python packages: {', '.join(missing)}")

    # Try user install first (no sudo needed)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--user", "--break-system-packages",
        *missing
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Retry without --user (some envs like venvs don't need it)
            cmd_fallback = [
                sys.executable, "-m", "pip", "install",
                "--break-system-packages",
                *missing
            ]
            result = subprocess.run(cmd_fallback, capture_output=True, text=True)
            if result.returncode != 0:
                # Last resort: no --break-system-packages flag (older pip)
                cmd_last = [sys.executable, "-m", "pip", "install", "--user", *missing]
                result = subprocess.run(cmd_last, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"❌ Failed to install dependencies:\n{result.stderr}")
                    return False

        print("✅ Dependencies installed successfully")

        # Refresh import paths for --user installs
        import site
        import importlib
        importlib.invalidate_caches()
        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            sys.path.insert(0, user_site)

        return True

    except Exception as e:
        print(f"❌ pip install failed: {e}")
        return False


# ============================================================================
# Video Processing
# ============================================================================

def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',  # bare ID
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_video_metadata(url: str) -> dict:
    """Fetch video metadata via yt-dlp."""
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title", "Unknown"),
            "channel": info.get("uploader", "Unknown"),
            "channel_id": info.get("uploader_id", ""),
            "upload_date": info.get("upload_date", ""),
            "duration_seconds": info.get("duration", 0),
            "description": info.get("description", ""),
            "url": info.get("webpage_url", url),
            "video_id": info.get("id", ""),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "tags": info.get("tags", []),
            "categories": info.get("categories", []),
            "language": info.get("language"),
        }


def download_audio(url: str, output_dir: str) -> str:
    """Download audio from YouTube video."""
    import yt_dlp

    output_path = os.path.join(output_dir, "audio.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "0",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    wav_path = os.path.join(output_dir, "audio.wav")
    if os.path.exists(wav_path):
        return wav_path

    # Fallback: find whatever audio file was created
    for f in os.listdir(output_dir):
        if f.startswith("audio."):
            return os.path.join(output_dir, f)

    raise FileNotFoundError("Audio download failed — no output file found")


def transcribe_audio(audio_path: str, model_size: str = "small",
                     device: str = "auto", language: str = None,
                     thicc: bool = False) -> dict:
    """Transcribe audio using faster-whisper."""
    from faster_whisper import WhisperModel

    # Device selection
    if device == "auto":
        try:
            import torch
            compute_type = "float16" if torch.cuda.is_available() else "int8"
            actual_device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            compute_type = "int8"
            actual_device = "cpu"
    else:
        actual_device = device
        compute_type = "float16" if device == "cuda" else "int8"

    print(f"🧠 Loading model '{model_size}' on {actual_device} ({compute_type})...")
    model = WhisperModel(model_size, device=actual_device, compute_type=compute_type)

    print("🎙️  Transcribing...")
    kwargs = {"beam_size": 5, "vad_filter": True}
    if thicc:
        kwargs["word_timestamps"] = True
    if language:
        kwargs["language"] = language

    segments_gen, info = model.transcribe(audio_path, **kwargs)

    segments = []
    full_text_parts = []

    for seg in segments_gen:
        words = []
        if thicc and seg.words:
            words = [
                {
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "confidence": round(w.probability, 4),
                }
                for w in seg.words
            ]

        segment_data = {
            "id": seg.id,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "confidence": round(seg.avg_logprob, 4) if seg.avg_logprob else None,
            "no_speech_probability": round(seg.no_speech_prob, 4),
            "words": words,
        }
        segments.append(segment_data)
        full_text_parts.append(seg.text.strip())

        # Progress indicator
        if seg.id % 20 == 0:
            print(f"   ... segment {seg.id} ({seg.start:.1f}s)")

    return {
        "segments": segments,
        "full_text": " ".join(full_text_parts),
        "detected_language": info.language,
        "language_probability": round(info.language_probability, 4),
        "duration_seconds": round(info.duration, 2),
        "transcription_model": model_size,
        "device_used": actual_device,
        "compute_type": compute_type,
    }


# ============================================================================
# Remote transcription (Whisper server speaking the standard
# POST /v1/audio/transcriptions API, e.g. a faster-whisper server)
# ============================================================================

REMOTE_TIMEOUT_SECONDS = 3600


def _remote_endpoint(base_url: str) -> str:
    """Accept a base URL with or without a trailing /v1."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base + "/v1/audio/transcriptions"


def _encode_multipart(fields, file_path: str, content_type: str):
    """Build a multipart/form-data body (stdlib only). Fields may repeat."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    with open(file_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(file_path)
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _compress_for_upload(audio_path: str) -> str:
    """Whisper works on 16 kHz mono audio, so send exactly that, as lossless FLAC."""
    out_path = os.path.splitext(audio_path)[0] + "_16k.flac"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", audio_path,
         "-ac", "1", "-ar", "16000", "-c:a", "flac", out_path],
        check=True,
    )
    return out_path


def _assign_words(segments: list, words: list) -> None:
    """Attach top-level word timestamps (some servers return them only there) to segments by time."""
    for seg in segments:
        seg.setdefault("words", [])
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        for seg in segments:
            if seg["start"] <= mid <= seg["end"]:
                seg["words"].append(w)
                break


def transcribe_remote(audio_path: str, url: str, model: str, language: str = None,
                      thicc: bool = False, api_key: str = None) -> dict:
    """Transcribe on a Whisper server (e.g. faster-whisper on a GPU machine).

    Returns the same structure as transcribe_audio().
    """
    upload_path = _compress_for_upload(audio_path)
    fields = [("model", model), ("response_format", "verbose_json"), ("vad_filter", "true")]
    if language:
        fields.append(("language", language))
    if thicc:
        fields += [("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word")]
    body, content_type = _encode_multipart(fields, upload_path, "audio/flac")
    headers = {"Content-Type": content_type}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    endpoint = _remote_endpoint(url)
    print(f"🌐 Transcribing on remote server with model '{model}' "
          f"({len(body) / (1024 * 1024):.1f} MB upload)...")
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=REMOTE_TIMEOUT_SECONDS) as response:
            result = json.load(response)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"remote server returned HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise RuntimeError(
            f"could not reach the remote server at {endpoint} ({reason}). "
            "Check that it is running, or drop --remote to transcribe locally."
        ) from None

    raw_segments = result.get("segments") or []
    segments = []
    for i, seg in enumerate(raw_segments):
        words = []
        if thicc:
            words = [
                {
                    "word": w["word"].strip(),
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                    "confidence": round(w["probability"], 4) if w.get("probability") is not None else None,
                }
                for w in seg.get("words") or []
            ]
        avg_logprob = seg.get("avg_logprob")
        no_speech = seg.get("no_speech_prob")
        segments.append({
            "id": seg.get("id", i + 1),
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"].strip(),
            "confidence": round(avg_logprob, 4) if avg_logprob else None,
            "no_speech_probability": round(no_speech, 4) if no_speech is not None else None,
            "words": words,
        })
    if thicc and result.get("words") and not any(s["words"] for s in segments):
        _assign_words(segments, [
            {"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3),
             "confidence": round(w["probability"], 4) if w.get("probability") is not None else None}
            for w in result["words"]
        ])

    language_probability = result.get("language_probability")
    return {
        "segments": segments,
        "full_text": " ".join(s["text"] for s in segments) if segments else result.get("text", "").strip(),
        "detected_language": result.get("language"),
        "language_probability": round(language_probability, 4) if language_probability is not None else None,
        "duration_seconds": round(result.get("duration") or 0, 2),
        "transcription_model": model,
        "device_used": "remote",
        "compute_type": "remote",
    }


# ============================================================================
# Output
# ============================================================================

def generate_output_path(video_id: str, output_dir: str = ".") -> str:
    """Generate collision-free output filename."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Short hash for extra collision resistance
    hash_input = f"{video_id}_{timestamp}_{os.getpid()}"
    short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]

    vid_slug = video_id or "unknown"
    filename = f"transcript_{vid_slug}_{timestamp}_{short_hash}.json"
    return os.path.join(output_dir, filename)


def build_output(video_meta: dict, transcription: dict, thicc: bool = False) -> dict:
    """
    Build final JSON output optimized for downstream AI consumption.

    Default (slim):
    - meta: video + transcription metadata
    - content.full_text: complete transcript as single string
    - content.segments: timestamped text segments (no word-level data)

    --thicc:
    - Everything above plus word-level timestamps and confidence scores
    """
    if thicc:
        segments = transcription["segments"]
    else:
        segments = [
            {
                "id": seg["id"],
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
            }
            for seg in transcription["segments"]
        ]

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "video": video_meta,
            "transcription": {
                "detected_language": transcription["detected_language"],
                "language_confidence": transcription["language_probability"],
                "duration_seconds": transcription["duration_seconds"],
                "segment_count": len(transcription["segments"]),
                "word_count": len(transcription["full_text"].split()),
            },
        },
        "content": {
            "full_text": transcription["full_text"],
            "segments": segments,
        },
        "processing": {
            "tool": "faster-whisper",
            "model": transcription["transcription_model"],
            "device": transcription["device_used"],
            "compute_type": transcription["compute_type"],
            "generator": "transcribe.py",
            "output_mode": "thicc" if thicc else "slim",
        },
    }


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe YouTube videos to structured JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python transcribe.py https://www.youtube.com/watch?v=dQw4w9WgXcQ
    python transcribe.py https://youtu.be/dQw4w9WgXcQ --model medium
    python transcribe.py "VIDEO_URL" --language ja --device cpu
    python transcribe.py "VIDEO_URL" --thicc  # word-level timestamps & confidence
    python transcribe.py "VIDEO_URL" --save-audio  # keep extracted audio file
    python transcribe.py "VIDEO_URL" --no-transcribe  # audio only, skip Whisper
    python transcribe.py "VIDEO_URL" --remote  # transcribe on $WHISPER_API_URL
        """,
    )
    parser.add_argument("url", help="YouTube video URL or video ID")
    parser.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size (default: small)")
    parser.add_argument("--language", default=None, help="Force language code (e.g. en, ja, nl). Auto-detected if omitted.")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                        help="Compute device (default: auto)")
    parser.add_argument("--thicc", action="store_true",
                        help="Include word-level timestamps and confidence scores (default: slim output)")
    parser.add_argument("--save-audio", action="store_true",
                        help="Also save the extracted audio file alongside the transcript JSON")
    parser.add_argument("--no-transcribe", action="store_true",
                        help="Download and extract audio only; skip transcription and JSON output (implies --save-audio)")
    parser.add_argument("--output-dir", default=".", help="Output directory (default: current)")
    parser.add_argument("--remote", action="store_true",
                        help="Transcribe on a Whisper server (e.g. faster-whisper on another machine's GPU) "
                             "instead of locally. URL from --remote-url or $WHISPER_API_URL")
    parser.add_argument("--remote-url", default=os.environ.get("WHISPER_API_URL"),
                        help="Base URL of the Whisper server (default: $WHISPER_API_URL)")
    parser.add_argument("--remote-model", default=None,
                        help="Model name to request from the server (default: same as --model)")

    args = parser.parse_args()

    # --no-transcribe is pointless without the audio, so imply --save-audio
    if args.no_transcribe:
        args.save_audio = True

    if args.remote and not args.no_transcribe and not args.remote_url:
        print("❌ --remote needs a server URL: pass --remote-url or set WHISPER_API_URL")
        sys.exit(1)

    print("=" * 60)
    print("🎬 YouTube Transcriber")
    print("=" * 60)

    # --- OS Detection ---
    os_info = get_os_info()
    wsl_tag = " (WSL)" if os_info.get("wsl") else ""
    print(f"🖥️  OS: {platform.system()} / {os_info.get('distro', 'unknown')}{wsl_tag}")

    # --- Check ffmpeg ---
    if not check_ffmpeg():
        print_ffmpeg_install_instructions(os_info)
        sys.exit(1)
    print("✅ ffmpeg found")

    # --- Install Python deps (faster-whisper is only needed for local transcription) ---
    skip_deps = ("faster_whisper",) if (args.remote or args.no_transcribe) else ()
    if not ensure_python_deps(skip=skip_deps):
        print("❌ Could not install required Python packages. Exiting.")
        sys.exit(1)

    # --- Validate URL ---
    video_id = extract_video_id(args.url)
    if not video_id:
        print(f"❌ Could not parse video ID from: {args.url}")
        print("   Supported: youtube.com/watch?v=..., youtu.be/..., shorts/..., or bare ID")
        sys.exit(1)
    print(f"🎯 Video ID: {video_id}")

    # --- Fetch metadata ---
    print("📋 Fetching video metadata...")
    try:
        video_meta = get_video_metadata(args.url)
        print(f"   Title: {video_meta['title']}")
        print(f"   Channel: {video_meta['channel']}")
        duration = video_meta.get("duration_seconds", 0)
        print(f"   Duration: {duration // 60}m {duration % 60}s")
    except Exception as e:
        print(f"⚠️  Could not fetch metadata (continuing anyway): {e}")
        video_meta = {"video_id": video_id, "url": args.url, "title": "Unknown"}

    # --- Determine transcript output path (shared stem with audio if saved) ---
    os.makedirs(args.output_dir, exist_ok=True)
    output_path = generate_output_path(video_id, args.output_dir)
    saved_audio_path = None

    # --- Download audio ---
    with tempfile.TemporaryDirectory(prefix="transcribe_") as tmp_dir:
        print("⬇️  Downloading audio...")
        try:
            audio_path = download_audio(args.url, tmp_dir)
            audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            print(f"   Audio: {audio_size_mb:.1f} MB")
        except Exception as e:
            print(f"❌ Audio download failed: {e}")
            sys.exit(1)

        # --- Save audio (before temp dir cleanup) ---
        if args.save_audio:
            audio_ext = os.path.splitext(audio_path)[1] or ".wav"
            audio_stem = Path(output_path).stem.replace("transcript_", "audio_", 1)
            saved_audio_path = os.path.join(args.output_dir, f"{audio_stem}{audio_ext}")
            shutil.copy2(audio_path, saved_audio_path)
            print(f"🎵 Audio saved: {saved_audio_path}")

        # --- Transcribe (unless skipped) ---
        transcription = None
        if args.no_transcribe:
            print("⏭️  Skipping transcription (--no-transcribe)")
        else:
            try:
                if args.remote:
                    transcription = transcribe_remote(
                        audio_path,
                        url=args.remote_url,
                        model=args.remote_model or args.model,
                        language=args.language,
                        thicc=args.thicc,
                        api_key=os.environ.get("WHISPER_API_KEY"),
                    )
                else:
                    transcription = transcribe_audio(
                        audio_path,
                        model_size=args.model,
                        device=args.device,
                        language=args.language,
                        thicc=args.thicc,
                    )
            except Exception as e:
                print(f"❌ Transcription failed: {e}")
                sys.exit(1)

    # --- Build & write output (unless transcription was skipped) ---
    print("\n" + "=" * 60)
    print("✅ Done!")
    if transcription is not None:
        output = build_output(video_meta, transcription, thicc=args.thicc)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        file_size_kb = os.path.getsize(output_path) / 1024
        print(f"📄 Output: {output_path}")
        print(f"   Size: {file_size_kb:.1f} KB")
    if saved_audio_path:
        saved_audio_mb = os.path.getsize(saved_audio_path) / (1024 * 1024)
        print(f"🎵 Audio:  {saved_audio_path}")
        print(f"   Size: {saved_audio_mb:.1f} MB")
    if transcription is not None:
        lang_prob = transcription["language_probability"]
        lang_conf = f" ({lang_prob:.1%} confidence)" if lang_prob is not None else ""
        print(f"   Language: {transcription['detected_language']}{lang_conf}")
        print(f"   Segments: {len(transcription['segments'])}")
        print(f"   Words: {len(transcription['full_text'].split())}")
    print("=" * 60)


if __name__ == "__main__":
    main()
