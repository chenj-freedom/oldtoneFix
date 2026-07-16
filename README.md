<p align="center">
  <a href="https://github.com/chenj-freedom/oldtoneFix">
    <img src="assets/brand/oldtonefix-banner.png" alt="oldtoneFix — Restore old recordings. Keep every word." width="100%">
  </a>
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-58C7BE?style=for-the-badge&amp;logo=python&amp;logoColor=F4F0E8&amp;labelColor=15191E"></a>
  <a href="https://ffmpeg.org/"><img alt="FFmpeg required" src="https://img.shields.io/badge/FFmpeg-required-D89A45?style=for-the-badge&amp;logo=ffmpeg&amp;logoColor=F4F0E8&amp;labelColor=15191E"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-58C7BE?style=for-the-badge&amp;labelColor=15191E"></a>
  <a href="#输出行为"><img alt="MP3 WAV FLAC" src="https://img.shields.io/badge/Audio-MP3%20%7C%20WAV%20%7C%20FLAC-D89A45?style=for-the-badge&amp;labelColor=15191E"></a>
</p>

<p align="center">
  <strong>中文</strong> · <a href="README.en.md">English</a>
</p>

`oldtoneFix` 是面向朗读、旁白和有声资料的老录音批量降噪工具。它压低持续的宽带底噪、白噪声和电流嘶声，同时尽量保留口齿清晰度与原始音色。

## 功能亮点

- **人声优先**：RNNoise 与 FFT 降噪组合，避免只追求“安静”而吞掉字音。
- **批量处理**：支持单个音频文件或递归处理整个目录。
- **安全输出**：使用临时文件处理，成功后再原子替换目标文件。
- **结构保留**：保留元数据、文件扩展名和输入目录的相对结构。
- **参数可调**：可独立控制高通、RNNoise、FFT 噪声模型和高频修整。

## 环境要求

- Python 3.10+
- 已加入 `PATH` 的 [FFmpeg](https://ffmpeg.org/)
- 仓库内的 RNNoise 模型：`models/cb.rnnn`

## 快速开始

完整命令形式（`[]` 表示可选）：

```powershell
python audio_denoise.py -i <输入路径> [-o <输出目录>] [-k]
  [--highpass-hz <Hz>] [--rnnoise-mix <MIX>] [--afftdn-nr <dB>] [--afftdn-nf <dB>]
  [--afftdn-nt white|vinyl|shellac] [--afftdn-tn | --no-afftdn-tn]
  [--treble-gain <dB>] [--treble-hz <Hz>] [--treble-width <W>]
```

处理单个文件：

```powershell
python audio_denoise.py -i "samples\recording.mp3"
```

递归处理目录并指定输出目录：

```powershell
python audio_denoise.py -i "samples" -o "samples\out"
```

保留已经存在的输出文件：

```powershell
python audio_denoise.py -i "samples" --output "samples\out" --keep-existing
```

调整降噪强度：

```powershell
python audio_denoise.py -i "samples\recording.mp3" --rnnoise-mix 0.7 --afftdn-nr 12 --treble-gain -3
```

查看完整命令行帮助：

```powershell
python audio_denoise.py --help
```

## 输出行为

- 默认在源文件旁生成 `*_processed`，例如 `old recording_processed.mp3`。
- 指定 `-o` / `--output` 后，目录批处理会在输出目录中保留相对路径。
- **默认覆盖**已经存在的目标文件。
- 使用 `-k` / `--keep-existing` 可保留并跳过已经存在的输出。
- `.mp3` 使用 `libmp3lame -q:a 2`，`.wav` 使用 24-bit PCM，`.flac` 保持无损编码。

## 处理链路

```text
输入音频 → 高通滤波 → RNNoise → FFT 降噪 → 高频修整 → 原格式输出
```

| 阶段 | 作用 |
|------|------|
| 高通滤波 | 去除直流偏移和极低频隆隆声 |
| RNNoise | 作为主要降噪器压低连续底噪 |
| `afftdn` | 进一步处理 RNNoise 后残留的嘶声 |
| treble | 柔化刺耳高频，或按需恢复空气感 |

## 命令行参数

| 参数 | 是否可选 | 说明 |
|------|----------|------|
| `-i` / `--input` | 必填 | 输入音频文件或目录 |
| `-o` / `--output` | 可选 | 输出目录；默认写在每个源文件旁 |
| `-k` / `--keep-existing` | 可选 | 保留并跳过已经存在的输出 |
| `-h` / `--help` | 可选 | 显示英文命令行帮助 |

### 调优参数

不传调优参数时使用经过试听验证的默认预设。

| 参数 | 是否可选 | 范围 | 默认 | 作用 | 越小 | 越大 |
|------|----------|------|------|------|------|------|
| `--highpass-hz` | 可选 | 1~500 | 28 | 切除极低频隆隆声 | 低频更厚 | 更干净、更薄 |
| `--rnnoise-mix` | 可选 | -1~1 | 0.78 | RNNoise 干湿比，主要降噪强度 | 更保字、底噪更多 | 更干净、易吞字 |
| `--afftdn-nr` | 可选 | 0.01~97 | 14 | FFT 再压低残留嘶声 | 细节与嘶声更多 | 嘶声更少、易发闷 |
| `--afftdn-nf` | 可选 | -80~-20 | -50 | FFT 假定噪声地板，单位 dB | 更保守 | 更激进 |
| `--afftdn-nt` | 可选 | white / vinyl / shellac | white | 噪声频谱模型 | — | white 偏嘶声；vinyl/shellac 偏低频底噪 |
| `--afftdn-tn` / `--no-afftdn-tn` | 可选 | on/off | on | 是否随时间更新噪声估计 | — | 开启更适应变化；关闭更固定 |
| `--treble-gain` | 可选 | -20~20 | -2.5 | 高频搁架增益，单位 dB | 更暗、较少刺耳 | 更亮、更易发尖 |
| `--treble-hz` | 可选 | 1000~16000 | 7000 | 高频搁架中心频率 | 影响范围更宽 | 更偏超高频 |
| `--treble-width` | 可选 | 0.01~5 | 0.6 | 高频搁架过渡宽度 | 更陡、更局部 | 更缓、更宽 |

## 测试

```powershell
python -m unittest discover -s test -v
```

## 许可证

本项目采用 [MIT License](LICENSE)。
