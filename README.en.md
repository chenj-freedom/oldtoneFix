<p align="center">
  <a href="https://github.com/chenj-freedom/oldtoneFix">
    <img src="assets/brand/oldtonefix-banner.png" alt="oldtoneFix — Restore old recordings. Keep every word." width="100%">
  </a>
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-58C7BE?style=for-the-badge&amp;logo=python&amp;logoColor=F4F0E8&amp;labelColor=15191E"></a>
  <a href="https://ffmpeg.org/"><img alt="FFmpeg required" src="https://img.shields.io/badge/FFmpeg-required-D89A45?style=for-the-badge&amp;logo=ffmpeg&amp;logoColor=F4F0E8&amp;labelColor=15191E"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-58C7BE?style=for-the-badge&amp;labelColor=15191E"></a>
  <a href="#output-behavior"><img alt="MP3 WAV FLAC" src="https://img.shields.io/badge/Audio-MP3%20%7C%20WAV%20%7C%20FLAC-D89A45?style=for-the-badge&amp;labelColor=15191E"></a>
</p>

<p align="center">
  <a href="README.md">中文</a> · <strong>English</strong>
</p>

`oldtoneFix` is a batch denoising tool for old spoken-word recordings, narration, and audiobooks. It reduces continuous broadband floor noise, hiss, and electrical noise while preserving articulation and the character of the original voice.

## Highlights

- **Speech-first processing**: RNNoise and FFT denoising work together without optimizing only for silence.
- **Batch friendly**: process one audio file or recursively process a directory.
- **Safe publishing**: write to a temporary file and atomically replace the destination only after success.
- **Structure preserving**: keep metadata, file extensions, and relative input paths.
- **Tunable**: control the high-pass filter, RNNoise mix, FFT noise profile, and treble shaping independently.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) available on `PATH`
- The bundled RNNoise model: `models/cb.rnnn`

## Browser UI

The recommended local browser UI exposes every tuning value as a slider with its validated default position, current value, purpose, and clear lower/higher audible effects. Batch jobs show real-time progress, completed-file counts, and per-file logs. Use the `中文 / EN` switch in the upper-right corner at any time; the browser remembers the selected language and switching never interrupts the current task.

Windows:

```text
Double-click start_web.bat
```

macOS:

```text
Double-click start_web.command
```

If macOS says the file is not executable, run `chmod +x start_web.command` once in the project directory, then double-click it again.

You can also start it from a terminal:

```powershell
python .\scripts\oldtonefix_web.py
```

It opens the browser automatically. Use `python .\scripts\oldtonefix_web.py --no-open` to keep it closed and visit the local address printed in the terminal. The UI calls the same `scripts/oldtonefix.py` processing pipeline, so FFmpeg and `models/cb.rnnn` are still required.

## Quick Start

Full command form (`[]` = optional):

```powershell
python .\scripts\oldtonefix.py -i <input> [-o <output-dir>] [-k]
  [--highpass-hz <Hz>] [--rnnoise-mix <MIX>] [--afftdn-nr <dB>] [--afftdn-nf <dB>]
  [--afftdn-nt white|vinyl|shellac] [--afftdn-tn | --no-afftdn-tn]
  [--treble-gain <dB>] [--treble-hz <Hz>] [--treble-width <W>]
```

Process one file:

```powershell
python .\scripts\oldtonefix.py -i "samples\recording.mp3"
```

Process a directory recursively and choose an output directory:

```powershell
python .\scripts\oldtonefix.py -i "samples" -o "samples\out"
```

Preserve existing outputs:

```powershell
python .\scripts\oldtonefix.py -i "samples" --output "samples\out" --keep-existing
```

Tune the denoise strength:

```powershell
python .\scripts\oldtonefix.py -i "samples\recording.mp3" --rnnoise-mix 0.7 --afftdn-nr 12 --treble-gain -3
```

Show the complete CLI help:

```powershell
python .\scripts\oldtonefix.py --help
```

## Output Behavior

- By default, outputs are written beside each source as `*_processed`, for example `old recording_processed.mp3`.
- With `-o` / `--output` pointing to a different directory, oldtoneFix keeps the original filename; directory processing also preserves relative paths.
- If the requested output location would be the source file itself, oldtoneFix adds `_processed` instead and never overwrites the source.
- Existing destination files are **overwritten by default**.
- Use `-k` / `--keep-existing` to preserve and skip existing outputs.
- `.mp3` uses `libmp3lame -q:a 2`, `.wav` uses 24-bit PCM, and `.flac` remains lossless.

## Processing Pipeline

```text
Input → high-pass → RNNoise → FFT denoise → treble shaping → original-format output
```

| Stage | Purpose |
|-------|---------|
| High-pass | Remove DC offset and sub-bass rumble |
| RNNoise | Apply the main reduction of continuous floor noise |
| `afftdn` | Reduce residual hiss after RNNoise |
| Treble | Soften harsh highs or restore air when needed |

## CLI Options

| Option | Required | Description |
|--------|----------|-------------|
| `-i` / `--input` | Required | Input audio file or directory |
| `-o` / `--output` | Optional | Output directory; a different directory keeps the original filename, while an omitted value writes `*_processed` beside the source |
| `-k` / `--keep-existing` | Optional | Preserve and skip existing outputs |
| `-h` / `--help` | Optional | Show the English command-line help |

### Tuning Options

Omit tuning options to use the validated listening preset.

| Option | Required | Range | Default | Effect | Lower | Higher |
|--------|----------|-------|---------|--------|-------|--------|
| `--highpass-hz` | Optional | 1~500 | 28 | Cut sub-bass rumble | Warmer, fuller lows | Cleaner, thinner lows |
| `--rnnoise-mix` | Optional | -1~1 | 0.78 | RNNoise wet/dry mix and main denoise strength | More speech detail and residual noise | Less hiss and more risk of swallowed speech |
| `--afftdn-nr` | Optional | 0.01~97 | 14 | Reduce residual hiss with FFT denoising | More detail and hiss | Less hiss and duller speech |
| `--afftdn-nf` | Optional | -80~-20 | -50 | Assumed FFT noise floor in dB | More conservative | More aggressive |
| `--afftdn-nt` | Optional | white / vinyl / shellac | white | Noise spectrum profile | — | white favors hiss; vinyl/shellac favor low-weighted floor noise |
| `--afftdn-tn` / `--no-afftdn-tn` | Optional | on/off | on | Update the noise estimate over time | — | On adapts; off keeps the estimate fixed |
| `--treble-gain` | Optional | -20~20 | -2.5 | Treble shelf gain in dB | Darker and less harsh | Brighter with more harshness risk |
| `--treble-hz` | Optional | 1000~16000 | 7000 | Treble shelf center frequency | Affects a wider upper band | Focuses on the top-air band |
| `--treble-width` | Optional | 0.01~5 | 0.6 | Treble shelf transition width | Steeper and more localized | Gentler and wider |

## Tests

```powershell
python -m unittest discover -s tests -v
```

## License

This project is available under the [MIT License](LICENSE).
