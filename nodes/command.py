import os
import re
import sys
import shlex
import subprocess
import hashlib
import traceback
import threading
import codecs
from collections import Counter
from typing import Dict, List, Tuple, Set, Union

from server import PromptServer

from .media_utils import extract_media_path, classify_input_type


class RB_Command:
    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("stdout", "stderr", "exit_code")
    FUNCTION = "execute_command"
    CATEGORY = "utils/command"
    OUTPUT_NODE = False
    DESCRIPTION = "Execute shell commands/scripts with dynamic inputs, and result caching. Supports placeholders ({input0}, {seed}) and environment variables ($INPUT_0, $SEED, etc.)."

    INPUT_MAX = 20

    def __init__(self):
        self._cache: Dict[str, Tuple[str, str, int]] = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "command": ("STRING", {
                    "multiline": True,
                    "default": "echo input: {input0} seed: {seed}",
                    "tooltip": "Placeholders: {input0} {input0_0} {input0_count} {input_count} {input_all} {input_images} {input_videos} {input_audios} {input_image_count} {input_video_count} {input_audio_count} {seed}\n"
                               "Environment variables: $INPUT_0 $INPUT_0_0 $INPUT_1 $SEED $INPUT_COUNT $INPUT_ALL $INPUT_IMAGES $INPUT_VIDEOS $INPUT_AUDIOS $INPUT_IMAGE_COUNT $INPUT_VIDEO_COUNT $INPUT_AUDIO_COUNT"
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
                "input_0": ("*", {"tooltip": "Dynamic input slot, connect any type (image, audio, video, text, etc.). Connecting triggers more slots up to 20"}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    def _compute_summary(self, placeholder_map: Dict[int, str], type_map: Dict[int, str],
                         bundle_map: Dict[int, List[str]] = None,
                         bundle_type_map: Dict[int, List[str]] = None) -> Dict[str, Union[int, str]]:
        keys = sorted(placeholder_map.keys())
        counter = Counter(type_map.values())
        if bundle_type_map:
            for types in bundle_type_map.values():
                counter.update(types)

        all_parts = []
        image_parts = []
        video_parts = []
        audio_parts = []
        for k in keys:
            if bundle_map and k in bundle_map:
                for i, p in enumerate(bundle_map[k]):
                    t = bundle_type_map[k][i] if bundle_type_map and k in bundle_type_map else "other"
                    q = shlex.quote(p)
                    all_parts.append(q)
                    if t == "image":
                        image_parts.append(q)
                    elif t == "video":
                        video_parts.append(q)
                    elif t == "audio":
                        audio_parts.append(q)
            else:
                q = shlex.quote(placeholder_map[k])
                all_parts.append(q)
                t = type_map.get(k, "other")
                if t == "image":
                    image_parts.append(q)
                elif t == "video":
                    video_parts.append(q)
                elif t == "audio":
                    audio_parts.append(q)

        flat_count = sum(
            len(bundle_map[k]) if bundle_map and k in bundle_map else 1
            for k in keys
        )

        return {
            "count": flat_count,
            "all": " ".join(all_parts),
            "images": " ".join(image_parts),
            "videos": " ".join(video_parts),
            "audios": " ".join(audio_parts),
            "image_count": counter.get("image", 0),
            "video_count": counter.get("video", 0),
            "audio_count": counter.get("audio", 0),
        }

    def _resolve_command(self, command: str,
                         placeholder_map: Dict[int, str],
                         type_map: Dict[int, str],
                         seed: int,
                         bundle_map: Dict[int, List[str]] = None,
                         bundle_type_map: Dict[int, List[str]] = None) -> Tuple[str, Set[int]]:
        """Resolve {inputN}, {inputN_M}, {inputN_count}, {input_count}, {input_all}, {input_images}, {input_videos}, {input_audios}, {input_image_count}, {input_video_count}, {input_audio_count}, {seed} placeholders"""
        missing: Set[int] = set()

        if bundle_map:
            def _sub_bundle(match):
                n = int(match.group(1))
                m_idx = int(match.group(2))
                if n in bundle_map:
                    if m_idx < len(bundle_map[n]):
                        return shlex.quote(bundle_map[n][m_idx])
                    raise ValueError(
                        f"Bundle input_{n} has {len(bundle_map[n])} item(s), "
                        f"but {{input{n}_{m_idx}}} references index {m_idx}")
                if n in placeholder_map:
                    raise ValueError(
                        f"input_{n} is not a bundle, cannot use {{input{n}_{m_idx}}} syntax")
                missing.add(n)
                return match.group(0)
            command = re.sub(r"\{input(\d+)_(\d+)\}", _sub_bundle, command)
        else:
            def _sub_bundle_missing(match):
                n = int(match.group(1))
                m_idx = int(match.group(2))
                if n in placeholder_map:
                    raise ValueError(
                        f"input_{n} is not a bundle, cannot use {{input{n}_{m_idx}}} syntax")
                missing.add(n)
                return match.group(0)
            command = re.sub(r"\{input(\d+)_(\d+)\}", _sub_bundle_missing, command)

        def _sub_count(match):
            n = int(match.group(1))
            if bundle_map and n in bundle_map:
                return str(len(bundle_map[n]))
            if n in placeholder_map:
                return "1"
            missing.add(n)
            return match.group(0)
        command = re.sub(r"\{input(\d+)_count\}", _sub_count, command)

        def _sub(match):
            n = int(match.group(1))
            if n in placeholder_map:
                if bundle_map and n in bundle_map:
                    return " ".join(shlex.quote(p) for p in bundle_map[n])
                return shlex.quote(placeholder_map[n])
            missing.add(n)
            return match.group(0)

        command = re.sub(r"\{input(\d+)\}", _sub, command)
        command = command.replace("{seed}", str(seed))

        s = self._compute_summary(placeholder_map, type_map, bundle_map, bundle_type_map)
        command = command.replace("{input_count}", str(s["count"]))
        command = command.replace("{input_all}", s["all"])
        command = command.replace("{input_images}", s["images"])
        command = command.replace("{input_videos}", s["videos"])
        command = command.replace("{input_audios}", s["audios"])
        command = command.replace("{input_image_count}", str(s["image_count"]))
        command = command.replace("{input_video_count}", str(s["video_count"]))
        command = command.replace("{input_audio_count}", str(s["audio_count"]))

        return command, missing

    def _build_env(self, placeholder_map: Dict[int, str], type_map: Dict[int, str], seed: int,
                   bundle_map: Dict[int, List[str]] = None,
                   bundle_type_map: Dict[int, List[str]] = None) -> dict:
        env = os.environ.copy()
        env["SEED"] = str(seed)

        s = self._compute_summary(placeholder_map, type_map, bundle_map, bundle_type_map)
        env["INPUT_COUNT"] = str(s["count"])
        env["INPUT_ALL"] = s["all"]
        env["INPUT_IMAGES"] = s["images"]
        env["INPUT_VIDEOS"] = s["videos"]
        env["INPUT_AUDIOS"] = s["audios"]
        env["INPUT_IMAGE_COUNT"] = str(s["image_count"])
        env["INPUT_VIDEO_COUNT"] = str(s["video_count"])
        env["INPUT_AUDIO_COUNT"] = str(s["audio_count"])

        for n in range(self.INPUT_MAX):
            key = f"INPUT_{n}"
            if n in placeholder_map:
                env[key] = placeholder_map[n]
            else:
                env.pop(key, None)
            if bundle_map and n in bundle_map:
                env[f"INPUT_{n}_COUNT"] = str(len(bundle_map[n]))
                for m_idx, path in enumerate(bundle_map[n]):
                    env[f"INPUT_{n}_{m_idx}"] = path
            else:
                env.pop(f"INPUT_{n}_COUNT", None)
                for m_idx in range(self.INPUT_MAX):
                    env.pop(f"INPUT_{n}_{m_idx}", None)
        return env

    def _compute_fingerprint(self, command: str, seed: int, working_dir: str,
                             executable: str, timeout: int,
                             placeholder_map: Dict[int, str],
                             bundle_map: Dict[int, List[str]] = None) -> str:
        h = hashlib.sha256()
        h.update(command.encode("utf-8"))
        h.update(str(seed).encode("utf-8"))
        h.update(working_dir.encode("utf-8"))
        h.update(executable.encode("utf-8"))
        h.update(str(timeout).encode("utf-8"))
        for n in sorted(placeholder_map.keys()):
            if bundle_map and n in bundle_map:
                for m_idx, path in enumerate(bundle_map[n]):
                    try:
                        st = os.stat(path)
                        h.update(f"{n}_{m_idx}:{path}:{st.st_size}:{st.st_mtime_ns}".encode())
                    except OSError:
                        h.update(f"{n}_{m_idx}:{path}".encode())
            else:
                path = placeholder_map[n]
                try:
                    st = os.stat(path)
                    h.update(f"{n}:{path}:{st.st_size}:{st.st_mtime_ns}".encode())
                except OSError:
                    h.update(f"{n}:{path}".encode())
        return h.hexdigest()

    @staticmethod
    def _resolve_node_id(unique_id):
        """Resolve node ID for sub-workflow support."""
        return str(unique_id) if not isinstance(unique_id, str) else unique_id

    def _pump(self, stream, prefix, unique_id, line_list):
        node_id = self._resolve_node_id(unique_id)
        buf = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            raw = stream.read(4096)
            if not raw:
                break
            chunk = decoder.decode(raw, final=False)

            sys.stdout.write(chunk)
            sys.stdout.flush()

            buf += chunk
            lines = buf.split("\n")
            buf = lines.pop()
            for line in lines:
                line_list.append(line)
                try:
                    PromptServer.instance.send_sync("command_node_output", {
                        "node": node_id,
                        "data": line
                    })
                except Exception:
                    pass
            if buf:
                try:
                    PromptServer.instance.send_sync("command_node_output", {
                        "node": node_id,
                        "data": f"\x01{buf}"
                    })
                except Exception:
                    pass
        final = decoder.decode(b"", final=True)
        if final:
            buf += final
        if buf:
            line_list.append(buf)
            sys.stdout.write("\n")
            sys.stdout.flush()
        stream.close()

    def execute_command(self, command, seed, working_dir, executable, timeout,
                        unique_id=None, prompt=None, extra_pnginfo=None, **kwargs):
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        proc = None
        node_id = self._resolve_node_id(unique_id)
        temp_files: List[str] = []
        try:
            try:
                PromptServer.instance.send_sync("command_node_output", {
                    "node": node_id,
                    "data": "[CLEAR]"})
            except Exception:
                pass

            placeholder_map: Dict[int, str] = {}
            type_map: Dict[int, str] = {}
            bundle_map: Dict[int, List[str]] = {}
            bundle_type_map: Dict[int, List[str]] = {}
            for k, v in kwargs.items():
                m = re.fullmatch(r"input_(\d+)", k)
                if m and v is not None:
                    idx = int(m.group(1))

                    if isinstance(v, list):
                        bundle_paths = []
                        bundle_types = []
                        for item in v:
                            item_type = classify_input_type(item)
                            bundle_types.append(item_type)
                            path = extract_media_path(item, temp_files=temp_files)
                            if path is not None:
                                bundle_paths.append(path)
                            elif isinstance(item, (str, int, float, bool)):
                                bundle_paths.append(str(item))
                            else:
                                raise ValueError(
                                    f"Bundled input input_{idx} contains unresolvable item"
                                    f" (received type: {type(item).__name__})")
                        bundle_map[idx] = bundle_paths
                        bundle_type_map[idx] = bundle_types
                        placeholder_map[idx] = " ".join(bundle_paths)
                        type_map[idx] = "bundle"
                    else:
                        type_map[idx] = classify_input_type(v)
                        path = extract_media_path(v, temp_files=temp_files)
                        if path is not None:
                            placeholder_map[idx] = path
                        elif isinstance(v, (str, int, float, bool)):
                            placeholder_map[idx] = str(v)
                        else:
                            raise ValueError(
                                f"Input input_{m.group(1)} could not be resolved to a file path"
                                f" (received type: {type(v).__name__})")

            resolved_command, missing = self._resolve_command(
                command, placeholder_map, type_map, seed, bundle_map, bundle_type_map)
            if missing:
                nums = ", ".join(str(n) for n in sorted(missing))
                raise ValueError(f"Command references unconnected input(s): input {nums}")

            fingerprint = self._compute_fingerprint(
                command, seed, working_dir, executable, timeout, placeholder_map, bundle_map)
            cached = self._cache.get(fingerprint)
            if cached is not None:
                print(f"[RB_Command] Cache hit, skipping execution", flush=True)
                for stream_name, lines in [("stdout", cached[0].split("\n")), ("stderr", cached[1].split("\n"))]:
                    prefix = "[RB_Command] " if stream_name == "stdout" else "[RB_Command Error] "
                    for line in lines:
                        print(f"{prefix}{line}", flush=True)
                        try:
                            PromptServer.instance.send_sync("command_node_output", {
                                "node": node_id,
                                "data": line})
                        except Exception:
                            pass
                return cached

            if not os.path.isdir(working_dir):
                raise ValueError(f"Working directory does not exist: {working_dir}")

            env = self._build_env(placeholder_map, type_map, seed, bundle_map, bundle_type_map)

            print(f"[RB_Command] {resolved_command}", flush=True)
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

            t_out = threading.Thread(
                target=self._pump,
                args=(proc.stdout, "[RB_Command] ", unique_id, stdout_lines), daemon=True)
            t_err = threading.Thread(
                target=self._pump,
                args=(proc.stderr, "[RB_Command Error] ", unique_id, stderr_lines), daemon=True)
            t_out.start()
            t_err.start()

            proc.wait(timeout=timeout)
            t_out.join(timeout=5)
            t_err.join(timeout=5)

            exit_code = proc.returncode

            result = ("\n".join(stdout_lines).strip(), "\n".join(stderr_lines).strip(), exit_code)
            self._cache.clear()
            self._cache[fingerprint] = result
            return result

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            msg = "Command execution timed out and was terminated"
            stderr_lines.append(msg)
            print(f"[RB_Command] {msg}", flush=True)
            try:
                PromptServer.instance.send_sync("command_node_output", {
                    "node": node_id,
                    "data": msg})
            except Exception:
                pass
            raise TimeoutError(f"Command timed out ({timeout}s)")

        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[RB_Command Error] {msg}", flush=True)
            traceback.print_exc()
            try:
                PromptServer.instance.send_sync("command_node_output", {
                    "node": node_id,
                    "data": msg})
            except Exception:
                pass
            raise
        finally:
            for f in temp_files:
                try:
                    os.remove(f)
                except OSError:
                    pass