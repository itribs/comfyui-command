# ComfyUI Command Node

Execute shell commands/scripts directly within ComfyUI workflows, with streaming output and dynamic input slots.

![demo](demo.png)

## Features

- **Command Execution**: Run arbitrary shell commands and scripts via `/bin/bash` (configurable)
- **Dynamic Input Slots**: Automatically expand input slots on connection (up to 20), supporting images, audio, video, text, and more
- **Placeholder Substitution**: Use `{input0}`, `{input1}`, `{seed}` placeholders in commands to reference inputs and seed
- **Environment Variables**: Access parameters via `$INPUT_0`, `$INPUT_1`, `$SEED`, `$INPUT_COUNT`, `$INPUT_ALL`
- **Streaming Output**: Real-time stdout/stderr display with auto-scrolling output panel
- **Result Caching**: SHA256 fingerprint-based cache to avoid re-executing identical commands
- **Timeout Protection**: Configurable timeout (1-3600 seconds), auto-terminates on timeout
- **Separate Outputs**: stdout, stderr, and exit code are returned as distinct outputs for flexible workflow routing

## Installation

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/yourusername/comfyui-command.git
pip install -r requirements.txt
```

## Parameters

### Required

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | STRING (multiline) | `echo input: {input0} seed: {seed}` | Shell command to execute, supports placeholders |
| `seed` | INT | `0` | Random seed, accessible via `{seed}` or `$SEED` |
| `working_dir` | STRING | current working directory | Working directory for command execution |
| `executable` | STRING | `/bin/bash` | Shell interpreter to use |
| `timeout` | INT | `30` | Timeout in seconds (1-3600) |

### Optional (Dynamic Input Slots)

| Parameter | Type | Description |
|-----------|------|-------------|
| `input_0` | `*` (any type) | First input slot. Connecting triggers `input_1`, and so on, up to 20 slots |

## Outputs

| Output | Type | Description |
|--------|------|-------------|
| `stdout` | STRING | Standard output from the command |
| `stderr` | STRING | Standard error from the command |
| `exit_code` | INT | Exit code (0 = success, non-zero = error) |

## Placeholder Syntax

Use the following placeholders in the `command` field:

| Placeholder | Description |
|-------------|-------------|
| `{input0}`, `{input1}`, ... | Replaced with the corresponding input slot value (paths are auto-quoted for spaces) |
| `{input_count}` | Replaced with the number of connected input slots |
| `{input_all}` | All input paths (auto-quoted, suitable for bash array expansion) |
| `{seed}` | Replaced with the seed value |

Examples:

```bash
# Merge two videos with ffmpeg
ffmpeg -i {input0} -i {input1} -filter_complex "[0:v][1:v]hstack" output.mp4

# Resize image with ImageMagick
convert {input0} -resize 512x512 -quality 90 output.jpg

# Call a Python script
python3 /path/to/script.py --input {input0} --seed {seed}

# Batch process all inputs via bash array
arr=({input_all})
for f in "${arr[@]}"; do
    echo "Processing: $f"
    convert "$f" -resize 256x256 "${f%.*}_thumb.png"
done
```

## Environment Variables

The following environment variables are available during command execution:

| Variable | Description |
|----------|-------------|
| `$INPUT_0`, `$INPUT_1`, ... | File path of the corresponding input slot |
| `$SEED` | Seed value |
| `$INPUT_COUNT` | Number of connected input slots |
| `$INPUT_ALL` | All input paths (auto-quoted, suitable for bash array expansion) |

Example:

```bash
# Batch process all inputs via bash array (handles spaces, quotes, newlines correctly)
eval "arr=($INPUT_ALL)"
for f in "${arr[@]}"; do
    echo "Processing: $f"
    convert "$f" -resize 256x256 "${f%.*}_thumb.png"
done
```

## Supported Input Types

The node accepts the following ComfyUI data types and converts them for use in commands:

| Type | Handling |
|------|----------|
| `str`, `int`, `float`, `bool` | Converted to string representation |
| `torch.Tensor` (image) | Saved as temporary PNG file |
| Audio dict (`waveform` + `sample_rate`) | Saved as temporary WAV file |
| `VideoInput` (comfy_api) | Source file extracted or saved as temporary MP4 |
| Other media objects (with `path`/`filename` attribute) | Path auto-extracted |

## Dependencies

The following Python packages are required (most are already included in ComfyUI):

- `numpy` — array processing
- `Pillow` — image processing
- `scipy` — audio file writing

`torch` and `folder_paths` are provided by ComfyUI. `comfy_api` is optional and only needed for `VideoInput` support.

## License

MIT License