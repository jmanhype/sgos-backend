"""
Video/Audio Transcription using faster-whisper.
Accepts video/audio files, returns timestamped transcript + summary.
"""
import os
import sys
import json
import tempfile
import subprocess
from datetime import datetime

# faster-whisper is installed globally
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False


# ─── Model Management ─────────────────────────────────────────────────────────

_model_cache = {}

def get_model(model_size: str = "base") -> "WhisperModel":
    """Load and cache a Whisper model. Sizes: tiny, base, small, medium, large-v3."""
    if model_size not in _model_cache:
        if not WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper not installed. pip install faster-whisper")
        print(f"🔄 Loading Whisper model: {model_size}...")
        _model_cache[model_size] = WhisperModel(
            model_size,
            device="cpu",  # Use CPU on Mac; switch to "cuda" if on GPU
            compute_type="int8",
        )
        print(f"✅ Model '{model_size}' loaded")
    return _model_cache[model_size]


# ─── Audio Extraction ─────────────────────────────────────────────────────────

def extract_audio(video_path: str, output_path: str = None) -> str:
    """Extract audio from video file using ffmpeg. Returns path to audio file."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
    
    # Use ffmpeg to extract audio as 16kHz WAV (optimal for Whisper)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn",                    # No video
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ar", "16000",          # 16kHz sample rate
        "-ac", "1",              # Mono
        output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")
    
    return output_path


def is_video_file(path: str) -> bool:
    """Check if file is a video (vs audio)."""
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
    return os.path.splitext(path)[1].lower() in video_exts


def is_audio_file(path: str) -> bool:
    """Check if file is audio."""
    audio_exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
    return os.path.splitext(path)[1].lower() in audio_exts


# ─── Transcription ─────────────────────────────────────────────────────────────

def transcribe(
    file_path: str,
    model_size: str = "base",
    language: str = None,
    timestamp_granularity: str = "segment",
) -> dict:
    """
    Transcribe a video or audio file.
    
    Args:
        file_path: Path to video/audio file
        model_size: Whisper model (tiny, base, small, medium, large-v3)
        language: ISO 639-1 code (e.g., 'en'). None = auto-detect.
        timestamp_granularity: 'segment' or 'word'
    
    Returns:
        Dict with transcript, segments, metadata.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Extract audio from video if needed
    audio_path = file_path
    temp_audio = None
    
    if is_video_file(file_path):
        print(f"🎬 Extracting audio from video...")
        temp_audio = extract_audio(file_path)
        audio_path = temp_audio
    elif not is_audio_file(file_path):
        # Try to extract audio anyway
        print(f"⚠️ Unknown file type, attempting audio extraction...")
        temp_audio = extract_audio(file_path)
        audio_path = temp_audio
    
    try:
        model = get_model(model_size)
        
        print(f"🎙️ Transcribing with model '{model_size}'...")
        segments_gen, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        
        segments = []
        full_text_parts = []
        
        for seg in segments_gen:
            segment_data = {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            if hasattr(seg, 'words') and seg.words:
                segment_data["words"] = [
                    {"word": w.word, "start": round(w.start, 2), "end": round(w.end, 2)}
                    for w in seg.words
                ]
            segments.append(segment_data)
            full_text_parts.append(seg.text.strip())
        
        full_text = " ".join(full_text_parts)
        
        # Generate summary stats
        duration = info.duration if hasattr(info, 'duration') else 0
        word_count = len(full_text.split())
        
        result = {
            "transcript": full_text,
            "segments": segments,
            "metadata": {
                "file": os.path.basename(file_path),
                "duration_seconds": round(duration, 1),
                "language": info.language if hasattr(info, 'language') else language or "auto",
                "language_probability": round(info.language_probability, 3) if hasattr(info, 'language_probability') else None,
                "word_count": word_count,
                "segment_count": len(segments),
                "model": model_size,
                "transcribed_at": datetime.now().isoformat(),
            },
        }
        
        print(f"✅ Transcription complete: {word_count} words, {len(segments)} segments, {duration:.1f}s")
        return result
    
    finally:
        # Clean up temp audio
        if temp_audio and os.path.exists(temp_audio):
            os.unlink(temp_audio)


def format_transcript(result: dict, format_type: str = "text") -> str:
    """Format transcript for different use cases."""
    if format_type == "text":
        return result["transcript"]
    
    elif format_type == "timestamped":
        lines = []
        for seg in result["segments"]:
            start = seg["start"]
            mins, secs = divmod(int(start), 60)
            lines.append(f"[{mins:02d}:{secs:02d}] {seg['text']}")
        return "\n".join(lines)
    
    elif format_type == "srt":
        lines = []
        for i, seg in enumerate(result["segments"], 1):
            start_h, start_r = divmod(seg["start"], 3600)
            start_m, start_s = divmod(start_r, 60)
            end_h, end_r = divmod(seg["end"], 3600)
            end_m, end_s = divmod(end_r, 60)
            lines.append(str(i))
            lines.append(f"{int(start_h):02d}:{int(start_m):02d}:{start_s:06.3f} --> {int(end_h):02d}:{int(end_m):02d}:{end_s:06.3f}".replace(".", ","))
            lines.append(seg["text"])
            lines.append("")
        return "\n".join(lines)
    
    elif format_type == "summary":
        meta = result["metadata"]
        return (
            f"📹 File: {meta['file']}\n"
            f"⏱️ Duration: {meta['duration_seconds']}s\n"
            f"🌐 Language: {meta['language']} ({meta.get('language_probability', 'N/A')} confidence)\n"
            f"📝 Words: {meta['word_count']}\n"
            f"🔢 Segments: {meta['segment_count']}\n\n"
            f"--- TRANSCRIPT ---\n\n{result['transcript']}"
        )
    
    return result["transcript"]


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <file_path> [--model base|small|medium] [--format text|timestamped|srt|summary]")
        sys.exit(1)
    
    file_path = sys.argv[1]
    model_size = "base"
    fmt = "summary"
    
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        model_size = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "base"
    
    if "--format" in sys.argv:
        idx = sys.argv.index("--format")
        fmt = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "summary"
    
    result = transcribe(file_path, model_size=model_size)
    print(format_transcript(result, fmt))
