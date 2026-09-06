import os
import io
import tempfile
from typing import List, Optional

import numpy as np
from PIL import Image
import torch
from scipy.io import wavfile
import folder_paths

try:
    from comfy_api.latest import VideoInput
except ImportError:
    VideoInput = None


def is_audio_dict(value) -> bool:
    return isinstance(value, dict) and "waveform" in value and "sample_rate" in value


def extract_media_path(value, temp_files=None) -> Optional[str]:
    """Extract file path from ComfyUI data types, returns None on failure"""
    if isinstance(value, str):
        if os.path.exists(value):
            return value
        full = os.path.join(folder_paths.get_input_directory(), value)
        if os.path.exists(full):
            return full
        return None

    if is_audio_dict(value):
        return audio_to_temp_file(value["waveform"], value["sample_rate"], temp_files=temp_files)

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
        return tensor_to_temp_file(value, temp_files=temp_files)

    return extract_path_from_object(value, temp_files=temp_files)


def detect_extension_from_bytes(data: bytes) -> str:
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

    return ".bin"


def video_save_to_temp(value, temp_files=None) -> Optional[str]:
    fd, path = tempfile.mkstemp(suffix=".mp4", prefix="command_input_")
    os.close(fd)
    if temp_files is not None:
        temp_files.append(path)
    try:
        value.save_to(path)
        return path
    except Exception:
        return None


def extract_path_from_object(value, temp_files=None) -> Optional[str]:
    """Extract file path from arbitrary object, prioritizing VideoFromFile and other media types"""
    if VideoInput is not None and isinstance(value, VideoInput):
        stream_source = getattr(value, "get_stream_source", None)
        if callable(stream_source):
            try:
                src = stream_source()
            except Exception:
                src = None
            if isinstance(src, str) and os.path.exists(src):
                return src
        return video_save_to_temp(value, temp_files=temp_files)

    stream_source = getattr(value, "get_stream_source", None)
    if callable(stream_source):
        try:
            src = stream_source()
        except Exception:
            src = None
        if isinstance(src, str) and os.path.exists(src):
            return src
        if isinstance(src, io.BytesIO):
            return bytesio_to_temp_file(src, temp_files=temp_files)

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


def bytesio_to_temp_file(data: io.BytesIO, temp_files=None) -> str:
    raw = data.getvalue()
    ext = detect_extension_from_bytes(raw)
    fd, path = tempfile.mkstemp(suffix=ext, prefix="command_input_")
    os.close(fd)
    if temp_files is not None:
        temp_files.append(path)
    with open(path, "wb") as f:
        f.write(raw)
    return path


def tensor_to_temp_file(tensor: torch.Tensor, temp_files=None) -> str:
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
    if temp_files is not None:
        temp_files.append(path)
    img.save(path, "PNG")
    return path


def audio_to_temp_file(waveform: torch.Tensor, sample_rate: int, temp_files=None) -> str:
    waveform = waveform.cpu().detach()
    if waveform.ndim == 3 and waveform.shape[0] == 1:
        waveform = waveform.squeeze(0)
    if waveform.ndim != 2:
        raise ValueError(f"Unsupported audio waveform dimensions: {waveform.ndim} (expected (channels, samples))")

    arr = waveform.contiguous().numpy().astype(np.float32)
    arr = arr.T

    fd, path = tempfile.mkstemp(suffix=".wav", prefix="command_input_")
    os.close(fd)
    if temp_files is not None:
        temp_files.append(path)
    wavfile.write(path, int(sample_rate), arr)
    return path


def classify_input_type(v) -> str:
    if is_audio_dict(v):
        return "audio"
    if isinstance(v, torch.Tensor):
        return "image"
    if VideoInput is not None and isinstance(v, VideoInput):
        return "video"
    if isinstance(v, dict):
        for key in ("path", "filename", "fullpath"):
            if key in v and isinstance(v[key], str):
                ext = os.path.splitext(v[key])[1].lower()
                if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff'):
                    return "image"
                if ext in ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv'):
                    return "video"
                if ext in ('.wav', '.mp3', '.ogg', '.flac', '.aac', '.m4a'):
                    return "audio"
                break
    return "other"