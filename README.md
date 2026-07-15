# oldtoneFix

`oldtoneFix` 是一个面向老录音的批量降噪命令行工具。支持两种模式：针对稳定工频嗡声的窄带 `hum`，以及针对持续宽带底噪的 `broadband`。

## 处理内容

### `hum`（默认）

- 28 Hz 高通：去除直流偏移和不可用的次声成分。
- 50.16 Hz、带宽 1.8 Hz 的窄陷波：降低主工频嗡声。
- 150.49 Hz、带宽 3.5 Hz 的窄陷波：降低明显的三次谐波。

### `broadband`

- 28 Hz 高通。
- FFmpeg `arnndn`（RNNoise，`models/cb.rnnn`，`mix=0.78`）：降噪同时回掺部分干声，减轻吞字。
- 较温和的 `afftdn` 清残留嘶声。
- 轻微衰减 7 kHz 附近高频，缓和背景乐发尖。

## 环境要求

- Python 3.10 或更高版本。
- [FFmpeg](https://ffmpeg.org/) 已安装，并且 `ffmpeg` 命令位于系统 `PATH` 中。
- 不需要安装第三方 Python 包。

可用以下命令检查环境：

```powershell
python --version
ffmpeg -version
```

## 使用方法

处理单个文件（默认 hum，输出到源文件同目录）：

```powershell
python audio_denoise.py -i "C:\audio\old recording.mp3"
```

会生成：`C:\audio\old recording_processed.mp3`

宽带底噪模式：

```powershell
python audio_denoise.py -i "C:\audio\old recording.mp3" -m broadband
```

递归处理整个目录：

```powershell
python audio_denoise.py -i "C:\audio\唐诗宋词"
```

指定输出目录：

```powershell
python audio_denoise.py -i "C:\audio\唐诗宋词" --output "C:\audio\处理结果"
```

默认会覆盖已有 `*_processed` 输出。如果需要保留已有文件并跳过它们：

```powershell
python audio_denoise.py -i "C:\audio\唐诗宋词" -o "C:\audio\处理结果" --keep-existing
```

## 参数说明

- `-i` / `--input`：必填，输入单个音频文件或需要递归处理的目录。
- `-o` / `--output`：可选，指定输出目录；不传时输出到每个源文件所在目录。
- `-m` / `--mode`：可选，`hum`（默认）或 `broadband`。
- `-k` / `--keep-existing`：可选，保留并跳过已存在的输出文件。不传时默认覆盖已有 `*_processed` 文件。
- `-h` / `--help`：显示帮助信息并退出。

## 输入与输出

- 支持 `.mp3`、`.wav`、`.flac`，扩展名不区分大小写。
- 输出文件名在扩展名前加 `_processed`（如 `a.mp3` → `a_processed.mp3`），不会覆盖源文件。
- 未指定 `-o` 时，输出写在源文件同目录。
- 指定 `-o` 时，输出写到该目录；目录批处理会保留相对子目录结构。
- 扫描目录时会跳过已有的 `*_processed` 文件，以及位于 `-o` 输出目录下的文件。
- 已有 `*_processed` 输出默认被覆盖；传入 `-k` 或 `--keep-existing` 时才会跳过。
- MP3 使用 `libmp3lame -q:a 2`，WAV 使用 24 位 PCM，FLAC 使用无损编码。
- 尽量复制原音频元数据；源文件不会被修改或删除。
- 每个文件先写入临时文件，FFmpeg 成功后才发布为最终输出。单个文件失败不会阻断后续文件。

## 运行测试

```powershell
python -m unittest discover -s test -v
```

测试不访问网络；其中一项集成测试会调用本机 FFmpeg，验证坏文件不会阻断后续有效文件。

## 适用范围

- `hum`：适合有明确 50 Hz 类工频尖峰的旧录音。
- `broadband`：适合朗读旁白上的持续白噪声/电流底噪；依赖仓库内 `models/cb.rnnn`。人声可能略被压一点，可按听感再调。
