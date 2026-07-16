# oldtoneFix

[English](README.en.md)

老录音批量降噪：压低朗读/旁白上的持续宽带底噪（白噪声、电流嘶声等），并尽量保留口齿清晰度。

处理链路：高通 → RNNoise（`models/cb.rnnn`）→ `afftdn` → treble。支持单文件或目录批处理；输出为 `*_processed`；保留元数据与相对目录结构。

## 环境

- Python 3.10+
- PATH 中的 FFmpeg
- `models/cb.rnnn`

## 用法

```powershell
python audio_denoise.py -i "C:\audio\old recording.mp3"
python audio_denoise.py -i "C:\audio\唐诗宋词" -o "C:\audio\out" --keep-existing
python audio_denoise.py -i "a.mp3" --rnnoise-mix 0.7 --afftdn-nr 12 --treble-gain -3
```

输出示例：`old recording_processed.mp3`（默认写在源文件同目录）。

## 参数

| 参数 | 说明 |
|------|------|
| `-i` / `--input` | 输入文件或目录 |
| `-o` / `--output` | 输出目录（默认：源文件同目录） |
| `-k` / `--keep-existing` | 跳过已存在的输出 |
| `-h` / `--help` | 命令行帮助 |

### 调优参数

不传则使用默认预设。CLI 帮助为英文：`python audio_denoise.py -h`

| 参数 | 范围 | 默认 | 作用 | 越小 | 越大 |
|------|------|------|------|------|------|
| `--highpass-hz` | 1~500 | 28 | 切极低频隆隆 | 低频更厚 | 更干净、更薄 |
| `--rnnoise-mix` | -1~1 | 0.78 | RNNoise 干湿比（主强度） | 更保字、底噪多 | 更干净、易吞字 |
| `--afftdn-nr` | 0.01~97 | 14 | FFT 再压残留嘶声 | 细节多、嘶声多 | 嘶声少、易发闷 |
| `--afftdn-nf` | -80~-20 | -50 | FFT 噪声地板 (dB) | 更保守 | 更激进 |
| `--afftdn-nt` | white / vinyl / shellac | white | 噪声频谱假设 | — | white 偏嘶声；vinyl/shellac 偏低频底噪 |
| `--afftdn-tn` / `--no-afftdn-tn` | on/off | on | 噪声跟踪 | — | 开=更适应变化；关=估计更固定 |
| `--treble-gain` | -20~20 | -2.5 | 高频搁架增益 (dB) | 更暗、少刺耳 | 更亮、易发尖 |
| `--treble-hz` | 1000~16000 | 7000 | 搁架中心频率 | 影响更宽 | 更偏超高频 |
| `--treble-width` | 0.01~5 | 0.6 | 搁架宽度 | 更陡、更局部 | 更缓、更宽 |

输出编码：`.mp3`（`libmp3lame -q:a 2`）、`.wav`（24-bit PCM）、`.flac`（无损）。

## 测试

```powershell
python -m unittest discover -s test -v
```
