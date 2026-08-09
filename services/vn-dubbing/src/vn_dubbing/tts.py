from __future__ import annotations

import gc
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Profile, Settings
from .models import ConfigurationError, PermanentFailure


LOGGER = logging.getLogger(__name__)


class TtsBackend(ABC):
    sample_rate: int

    @abstractmethod
    def synthesize(self, text: str, output_path: Path, seed: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class VieneuV2Backend(TtsBackend):
    sample_rate = 24_000

    def __init__(self, settings: Settings, profile: Profile):
        model = profile.model
        try:
            from huggingface_hub import snapshot_download
            from vieneu import Vieneu
            from vieneu.utils import NeuCodecOnnx
        except ImportError as exc:
            raise ConfigurationError(
                "VieNeu runtime is not installed; use the vn-dub-worker image"
            ) from exc

        cache = settings.model_cache / "huggingface"
        cache.mkdir(parents=True, exist_ok=True)
        LOGGER.info("resolving pinned VieNeu model revision %s", model["revision"])
        backbone_path = snapshot_download(
            repo_id=model["repository"],
            revision=model["revision"],
            cache_dir=cache,
            allow_patterns=[
                "*.json",
                "*.safetensors",
                "merges.txt",
                "vocab.json",
            ],
        )
        codec_path = snapshot_download(
            repo_id=model["codec_repository"],
            revision=model["codec_revision"],
            cache_dir=cache,
            allow_patterns=["*.onnx", "*.yaml", "*.json"],
        )
        # Explicit standard mode is essential: the current SDK defaults to v3.
        codec_file = Path(codec_path) / "model.onnx"
        if not codec_file.is_file():
            raise PermanentFailure(f"pinned ONNX codec is missing {codec_file}")

        # VieNeu 3.2.4 accepts a local model snapshot but its ONNX codec helper
        # accepts only a Hub repository ID and has no revision argument. Route
        # that helper to the already downloaded, pinned ONNX file during
        # construction; restore the SDK class immediately afterwards.
        original_codec_loader = NeuCodecOnnx.__dict__["from_pretrained"]

        def pinned_codec_loader(cls, repo_id, filename="model.onnx", hf_token=None):
            del repo_id, filename, hf_token
            return cls(str(codec_file))

        NeuCodecOnnx.from_pretrained = classmethod(pinned_codec_loader)
        try:
            self._tts = Vieneu(
                mode="standard",
                backbone_repo=backbone_path,
                backbone_device=model.get("backend", "cuda"),
                codec_repo=model["codec_repository"],
                codec_device="cpu",
                gguf_filename=None,
                emotion=model.get("emotion", "natural"),
            )
        finally:
            NeuCodecOnnx.from_pretrained = original_codec_loader
        available = {voice_id for _, voice_id in self._tts.list_preset_voices()}
        voice_id = model["voice_id"]
        if voice_id not in available:
            raise PermanentFailure(
                f"pinned VieNeu voice {voice_id!r} is unavailable; found {sorted(available)}"
            )
        self._voice = self._tts.get_preset_voice(voice_id)
        self._temperature = float(model.get("temperature", 0.3))
        self._top_k = int(model.get("top_k", 20))

    def synthesize(self, text: str, output_path: Path, seed: int) -> None:
        # VieNeu v2 does not expose an inference seed. Temperature/top-k, model,
        # voice data and normalized text are still pinned and recorded.
        del seed
        audio = self._tts.infer(
            text=text,
            voice=self._voice,
            temperature=self._temperature,
            top_k=self._top_k,
            apply_watermark=True,
        )
        if getattr(audio, "size", 0) == 0:
            raise RuntimeError("VieNeu returned empty audio")
        temporary = output_path.with_name(output_path.stem + ".partial.wav")
        self._tts.save(audio, temporary)
        os.replace(temporary, output_path)

    def close(self) -> None:
        close = getattr(self._tts, "close", None)
        if callable(close):
            close()
        self._tts = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


class VoxCpm2Backend(TtsBackend):
    sample_rate = 48_000

    def __init__(self, settings: Settings, profile: Profile):
        fallback = profile.data.get("fallback_model") or {}
        if not fallback.get("enabled"):
            raise ConfigurationError("VoxCPM2 cold backup is disabled in the profile")
        reference = Path(str(fallback.get("reference_audio") or ""))
        transcript = str(fallback.get("reference_transcript") or "").strip()
        expected_hash = str(fallback.get("reference_sha256") or "")
        if not reference.is_file() or not transcript or not expected_hash:
            raise ConfigurationError("VoxCPM2 requires a pinned reference, transcript and SHA-256")
        from .discovery import sha256_file

        if sha256_file(reference) != expected_hash:
            raise PermanentFailure("VoxCPM2 reference audio checksum does not match the profile")
        try:
            from huggingface_hub import snapshot_download
            from voxcpm import VoxCPM
        except ImportError as exc:
            raise ConfigurationError(
                "VoxCPM2 runtime is not installed; rebuild with INSTALL_VOXCPM=true"
            ) from exc
        model_path = snapshot_download(
            repo_id=fallback["repository"],
            revision=fallback["revision"],
            cache_dir=settings.model_cache / "huggingface",
        )
        self._model = VoxCPM.from_pretrained(model_path, load_denoiser=False)
        self._reference = reference
        self._transcript = transcript
        self._cfg = float(fallback.get("cfg_value", 3.0))
        self._timesteps = int(fallback.get("inference_timesteps", 10))

    def synthesize(self, text: str, output_path: Path, seed: int) -> None:
        try:
            import soundfile as sf
        except ImportError as exc:
            raise ConfigurationError("soundfile is required by VoxCPM2") from exc
        audio = self._model.generate(
            text=text,
            reference_wav_path=str(self._reference),
            prompt_wav_path=str(self._reference),
            prompt_text=self._transcript,
            cfg_value=self._cfg,
            inference_timesteps=self._timesteps,
            seed=seed,
        )
        temporary = output_path.with_name(output_path.stem + ".partial.wav")
        sf.write(temporary, audio, self.sample_rate, format="WAV", subtype="PCM_16")
        os.replace(temporary, output_path)

    def close(self) -> None:
        self._model = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def create_backend(settings: Settings, profile: Profile, engine: str | None = None) -> TtsBackend:
    selected = engine or profile.model["engine"]
    if selected == "vieneu-v2":
        return VieneuV2Backend(settings, profile)
    if selected == "voxcpm2":
        return VoxCpm2Backend(settings, profile)
    raise ConfigurationError(f"unknown TTS engine {selected!r}")
