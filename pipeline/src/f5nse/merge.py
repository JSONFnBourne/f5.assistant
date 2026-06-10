from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import torch
import tyro
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

LOGGER = logging.getLogger("f5nse.merge")


@dataclass
class MergeArgs:
    base_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    lora_path: Path = Path("data/models/f5nse-lora")
    output_dir: Path = Path("data/models/f5nse-merged")
    dtype: str = "bfloat16"
    trust_remote_code: bool = False


def dtype_from_string(name: str):
    mapping = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
    }
    return mapping.get(name.lower(), torch.float32)


def run_merge(args: MergeArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    torch_dtype = dtype_from_string(args.dtype)
    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch_dtype,
        device_map="cpu",
        trust_remote_code=args.trust_remote_code,
    )
    model = PeftModel.from_pretrained(base_model, args.lora_path)
    merged = model.merge_and_unload()
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    LOGGER.info("Merged model saved to %s", args.output_dir)


def main() -> None:
    args = tyro.cli(MergeArgs)
    run_merge(args)


if __name__ == "__main__":
    main()
