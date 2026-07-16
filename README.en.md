# oldtoneFix

[中文](README.md)

Batch denoise for old recordings: reduce continuous broadband floor noise (hiss / electrical noise) on spoken narration while keeping articulation.

Pipeline: high-pass → RNNoise (`models/cb.rnnn`) → `afftdn` → treble. Supports single files or recursive directories; writes `*_processed` outputs; keeps metadata and relative paths.

## Requirements

- Python 3.10+
- FFmpeg on `PATH`
- `models/cb.rnnn`

## Usage

```powershell
python audio_denoise.py -i "C:\audio\old recording.mp3"
python audio_denoise.py -i "C:\audio\album" -o "C:\audio\out" --keep-existing
python audio_denoise.py -i "a.mp3" --rnnoise-mix 0.7 --afftdn-nr 12 --treble-gain -3
```

Example output: `old recording_processed.mp3` (same folder as the source by default).

## Options

| Option | Description |
|--------|-------------|
| `-i` / `--input` | Input file or directory |
| `-o` / `--output` | Output directory (default: beside each source) |
| `-k` / `--keep-existing` | Skip existing outputs |
| `-h` / `--help` | CLI help (English) |

### Tuning options

Defaults are the validated listening preset. Full English help: `python audio_denoise.py -h`

| Option | Range | Default | Effect | Lower | Higher |
|--------|-------|---------|--------|-------|--------|
| `--highpass-hz` | 1~500 | 28 | Cut sub-bass rumble | Warmer lows | Cleaner, thinner |
| `--rnnoise-mix` | -1~1 | 0.78 | RNNoise wet/dry (main strength) | More natural speech, more noise | Cleaner, more swallowed speech |
| `--afftdn-nr` | 0.01~97 | 14 | Extra FFT hiss reduction | More detail and hiss | Less hiss, duller |
| `--afftdn-nf` | -80~-20 | -50 | FFT noise floor (dB) | More conservative | More aggressive |
| `--afftdn-nt` | white / vinyl / shellac | white | Noise spectrum profile | — | white≈hiss; vinyl/shellac≈low-weighted floor |
| `--afftdn-tn` / `--no-afftdn-tn` | on/off | on | Noise tracking | — | on=adapts; off=fixed estimate |
| `--treble-gain` | -20~20 | -2.5 | Treble shelf gain (dB) | Darker, less harsh | Brighter, harsher |
| `--treble-hz` | 1000~16000 | 7000 | Shelf center frequency | Wider band affected | More top-air only |
| `--treble-width` | 0.01~5 | 0.6 | Shelf width | Steeper, local | Wider, gentler |

Output codecs: `.mp3` (`libmp3lame -q:a 2`), `.wav` (24-bit PCM), `.flac` (lossless).

## Tests

```powershell
python -m unittest discover -s test -v
```
