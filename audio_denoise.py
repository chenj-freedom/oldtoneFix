"""Batch denoise for old recordings: narrowband hum or broadband floor noise."""

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".mp3", ".wav", ".flac"}
MODES = ("hum", "broadband")
PROCESSED_MARKER = "_processed"
DEFAULT_RNN_MODEL = Path(__file__).resolve().parent / "models" / "cb.rnnn"


@dataclass(frozen=True)
class ProcessResult:
    status: str
    detail: str = ""


def ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filtergraph option."""
    text = str(path.resolve()).replace("\\", "/").replace(":", "\\:")
    return f"'{text}'"


def build_filter_chain(
    mode: str = "hum", model_path: Path | None = None
) -> str:
    """Return the FFmpeg filter graph for the selected denoise mode."""
    if mode == "hum":
        return ",".join(
            (
                "highpass=f=28:p=2:r=f64",
                "bandreject=f=50.16:t=h:w=1.8:r=f64",
                "bandreject=f=150.49:t=h:w=3.5:r=f64",
            )
        )
    if mode == "broadband":
        # RNNoise with partial dry mix preserves consonants; milder FFT + soft
        # treble cut reduces residual hiss and harsh BGM edges.
        model = Path(model_path) if model_path is not None else DEFAULT_RNN_MODEL
        if not model.is_file():
            raise FileNotFoundError(f"RNNoise model not found: {model}")
        return ",".join(
            (
                "highpass=f=28:p=2:r=f64",
                f"arnndn=m={ffmpeg_filter_path(model)}:mix=0.78",
                "afftdn=nr=14:nf=-50:nt=white:tn=1",
                "treble=g=-2.5:f=7000:w=0.6",
            )
        )
    raise ValueError(f"Unsupported mode: {mode}")


def processed_filename(source: Path) -> str:
    """Return the output filename with a _processed suffix before the extension."""
    return f"{source.stem}{PROCESSED_MARKER}{source.suffix}"


def is_processed_output(path: Path) -> bool:
    """Return True when a path looks like a generated *_processed file."""
    return path.stem.endswith(PROCESSED_MARKER)


def find_audio_files(input_path: Path, output_root: Path | None) -> list[Path]:
    """Find supported inputs while excluding generated output files."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input does not exist: {input_path}")
    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported audio format: {input_path.suffix or '(none)'}")
        return [input_path]

    resolved_output = output_root.resolve() if output_root is not None else None
    files = (
        path
        for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    selected = []
    for path in files:
        if is_processed_output(path):
            continue
        if resolved_output is not None and path.resolve().is_relative_to(resolved_output):
            continue
        selected.append(path)
    return sorted(selected)


def resolve_output_root(_input_path: Path, requested: Path | None) -> Path | None:
    """Return the requested output directory, or None to write beside each source."""
    if requested is not None:
        return requested.resolve()
    return None


def destination_for(
    input_path: Path, source: Path, output_root: Path | None
) -> Path:
    """Map a source to its *_processed destination without overwriting the source."""
    name = processed_filename(source)
    if output_root is None:
        return (source.parent / name).resolve()
    if source == input_path:
        return (output_root / name).resolve()
    return (output_root / source.relative_to(input_path).with_name(name)).resolve()


def build_ffmpeg_command(
    ffmpeg: str,
    source: Path,
    temporary_output: Path,
    mode: str = "hum",
) -> list[str]:
    """Build an FFmpeg argument list for one supported output format."""
    codec_arguments = {
        ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        ".wav": ["-c:a", "pcm_s24le"],
        ".flac": ["-c:a", "flac"],
    }
    suffix = temporary_output.suffix.lower()
    if suffix not in codec_arguments:
        raise ValueError(f"Unsupported output format: {suffix or '(none)'}")

    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-map_metadata",
        "0",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        build_filter_chain(mode),
        *codec_arguments[suffix],
        str(temporary_output),
    ]


def process_file(
    ffmpeg: str,
    source: Path,
    destination: Path,
    keep_existing: bool,
    mode: str = "hum",
    runner=None,
) -> ProcessResult:
    """Process one source and publish its output only after FFmpeg succeeds."""
    if runner is None:
        runner = subprocess.run
    if destination.exists() and keep_existing:
        return ProcessResult("skipped", "output already exists")

    temporary_output = destination.with_name(
        f".{destination.stem}.{uuid.uuid4().hex}.tmp{destination.suffix}"
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = build_ffmpeg_command(ffmpeg, source, temporary_output, mode)
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "FFmpeg failed").strip()
            return ProcessResult("failed", detail)
        if not temporary_output.exists():
            return ProcessResult("failed", "FFmpeg reported success but produced no output")

        os.replace(temporary_output, destination)
        return ProcessResult("processed")
    except OSError as error:
        return ProcessResult("failed", str(error))
    finally:
        if temporary_output.exists():
            temporary_output.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Denoise old recordings (narrowband hum or broadband floor noise)."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="input audio file or directory",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output directory (default: same directory as each source file)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=MODES,
        default="hum",
        help="denoise mode: hum (default) or broadband",
    )
    parser.add_argument(
        "-k",
        "--keep-existing",
        action="store_true",
        help="keep and skip existing output files",
    )
    return parser.parse_args(argv)


def run(
    input_path: Path,
    output_root: Path | None,
    keep_existing: bool,
    ffmpeg: str,
    mode: str = "hum",
) -> int:
    """Process all selected files and return a shell-friendly exit code."""
    input_path = input_path.resolve()
    output_root = resolve_output_root(input_path, output_root)
    try:
        sources = find_audio_files(input_path, output_root)
    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not sources:
        print("Error: no supported audio files found", file=sys.stderr)
        return 1

    counts = {"processed": 0, "skipped": 0, "failed": 0}
    for source in sources:
        destination = destination_for(input_path, source, output_root)
        if destination == source.resolve():
            counts["failed"] += 1
            print(
                f"[FAIL] {source}: output would replace the source file",
                file=sys.stderr,
            )
            continue

        result = process_file(ffmpeg, source, destination, keep_existing, mode)
        counts[result.status] += 1
        if result.status == "processed":
            print(f"[OK] {source} -> {destination}")
        elif result.status == "skipped":
            print(f"[SKIP] {source}: {result.detail}")
        else:
            print(f"[FAIL] {source}: {result.detail}")

    print(
        "Summary: "
        f"processed={counts['processed']} "
        f"skipped={counts['skipped']} "
        f"failed={counts['failed']}"
    )
    return 1 if counts["failed"] else 0


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_output()
    arguments = parse_args(argv)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print(
            "Error: FFmpeg was not found. Install FFmpeg and add it to PATH.",
            file=sys.stderr,
        )
        return 1
    return run(
        arguments.input,
        arguments.output,
        arguments.keep_existing,
        ffmpeg,
        arguments.mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
