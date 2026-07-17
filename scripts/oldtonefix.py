"""Batch denoise for old recordings with continuous floor noise."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".mp3", ".wav", ".flac"}
PROCESSED_MARKER = "_processed"
PROGRESS_PREFIX = "@@OLDTONEFIX_PROGRESS@@"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RNN_MODEL = REPO_ROOT / "models" / "cb.rnnn"
AFFTDN_NOISE_TYPES = ("white", "vinyl", "shellac")


@dataclass(frozen=True)
class ProcessResult:
    status: str
    detail: str = ""


@dataclass(frozen=True)
class DenoiseTune:
    """Tunable denoise parameters. Defaults match the validated listening preset."""

    highpass_hz: float = 28.0
    rnnoise_mix: float = 0.78
    afftdn_nr: float = 14.0
    afftdn_nf: float = -50.0
    afftdn_nt: str = "white"
    afftdn_tn: bool = True
    treble_gain: float = -2.5
    treble_hz: float = 7000.0
    treble_width: float = 0.6


def ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filtergraph option."""
    text = str(path.resolve()).replace("\\", "/").replace(":", "\\:")
    return f"'{text}'"


def build_filter_chain(tune: DenoiseTune | None = None) -> str:
    """Return the FFmpeg filter graph for speech-aware floor-noise reduction."""
    settings = tune if tune is not None else DenoiseTune()
    if not DEFAULT_RNN_MODEL.is_file():
        raise FileNotFoundError(f"RNNoise model not found: {DEFAULT_RNN_MODEL}")
    track_noise = 1 if settings.afftdn_tn else 0
    return ",".join(
        (
            f"highpass=f={settings.highpass_hz:g}:p=2:r=f64",
            f"arnndn=m={ffmpeg_filter_path(DEFAULT_RNN_MODEL)}:mix={settings.rnnoise_mix:g}",
            (
                f"afftdn=nr={settings.afftdn_nr:g}:nf={settings.afftdn_nf:g}"
                f":nt={settings.afftdn_nt}:tn={track_noise}"
            ),
            (
                f"treble=g={settings.treble_gain:g}"
                f":f={settings.treble_hz:g}:w={settings.treble_width:g}"
            ),
        )
    )


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

    resolved_input = input_path.resolve()
    resolved_output = output_root.resolve() if output_root is not None else None
    if resolved_output == resolved_input:
        resolved_output = None
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
    """Map a source to a destination, adding _processed only to avoid the source."""
    if output_root is None:
        return (source.parent / processed_filename(source)).resolve()
    if source == input_path:
        candidate = (output_root / source.name).resolve()
    else:
        candidate = (output_root / source.relative_to(input_path)).resolve()
    if candidate == source.resolve():
        return candidate.with_name(processed_filename(source))
    return candidate


def build_ffmpeg_command(
    ffmpeg: str,
    source: Path,
    temporary_output: Path,
    tune: DenoiseTune | None = None,
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
        build_filter_chain(tune),
        *codec_arguments[suffix],
        str(temporary_output),
    ]


def process_file(
    ffmpeg: str,
    source: Path,
    destination: Path,
    keep_existing: bool,
    tune: DenoiseTune | None = None,
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
        command = build_ffmpeg_command(ffmpeg, source, temporary_output, tune)
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


def _float_in_range(name: str, low: float, high: float):
    def converter(text: str) -> float:
        try:
            value = float(text)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from error
        if value < low or value > high:
            raise argparse.ArgumentTypeError(
                f"{name} must be in [{low:g}, {high:g}], got {value:g}"
            )
        return value

    return converter


def tune_from_args(arguments: argparse.Namespace) -> DenoiseTune:
    """Build DenoiseTune from parsed CLI arguments."""
    return DenoiseTune(
        highpass_hz=arguments.highpass_hz,
        rnnoise_mix=arguments.rnnoise_mix,
        afftdn_nr=arguments.afftdn_nr,
        afftdn_nf=arguments.afftdn_nf,
        afftdn_nt=arguments.afftdn_nt,
        afftdn_tn=arguments.afftdn_tn,
        treble_gain=arguments.treble_gain,
        treble_hz=arguments.treble_hz,
        treble_width=arguments.treble_width,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = DenoiseTune()
    parser = argparse.ArgumentParser(
        description="Denoise old recordings with continuous floor noise.",
        formatter_class=argparse.RawTextHelpFormatter,
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
        help=(
            "output directory; a different directory keeps original filenames "
            "(default: write *_processed beside each source)"
        ),
    )
    parser.add_argument(
        "-k",
        "--keep-existing",
        action="store_true",
        help="keep and skip existing output files",
    )
    parser.add_argument(
        "--progress-json",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    tune = parser.add_argument_group(
        "tuning options",
        "Listening tweaks. Omit to use the validated defaults.",
    )
    tune.add_argument(
        "--highpass-hz",
        type=_float_in_range("highpass-hz", 1.0, 500.0),
        default=defaults.highpass_hz,
        metavar="HZ",
        help=(
            f"High-pass cutoff in Hz. Range 1~500, default {defaults.highpass_hz:g}.\n"
            "Effect: removes sub-bass rumble / DC offset.\n"
            "Lower: keeps more deep bass, warmer low end.\n"
            "Higher: cuts more low rumble, cleaner but thinner tone."
        ),
    )
    tune.add_argument(
        "--rnnoise-mix",
        type=_float_in_range("rnnoise-mix", -1.0, 1.0),
        default=defaults.rnnoise_mix,
        metavar="MIX",
        help=(
            f"RNNoise wet/dry mix. Range -1~1, default {defaults.rnnoise_mix:g}.\n"
            "Effect: blends denoised output with the original (main denoise strength).\n"
            "Lower: closer to original, clearer consonants, more residual noise.\n"
            "Higher: stronger denoise, less hiss, more risk of swallowed speech."
        ),
    )
    tune.add_argument(
        "--afftdn-nr",
        type=_float_in_range("afftdn-nr", 0.01, 97.0),
        default=defaults.afftdn_nr,
        metavar="DB",
        help=(
            f"FFT denoise strength (nr). Range 0.01~97, default {defaults.afftdn_nr:g}.\n"
            "Effect: further reduces residual hiss after RNNoise.\n"
            "Lower: more residual hiss, more speech detail kept.\n"
            "Higher: less hiss, more risk of dull / airless speech."
        ),
    )
    tune.add_argument(
        "--afftdn-nf",
        type=_float_in_range("afftdn-nf", -80.0, -20.0),
        default=defaults.afftdn_nf,
        metavar="DB",
        help=(
            f"FFT noise floor (nf) in dB. Range -80~-20, default {defaults.afftdn_nf:g}.\n"
            "Effect: assumed noise-floor level for FFT denoise.\n"
            "Lower (more negative): treats noise as quieter, more conservative.\n"
            "Higher (closer to 0): treats noise as louder, more aggressive."
        ),
    )
    tune.add_argument(
        "--afftdn-nt",
        choices=AFFTDN_NOISE_TYPES,
        default=defaults.afftdn_nt,
        metavar="TYPE",
        help=(
            f"FFT noise-type profile. Choices: {', '.join(AFFTDN_NOISE_TYPES)}; "
            f"default {defaults.afftdn_nt}.\n"
            "Effect: spectral shape used to estimate floor noise.\n"
            "white: flatter hiss.\n"
            "vinyl/shellac: more low-frequency-weighted floor noise."
        ),
    )
    tune.add_argument(
        "--afftdn-tn",
        action=argparse.BooleanOptionalAction,
        default=defaults.afftdn_tn,
        help=(
            f"Enable noise tracking. Default: "
            f"{'on' if defaults.afftdn_tn else 'off'} (--afftdn-tn / --no-afftdn-tn).\n"
            "Effect: updates the noise estimate over time.\n"
            "On: adapts better across changing sections.\n"
            "Off: fixed estimate; sometimes steadier, sometimes more residual."
        ),
    )
    tune.add_argument(
        "--treble-gain",
        type=_float_in_range("treble-gain", -20.0, 20.0),
        default=defaults.treble_gain,
        metavar="DB",
        help=(
            f"Treble shelf gain in dB. Range -20~20, default {defaults.treble_gain:g}.\n"
            "Effect: softens harsh BGM highs or restores air/sibilance.\n"
            "Lower (more negative): darker highs, less harshness, possibly duller speech.\n"
            "Higher (more positive): brighter highs, clearer but more harshness risk."
        ),
    )
    tune.add_argument(
        "--treble-hz",
        type=_float_in_range("treble-hz", 1000.0, 16000.0),
        default=defaults.treble_hz,
        metavar="HZ",
        help=(
            f"Treble shelf center frequency in Hz. Range 1000~16000, "
            f"default {defaults.treble_hz:g}.\n"
            "Effect: where the treble shelf starts acting.\n"
            "Lower: affects a wider upper-mid/high band.\n"
            "Higher: mainly the top air band, more localized."
        ),
    )
    tune.add_argument(
        "--treble-width",
        type=_float_in_range("treble-width", 0.01, 5.0),
        default=defaults.treble_width,
        metavar="W",
        help=(
            f"Treble shelf width. Range 0.01~5, default {defaults.treble_width:g}.\n"
            "Effect: how wide the treble transition is.\n"
            "Lower: steeper, more localized change.\n"
            "Higher: gentler transition across a wider band."
        ),
    )
    return parser.parse_args(argv)


def run(
    input_path: Path,
    output_root: Path | None,
    keep_existing: bool,
    ffmpeg: str,
    tune: DenoiseTune | None = None,
    progress_json: bool = False,
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

    total = len(sources)
    print(f"Found {total} supported audio file(s).", flush=True)
    if progress_json:
        _emit_progress(0, total)

    counts = {"processed": 0, "skipped": 0, "failed": 0}
    for completed, source in enumerate(sources, start=1):
        destination = destination_for(input_path, source, output_root)
        if destination == source.resolve():
            counts["failed"] += 1
            print(
                f"[FAIL] {source}: output would replace the source file",
                file=sys.stderr,
                flush=True,
            )
            if progress_json:
                _emit_progress(completed, total, source, "failed")
            continue

        result = process_file(ffmpeg, source, destination, keep_existing, tune)
        counts[result.status] += 1
        if result.status == "processed":
            print(f"[OK] {source} -> {destination}", flush=True)
        elif result.status == "skipped":
            print(f"[SKIP] {source}: {result.detail}", flush=True)
        else:
            print(f"[FAIL] {source}: {result.detail}", flush=True)
        if progress_json:
            _emit_progress(completed, total, source, result.status)

    print(
        "Summary: "
        f"processed={counts['processed']} "
        f"skipped={counts['skipped']} "
        f"failed={counts['failed']}",
        flush=True,
    )
    return 1 if counts["failed"] else 0


def _emit_progress(
    completed: int,
    total: int,
    source: Path | None = None,
    status: str | None = None,
) -> None:
    event = {
        "completed": completed,
        "total": total,
        "current": str(source) if source is not None else "",
        "status": status or "scanned",
    }
    print(f"{PROGRESS_PREFIX}{json.dumps(event, ensure_ascii=False)}", flush=True)


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=True,
            )


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
        tune_from_args(arguments),
        arguments.progress_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
