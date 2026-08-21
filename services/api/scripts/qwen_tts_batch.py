#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


def model_runtime() -> tuple[str, torch.dtype, str | None]:
    if torch.cuda.is_available():
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        try:
            import flash_attn  # noqa: F401
        except ImportError:
            attention = None
        else:
            attention = "flash_attention_2"
        return "cuda:0", dtype, attention
    if torch.backends.mps.is_available():
        return "mps", torch.float32, None
    return "cpu", torch.float32, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch local Qwen3-TTS renderer")
    parser.add_argument("request")
    parser.add_argument("output_dir")
    parser.add_argument("design_checkpoint")
    parser.add_argument("base_checkpoint")
    parser.add_argument("voice_reference")
    parser.add_argument("voice_design")
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    texts = request.get("texts") or []
    if not texts or not all(isinstance(item, str) and item.strip() for item in texts):
        raise ValueError("request.texts must contain non-empty strings")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device, dtype, attention = model_runtime()
    reference_text = "真正重要的不是消息本身，而是它改变了什么，以及我们应该怎样理解它。"
    reference_path = Path(args.voice_reference)
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    if not reference_path.is_file():
        design_model = Qwen3TTSModel.from_pretrained(
            args.design_checkpoint,
            device_map=device,
            dtype=dtype,
            attn_implementation=attention,
            local_files_only=True,
        )
        torch.manual_seed(20260820)
        reference_wavs, reference_rate = design_model.generate_voice_design(
            text=reference_text,
            language="Chinese",
            instruct=args.voice_design,
            do_sample=True,
            temperature=0.45,
            top_k=30,
            top_p=0.8,
            repetition_penalty=1.08,
            subtalker_dosample=True,
            subtalker_temperature=0.45,
            subtalker_top_k=30,
            subtalker_top_p=0.8,
            max_new_tokens=768,
        )
        sf.write(reference_path, np.asarray(reference_wavs[0], dtype=np.float32), reference_rate, subtype="PCM_16")
        del design_model
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    model = Qwen3TTSModel.from_pretrained(
        args.base_checkpoint,
        device_map=device,
        dtype=dtype,
        attn_implementation=attention,
        local_files_only=True,
    )
    voice_prompt = model.create_voice_clone_prompt(
        ref_audio=str(reference_path),
        ref_text=reference_text,
        x_vector_only_mode=False,
    )

    results = []
    for index, text in enumerate(texts):
        torch.manual_seed(20260820)
        started = time.time()
        wavs, sample_rate = model.generate_voice_clone(
            text=text,
            language="Chinese",
            voice_clone_prompt=voice_prompt,
            do_sample=True,
            temperature=0.45,
            top_k=30,
            top_p=0.8,
            repetition_penalty=1.08,
            subtalker_dosample=True,
            subtalker_temperature=0.45,
            subtalker_top_k=30,
            subtalker_top_p=0.8,
            max_new_tokens=1536,
        )
        audio = np.asarray(wavs[0], dtype=np.float32)
        path = output_dir / f"scene-{index + 1:02d}.wav"
        sf.write(path, audio, sample_rate, subtype="PCM_16")
        results.append(
            {
                "path": str(path),
                "duration_seconds": len(audio) / sample_rate,
                "generation_seconds": time.time() - started,
                "sample_rate": sample_rate,
            }
        )
    (output_dir / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
