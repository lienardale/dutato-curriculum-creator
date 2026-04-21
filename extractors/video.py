"""
Video extractor — fetches a transcript from a YouTube (or any yt-dlp-supported)
video or playlist URL, and produces the unified intermediate JSON format.

Caption recovery order:
  1. Manual captions in the preferred language(s) (fast)
  2. Auto-generated captions in the preferred language(s)
  3. Any other available transcript
  4. Local Whisper transcription (optional, requires `uv sync --extra video-heavy`)

Section granularity:
  - If yt-dlp reports chapters, one section per chapter.
  - Otherwise, fixed-duration time windows (default 5 min).
  Section titles are `[MM:SS-MM:SS] <name>` so they are self-describing and
  chunk_bridge.py can match them by title.

Local video files (.mp4, .webm, ...) are also supported — yt-dlp reads them
for metadata and whisper transcribes directly from the file.
"""

import sys
import tempfile
from collections import OrderedDict
from pathlib import Path


# ---------------------------------------------------------------------------
# Metadata (yt-dlp)
# ---------------------------------------------------------------------------

def _fetch_metadata(source: str, *, flat_playlist: bool = False) -> dict:
    """Fetch video/playlist metadata via yt-dlp without downloading media."""
    from yt_dlp import YoutubeDL

    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    if flat_playlist:
        opts["extract_flat"] = "in_playlist"

    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(source, download=False)


# ---------------------------------------------------------------------------
# Captions (youtube-transcript-api)
# ---------------------------------------------------------------------------

def _normalize_transcript(fetched) -> list[dict]:
    """Normalize transcript across youtube-transcript-api v0.x and v1.x.

    v0.x returns a list of dicts directly; v1.x returns a FetchedTranscript
    object with a ``to_raw_data()`` method.
    """
    if hasattr(fetched, "to_raw_data"):
        return fetched.to_raw_data()
    return fetched


def _list_transcripts(video_id: str):
    """Return a TranscriptList across youtube-transcript-api v0.x and v1.x.

    v0.x exposes ``YouTubeTranscriptApi.list_transcripts(video_id)`` as a
    classmethod. v1.x removed it in favor of an instance method
    ``YouTubeTranscriptApi().list(video_id)``. Both return a TranscriptList
    with the same ``find_manually_created_transcript`` / ``find_generated_transcript``
    interface.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    legacy = getattr(YouTubeTranscriptApi, "list_transcripts", None)
    try:
        if callable(legacy):
            return legacy(video_id)  # v0.x classmethod
        return YouTubeTranscriptApi().list(video_id)  # v1.x instance method
    except Exception:
        return None


def _try_captions(
    video_id: str,
    languages: list[str],
) -> tuple[list[dict] | None, str | None, bool | None]:
    """Try to pull captions, preferring manual > auto-generated > any.

    Returns (segments, language_code, is_auto_generated) or (None, None, None).
    Each segment is {"text": str, "start": float, "duration": float}.
    """
    transcript_list = _list_transcripts(video_id)
    if transcript_list is None:
        return None, None, None

    # Manual captions first
    try:
        transcript = transcript_list.find_manually_created_transcript(languages)
        return _normalize_transcript(transcript.fetch()), transcript.language_code, False
    except Exception:
        pass

    # Auto-generated in preferred languages
    try:
        transcript = transcript_list.find_generated_transcript(languages)
        return _normalize_transcript(transcript.fetch()), transcript.language_code, True
    except Exception:
        pass

    # Fall back to any transcript at all
    for transcript in transcript_list:
        try:
            return (
                _normalize_transcript(transcript.fetch()),
                transcript.language_code,
                bool(getattr(transcript, "is_generated", True)),
            )
        except Exception:
            continue

    return None, None, None


# ---------------------------------------------------------------------------
# Whisper fallback (faster-whisper, optional)
# ---------------------------------------------------------------------------

def _download_audio(url: str, tmpdir: str) -> str:
    """Download best audio stream for a URL via yt-dlp. Returns local file path."""
    from yt_dlp import YoutubeDL

    outtmpl = str(Path(tmpdir) / "audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

    candidates = sorted(Path(tmpdir).glob("audio.*"))
    if not candidates:
        raise RuntimeError(f"Audio download produced no file for {url}")
    return str(candidates[0])


def _transcribe_with_whisper(
    audio_path: str,
    model_name: str,
) -> tuple[list[dict], str]:
    """Run faster-whisper over an audio file.

    Returns (segments, detected_language). Raises ImportError if the optional
    dep is not installed.
    """
    from faster_whisper import WhisperModel  # optional dep (video-heavy extra)

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, beam_size=5)

    result = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        result.append({
            "text": text,
            "start": float(seg.start),
            "duration": float(seg.end - seg.start),
        })
    return result, info.language or ""


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

def _format_time(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS when ≥1 hour."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _split_by_chapters(
    segments: list[dict],
    chapters: list[dict],
    video_id: str,
    video_prefix: str,
) -> list[dict]:
    sections = []
    fallback_end = 0.0
    if segments:
        last = segments[-1]
        fallback_end = last["start"] + last.get("duration", 0.0)

    for chap in chapters:
        start = float(chap.get("start_time", 0.0))
        end = float(chap.get("end_time", fallback_end) or fallback_end)
        title = chap.get("title", "Chapter").strip() or "Chapter"

        parts = [s["text"].strip() for s in segments if start <= s["start"] < end]
        content = " ".join(p for p in parts if p).strip()
        if not content:
            continue

        sections.append({
            "title": f"{video_prefix}[{_format_time(start)}-{_format_time(end)}] {title}",
            "content": content,
            "depth": 0,
            "metadata": {
                "start_seconds": start,
                "end_seconds": end,
                "chapter": True,
                "video_id": video_id,
            },
        })
    return sections


def _split_by_windows(
    segments: list[dict],
    window_seconds: int,
    video_id: str,
    video_prefix: str,
) -> list[dict]:
    if not segments:
        return []

    windows: "OrderedDict[int, list[dict]]" = OrderedDict()
    for seg in segments:
        idx = int(seg["start"] // window_seconds)
        windows.setdefault(idx, []).append(seg)

    sections = []
    for idx, segs in windows.items():
        start = idx * window_seconds
        end = start + window_seconds
        content = " ".join(s["text"].strip() for s in segs if s["text"].strip()).strip()
        if not content:
            continue
        sections.append({
            "title": f"{video_prefix}[{_format_time(start)}-{_format_time(end)}]",
            "content": content,
            "depth": 0,
            "metadata": {
                "start_seconds": float(start),
                "end_seconds": float(end),
                "chapter": False,
                "video_id": video_id,
            },
        })
    return sections


def _segments_to_sections(
    segments: list[dict],
    chapters: list[dict],
    window_seconds: int,
    video_id: str,
    video_prefix: str = "",
) -> list[dict]:
    if chapters:
        return _split_by_chapters(segments, chapters, video_id, video_prefix)
    return _split_by_windows(segments, window_seconds, video_id, video_prefix)


# ---------------------------------------------------------------------------
# Per-video transcript resolution
# ---------------------------------------------------------------------------

def _resolve_transcript(
    video_url: str,
    video_id: str,
    languages: list[str],
    whisper_model: str,
    use_whisper: bool,
) -> tuple[list[dict], str, bool, str]:
    """Return (segments, language, is_auto_generated, transcript_source).

    Raises ValueError if no transcript can be obtained.
    """
    segments, lang, is_gen = _try_captions(video_id, languages)
    if segments is not None:
        return segments, (lang or ""), bool(is_gen), "captions"

    if not use_whisper:
        raise ValueError(
            f"No captions available for {video_url} and Whisper fallback is disabled "
            "(--no-whisper). Remove the flag and install `uv sync --extra video-heavy`."
        )

    # Check Whisper availability BEFORE downloading audio (avoid wasted bandwidth).
    try:
        import faster_whisper  # noqa: F401
    except ImportError as e:
        raise ValueError(
            f"No captions available for {video_url} and Whisper is not installed. "
            "Install with: uv sync --extra video-heavy"
        ) from e

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = _download_audio(video_url, tmpdir)
        segments, lang = _transcribe_with_whisper(audio_path, whisper_model)

    if not segments:
        raise ValueError(f"Whisper produced no transcript for {video_url}")
    return segments, (lang or ""), True, "whisper"


# ---------------------------------------------------------------------------
# Top-level: single video
# ---------------------------------------------------------------------------

def _extract_single(
    info: dict,
    source: str,
    languages: list[str],
    whisper_model: str,
    window_seconds: int,
    use_whisper: bool,
) -> dict:
    import _compat  # noqa: F401
    from chunk import count_tokens

    video_id = info.get("id", "") or ""
    title = info.get("title") or "Untitled Video"
    author = info.get("uploader") or info.get("channel") or ""
    duration = info.get("duration") or 0
    chapters = info.get("chapters") or []

    segments, lang, is_gen, transcript_source = _resolve_transcript(
        source, video_id, languages, whisper_model, use_whisper,
    )
    sections = _segments_to_sections(segments, chapters, window_seconds, video_id)
    total_tokens = sum(count_tokens(s["content"]) for s in sections)

    return {
        "source_type": "video",
        "source_path": source,
        "title": title,
        "author": author,
        "sections": sections,
        "images": [],
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_images": 0,
            "video_id": video_id,
            "duration_seconds": duration,
            "channel": author,
            "upload_date": info.get("upload_date", "") or "",
            "transcript_source": transcript_source,
            "caption_language": lang,
            "is_auto_generated": is_gen,
            "chapter_count": len(chapters),
        },
    }


# ---------------------------------------------------------------------------
# Top-level: playlist
# ---------------------------------------------------------------------------

def _extract_playlist(
    info: dict,
    source: str,
    languages: list[str],
    whisper_model: str,
    window_seconds: int,
    max_videos: int | None,
    use_whisper: bool,
) -> dict:
    import _compat  # noqa: F401
    from chunk import count_tokens

    playlist_title = info.get("title") or "Playlist"
    playlist_author = info.get("uploader") or info.get("channel") or ""
    entries = [e for e in (info.get("entries") or []) if e]
    if max_videos is not None:
        entries = entries[:max_videos]

    all_sections: list[dict] = []
    total_duration = 0
    video_ids: list[str] = []

    for idx, entry in enumerate(entries, 1):
        video_url = (
            entry.get("webpage_url")
            or entry.get("url")
            or (f"https://www.youtube.com/watch?v={entry['id']}" if entry.get("id") else None)
        )
        if not video_url:
            print(f"[video] skipping playlist entry {idx}: no URL", file=sys.stderr)
            continue

        # Flat playlist entries are minimal — re-fetch full metadata so we get
        # chapters/duration. Full extract_info on the playlist works too but is
        # slower; flat + per-entry refetch gives better progress visibility.
        try:
            video_info = entry if entry.get("duration") is not None else _fetch_metadata(video_url)
        except Exception as e:
            print(f"[video] metadata fetch failed for entry {idx}: {e}", file=sys.stderr)
            continue

        video_id = video_info.get("id", "") or ""
        video_title = video_info.get("title") or f"Video {idx}"
        video_chapters = video_info.get("chapters") or []
        total_duration += video_info.get("duration") or 0
        video_ids.append(video_id)

        try:
            segments, lang, is_gen, transcript_source = _resolve_transcript(
                video_url, video_id, languages, whisper_model, use_whisper,
            )
        except ValueError as e:
            print(f"[video] skipping entry {idx} ({video_title}): {e}", file=sys.stderr)
            continue

        prefix = f"Ep {idx:02d} — {video_title}: "
        video_sections = _segments_to_sections(
            segments, video_chapters, window_seconds, video_id, video_prefix=prefix,
        )
        for sec in video_sections:
            sec["metadata"]["playlist_index"] = idx
            sec["metadata"]["video_title"] = video_title
            sec["metadata"]["transcript_source"] = transcript_source
            sec["metadata"]["caption_language"] = lang
            sec["metadata"]["is_auto_generated"] = is_gen
        all_sections.extend(video_sections)

    total_tokens = sum(count_tokens(s["content"]) for s in all_sections)

    return {
        "source_type": "video_playlist",
        "source_path": source,
        "title": playlist_title,
        "author": playlist_author,
        "sections": all_sections,
        "images": [],
        "metadata": {
            "total_sections": len(all_sections),
            "total_tokens": total_tokens,
            "total_images": 0,
            "playlist_id": info.get("id", "") or "",
            "video_count": len(video_ids),
            "duration_seconds": total_duration,
            "channel": playlist_author,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_video(
    source: str,
    *,
    images_dir: str | None = None,  # ignored — videos produce no images
    languages: list[str] | None = None,
    whisper_model: str = "base",
    window_seconds: int = 300,
    max_videos: int | None = None,
    use_whisper: bool = True,
) -> dict:
    """Extract transcript from a video URL or a playlist URL.

    Args:
        source: URL (YouTube, Vimeo, etc.) or a local video file path.
        languages: Preferred caption languages in priority order (default: ['en']).
        whisper_model: faster-whisper model name ('tiny', 'base', 'small', ...).
        window_seconds: Section window size when the video has no chapters.
        max_videos: Cap on videos fetched from a playlist.
        use_whisper: If False, error when captions are absent instead of
            falling back to local Whisper transcription.

    Returns:
        Unified intermediate JSON dict.
    """
    del images_dir  # accepted for framework compat, but unused
    languages = languages or ["en"]

    # First try a flat playlist probe to detect playlist vs single video cheaply
    try:
        info = _fetch_metadata(source, flat_playlist=True)
    except Exception:
        info = _fetch_metadata(source)

    if info.get("_type") == "playlist" or "entries" in info:
        return _extract_playlist(
            info, source, languages, whisper_model, window_seconds, max_videos, use_whisper,
        )
    # For a single video, re-fetch with full metadata (chapters aren't in flat mode)
    full_info = _fetch_metadata(source) if info.get("_type") != "video" else info
    return _extract_single(
        full_info, source, languages, whisper_model, window_seconds, use_whisper,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Extract transcript from a video URL (YouTube, Vimeo, etc.) or playlist",
    )
    parser.add_argument("url", help="Video or playlist URL (or local video file path)")
    parser.add_argument("-o", "--output-dir", help="Save extracted JSON to this directory")
    parser.add_argument(
        "--lang", action="append", dest="languages", default=None,
        help="Preferred caption language (ISO code; repeat flag for fallbacks). Default: en",
    )
    parser.add_argument(
        "--whisper-model", default="base",
        help="faster-whisper model: tiny / base / small / medium / large-v3 (default: base)",
    )
    parser.add_argument(
        "--window-seconds", type=int, default=300,
        help="Section window size when no chapters are present (default: 300)",
    )
    parser.add_argument(
        "--max-videos", type=int, default=None,
        help="Cap on videos fetched from a playlist",
    )
    parser.add_argument(
        "--no-whisper", action="store_true",
        help="Disable Whisper fallback (error out if captions are unavailable)",
    )
    args = parser.parse_args()

    from extractors import extract_source
    result = extract_source(
        args.url,
        args.output_dir,
        languages=args.languages,
        whisper_model=args.whisper_model,
        window_seconds=args.window_seconds,
        max_videos=args.max_videos,
        use_whisper=not args.no_whisper,
    )
    if not args.output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        meta = result["metadata"]
        kind = result.get("source_type", "video")
        msg = f"Extracted {meta['total_sections']} sections, {meta['total_tokens']} tokens from {args.url} ({kind})"
        if "transcript_source" in meta:
            msg += f" [source={meta['transcript_source']}, lang={meta.get('caption_language', '?')}]"
        print(msg)
