import os
import io
import re
import sys
import shlex
import subprocess
import hashlib
import traceback
import threading
import codecs
import tempfile
from typing import Dict, List, Optional, Tuple, Set

import numpy as np
from PIL import Image
import torch
from scipy.io import wavfile
import folder_paths
from server import PromptServer

try:
    from comfy_api.latest import VideoInput
except ImportError:
    VideoInput = None


def _is_audio_dict(value) -> bool:
    return isinstance(value, dict) and "waveform" in value and "sample_rate" in value


def _extract_media_path(value) -> Optional[str]:
    """Extract file path from ComfyUI data types, returns None on failure"""
    if isinstance(value, str):
        if os.path.exists(value):
            return value
        full = os.path.join(folder_paths.get_input_directory(), value)
        if os.path.exists(full):
            return full
        return None

    if _is_audio_dict(value):
        return _audio_to_temp_file(value["waveform"], value["sample_rate"])

    if isinstance(value, dict):
        for key in ("path", "filename", "fullpath"):
            if key in value and isinstance(value[key], str):
                candidate = value[key]
                if os.path.isabs(candidate):
                    return candidate if os.path.exists(candidate) else None
                full = os.path.join(folder_paths.get_input_directory(), candidate)
                if os.path.exists(full):
                    return full

    if isinstance(value, torch.Tensor):
        return _tensor_to_temp_file(value)

    return _extract_path_from_object(value)


def _detect_extension_from_bytes(data: bytes) -> str:
    """Detect file format via magic bytes, returns extension (with dot), falls back to .bin"""
    if len(data) < 4:
        return ".bin"

    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF":
        if len(data) >= 12 and data[8:12] == b"AVI ":
            return ".avi"
        if len(data) >= 12 and data[8:12] == b"WAVE":
            return ".wav"
        return ".bin"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if data[:4] == b"FLV\x01":
        return ".flv"
    if data[:3] == b"ID3" or (data[0] == 0xff and (data[1] & 0xe0) == 0xe0):
        return ".mp3"
    if data[4:8] == b"ftyp":
        return ".mp4"
    if data[:4] == b"\x00\x00\x00\x1c" and data[4:8] == b"ftyp":
        return ".mp4"

    return ".bin"


def _video_save_to_temp(value) -> Optional[str]:
    """Reference implementation from official SaveVideo, calls save_to directly to temp file, avoids BytesIO roundtrip"""
    fd, path = tempfile.mkstemp(suffix=".mp4", prefix="command_input_")
    os.close(fd)
    try:
        value.save_to(path)
        return path
    except Exception:
        return None


def _extract_path_from_object(value) -> Optional[str]:
    """Extract file path from arbitrary object, prioritizing VideoFromFile and other media types"""
    # VideoInput type: prefer get_stream_source for file path, otherwise use save_to directly
    if VideoInput is not None and isinstance(value, VideoInput):
        stream_source = getattr(value, "get_stream_source", None)
        if callable(stream_source):
            try:
                src = stream_source()
            except Exception:
                src = None
            if isinstance(src, str) and os.path.exists(src):
                return src
        return _video_save_to_temp(value)

    # Other types: try get_stream_source
    stream_source = getattr(value, "get_stream_source", None)
    if callable(stream_source):
        try:
            src = stream_source()
        except Exception:
            src = None
        if isinstance(src, str) and os.path.exists(src):
            return src
        if isinstance(src, io.BytesIO):
            return _bytesio_to_temp_file(src)

    for attr in ("path", "file", "filename", "fullpath", "name"):
        candidate = getattr(value, attr, None)
        if callable(candidate):
            try:
                candidate = candidate()
            except Exception:
                continue
        if isinstance(candidate, str) and os.path.exists(candidate):
            return candidate
    return None


def _bytesio_to_temp_file(data: io.BytesIO) -> str:
    """Save BytesIO content to temp file, returns path"""
    raw = data.getvalue()
    ext = _detect_extension_from_bytes(raw)
    fd, path = tempfile.mkstemp(suffix=ext, prefix="command_input_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(raw)
    return path

def _tensor_to_temp_file(tensor: torch.Tensor) -> str:
    """Save torch.Tensor (image) as temp PNG file, returns path"""
    tensor = tensor.cpu().detach()

    if tensor.ndim == 4:
        tensor = tensor[0].contiguous()
    if tensor.ndim != 3:
        raise ValueError(f"Unsupported tensor dimensions: {tensor.ndim} (expected 3D HWC)")

    arr = np.clip(255. * tensor.numpy(), 0, 255).astype(np.uint8)
    if arr.shape[-1] == 3:
        img = Image.fromarray(arr, "RGB")
    elif arr.shape[-1] == 4:
        img = Image.fromarray(arr, "RGBA")
    else:
        img = Image.fromarray(arr, "L")

    fd, path = tempfile.mkstemp(suffix=".png", prefix="command_input_")
    os.close(fd)
    img.save(path, "PNG")
    return path


def _audio_to_temp_file(waveform: torch.Tensor, sample_rate: int) -> str:
    """Save audio dict (waveform Tensor + sample_rate) as temp WAV file, returns path"""
    waveform = waveform.cpu().detach()
    if waveform.ndim == 3 and waveform.shape[0] == 1:
        waveform = waveform.squeeze(0)
    if waveform.ndim != 2:
        raise ValueError(f"Unsupported audio waveform dimensions: {waveform.ndim} (expected (channels, samples))")

    arr = waveform.contiguous().numpy().astype(np.float32)
    arr = arr.T

    fd, path = tempfile.mkstemp(suffix=".wav", prefix="command_input_")
    os.close(fd)
    wavfile.write(path, int(sample_rate), arr)
    return path


class CommandNode:
    """Command execution node (streaming output + dynamic inputs, Impact Pack pattern)"""

    DESCRIPTION = "Execute shell commands/scripts with dynamic inputs, streaming output, and result caching. Supports placeholders ({input0}, {seed}) and environment variables ($INPUT_0, $SEED, etc.)."
    INPUT_MAX = 20
    CACHE_MAXSIZE = 1

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "command": ("STRING", {
                    "multiline": True,
                    "default": "echo input: {input0} seed: {seed}",
                    "tooltip": "Placeholders: {input0} maps to slot Input 0\n"
                               "Environment variables: $INPUT_0 $INPUT_1 $SEED $INPUT_COUNT $INPUT_ALL"
                }),
                "working_dir": ("STRING", {
                    "default": os.getcwd(),
                    "tooltip": "Working directory for command execution"
                }),
                "executable": ("STRING", {
                    "default": "/bin/bash",
                    "tooltip": "Shell interpreter to use (e.g. /bin/bash, /bin/sh, python3)"
                }),
                "timeout": ("INT", {
                    "default": 30,
                    "min": 1,
                    "max": 3600,
                    "tooltip": "Timeout in seconds, process will be killed if exceeded"
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 0xFFFFFFFFFFFFFFFF,
                    "control_after_generate": True,
                    "tooltip": "Random seed, accessible via {seed} or $SEED"
                }),
            },
            "optional": {
                # Following Impact Pack: only define the first slot, the rest are dynamically added by the frontend.
                # **kwargs receives all frontend slots, unconnected ones are None
                "input_0": ("*", {"tooltip": "Dynamic input slot, connect any type (image, audio, video, text, etc.). Connecting triggers more slots up to 20"}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("stdout", "stderr", "exit_code")
    FUNCTION = "execute_command"
    CATEGORY = "utils/command"
    OUTPUT_NODE = False

    # ---------------- Placeholder resolution ----------------

    def _resolve_command(self, command: str,
                         placeholder_map: Dict[int, str],
                         seed: int) -> Tuple[str, Set[int]]:
        """Resolve {inputN} and {seed} placeholders, auto-quote paths with spaces. Returns (command, set of missing slot indices)"""
        missing: Set[int] = set()

        def _sub(match):
            n = int(match.group(1))
            if n in placeholder_map:
                return shlex.quote(placeholder_map[n])
            missing.add(n)
            return match.group(0)

        command = re.sub(r"\{input(\d+)\}", _sub, command)
        command = command.replace("{seed}", str(seed))
        return command, missing

    # ---------------- Environment variables ----------------

    def _build_env(self, placeholder_map: Dict[int, str], seed: int) -> dict:
        env = os.environ.copy()
        env["SEED"] = str(seed)

        keys = sorted(placeholder_map.keys())
        env["INPUT_COUNT"] = str(len(keys))
        env["INPUT_ALL"] = " ".join(shlex.quote(placeholder_map[k]) for k in keys)

        for n in range(self.INPUT_MAX):
            key = f"INPUT_{n}"
            if n in placeholder_map:
                env[key] = placeholder_map[n]
            else:
                env.pop(key, None)
        return env

    # ---------------- Fingerprint / Cache ----------------

    def _compute_fingerprint(self, command: str, seed: int, working_dir: str,
                             executable: str, timeout: int,
                             placeholder_map: Dict[int, str]) -> str:
        h = hashlib.sha256()
        h.update(command.encode("utf-8"))
        h.update(str(seed).encode("utf-8"))
        h.update(working_dir.encode("utf-8"))
        h.update(executable.encode("utf-8"))
        h.update(str(timeout).encode("utf-8"))
        for n in sorted(placeholder_map.keys()):
            path = placeholder_map[n]
            if os.path.isfile(path):
                st = os.stat(path)
                h.update(f"{n}:{path}:{st.st_size}:{st.st_mtime_ns}".encode())
            else:
                h.update(f"{n}:{path}".encode())
        return h.hexdigest()

    # ---------------- Streaming output ----------------

    def _pump(self, stream, prefix, unique_id, line_list):
        buf = ""
        first = True
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            raw = stream.read(4096)
            if not raw:
                break
            chunk = decoder.decode(raw, final=False)

            if first:
                sys.stdout.write(prefix + chunk.replace("\n", "\n" + prefix))
                first = False
            else:
                sys.stdout.write(chunk.replace("\n", "\n" + prefix))
            sys.stdout.flush()

            buf += chunk
            lines = buf.split("\n")
            buf = lines.pop()
            for line in lines:
                line_list.append(line)
                try:
                    PromptServer.instance.send_sync("command_node_output", {
                        "node": unique_id,
                        "data": line
                    })
                except Exception:
                    pass
            if buf:
                try:
                    PromptServer.instance.send_sync("command_node_output", {
                        "node": unique_id,
                        "data": f"\x01{buf}"
                    })
                except Exception:
                    pass
        if buf:
            line_list.append(buf)
            sys.stdout.write("\n")
            sys.stdout.flush()
        stream.close()

    # ---------------- Main execution ----------------

    def execute_command(self, command, seed, working_dir, executable, timeout,
                        unique_id=None, prompt=None, extra_pnginfo=None, **kwargs):
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        proc = None
        try:
            # 1. Clear frontend output
            try:
                PromptServer.instance.send_sync("command_node_output", {
                    "node": unique_id, "data": "[CLEAR]"})
            except Exception:
                pass

            # 2. Collect inputs
            placeholder_map: Dict[int, str] = {}
            for k, v in kwargs.items():
                m = re.fullmatch(r"input_(\d+)", k)
                if m and v is not None:
                    path = _extract_media_path(v)
                    if path is not None:
                        placeholder_map[int(m.group(1))] = path
                    elif isinstance(v, (str, int, float, bool)):
                        placeholder_map[int(m.group(1))] = str(v)
                    else:
                        raise ValueError(
                            f"Input input_{m.group(1)} could not be resolved to a file path"
                            f" (received type: {type(v).__name__})")

            # 3. Placeholder validation
            resolved_command, missing = self._resolve_command(command, placeholder_map, seed)
            if missing:
                nums = ", ".join(str(n) for n in sorted(missing))
                raise ValueError(f"Command references unconnected input(s): input {nums}")

            # 4. Cache
            fingerprint = self._compute_fingerprint(
                command, seed, working_dir, executable, timeout, placeholder_map)
            cached = self._cache.get(fingerprint)
            if cached is not None:
                print(f"[CommandNode] Cache hit, skipping execution", flush=True)
                for stream_name, lines in [("[CommandNode] ", cached[0].split("\n")), ("[CommandNode] ", cached[1].split("\n"))]:
                    for line in lines:
                        entry = f"{stream_name}{line}"
                        try:
                            PromptServer.instance.send_sync("command_node_output", {
                                "node": unique_id, "data": entry})
                        except Exception:
                            pass
                return cached

            # 5. Working directory
            if not os.path.isdir(working_dir):
                raise ValueError(f"Working directory does not exist: {working_dir}")

            # 6. Environment variables
            env = self._build_env(placeholder_map, seed)

            # 7. Start process
            print(f"[CommandNode] {resolved_command}", flush=True)
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(
                resolved_command,
                shell=True,
                executable=executable,
                cwd=working_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=creationflags,
            )

            # 8. Dual-thread streaming read
            t_out = threading.Thread(
                target=self._pump,
                args=(proc.stdout, "[CommandNode] ", unique_id, stdout_lines), daemon=True)
            t_err = threading.Thread(
                target=self._pump,
                args=(proc.stderr, "[CommandNode] ", unique_id, stderr_lines), daemon=True)
            t_out.start()
            t_err.start()

            # 9. Wait for completion (with timeout)
            proc.wait(timeout=timeout)
            t_out.join(timeout=5)
            t_err.join(timeout=5)

            exit_code = proc.returncode

            # 10. Cache and return (keep only last entry)
            result = ("\n".join(stdout_lines).strip(), "\n".join(stderr_lines).strip(), exit_code)
            self._cache.clear()
            self._cache[fingerprint] = result
            return result

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            msg = "Command execution timed out and was terminated"
            stderr_lines.append(msg)
            print(f"[CommandNode] {msg}", flush=True)
            try:
                PromptServer.instance.send_sync("command_node_output", {
                    "node": unique_id, "data": msg})
            except Exception:
                pass
            raise TimeoutError(f"Command timed out ({timeout}s)")

        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[CommandNode Error] {msg}", flush=True)
            traceback.print_exc()
            try:
                PromptServer.instance.send_sync("command_node_output", {
                    "node": unique_id, "data": msg})
            except Exception:
                pass
            raise

    _cache: Dict[str, tuple] = {}