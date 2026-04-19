from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from datasets import load_dataset
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from transformers import TrainingArguments
from peft import LoraConfig
from trl import SFTTrainer
import tyro

LOGGER = logging.getLogger("f5nse.train")


@dataclass
class TrainArgs:
    train_path: Path = Path("data/datasets/train.jsonl")
    eval_path: Path = Path("data/datasets/eval.jsonl")
    base_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    output_dir: Path = Path("data/models/f5nse-lora")
    load_in_4bit: bool = True
    load_in_8bit: bool = False
    lora_r: int = 64
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    lora_target_modules: List[str] = field(
        default_factory=lambda: [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ]
    )
    learning_rate: float = 2e-4
    weight_decay: float = 0.0
    num_train_epochs: float = 3.0
    warmup_ratio: float = 0.05
    per_device_train_batch_size: int = 1
    per_device_eval_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    max_seq_length: int = 1024
    logging_steps: int = 10
    evaluation_strategy: str = "steps"
    eval_steps: int = 50
    save_steps: int = 100
    save_total_limit: int = 2
    gradient_checkpointing: bool = True
    seed: int = 42
    trust_remote_code: bool = False
    packing: bool = False
    optim: str = "paged_adamw_8bit"


def prepare_model_and_tokenizer(args: TrainArgs):
    quant_config = None
    model_kwargs = {"trust_remote_code": args.trust_remote_code}
    if args.load_in_4bit:
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
        model_kwargs["quantization_config"] = quant_config
        model_kwargs["device_map"] = "auto"
    elif args.load_in_8bit:
        quant_config = BitsAndBytesConfig(load_in_8bit=True)
        model_kwargs["quantization_config"] = quant_config
        model_kwargs["device_map"] = "auto"
    else:
        model_kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        model_kwargs["device_map"] = "auto" if torch.cuda.is_available() else None
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "right"
    return model, tokenizer


def formatting_func_factory(tokenizer):
    system_prompt = (
        "You are f5nse, an expert assistant dedicated to F5 BIG-IP, VELOS, and rSeries products. "
        "Respond using precise, US English, citing relevant modules when helpful."
    )

    def _formatting_func(example):
        question = example.get("question") or example.get("prompt")
        if question is None:
            raise KeyError("Record missing 'question'/'prompt' field required for formatting.")
        answer = example.get("answer") or example.get("completion")
        if answer is None:
            raise KeyError("Record missing 'answer'/'completion' field required for formatting.")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
        formatted = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )
        return [formatted]

    return _formatting_func


def run_train(args: TrainArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    dataset = load_dataset(
        "json",
        data_files={"train": str(args.train_path), "eval": str(args.eval_path)},
    )
    model, tokenizer = prepare_model_and_tokenizer(args)

    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=args.lora_target_modules,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
    )

    training_kwargs = {
        "output_dir": str(args.output_dir),
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "num_train_epochs": args.num_train_epochs,
        "warmup_ratio": args.warmup_ratio,
        "per_device_train_batch_size": args.per_device_train_batch_size,
        "per_device_eval_batch_size": args.per_device_eval_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "logging_steps": args.logging_steps,
        "eval_steps": args.eval_steps,
        "save_steps": args.save_steps,
        "save_total_limit": args.save_total_limit,
        "fp16": False,
        "bf16": not args.load_in_4bit and not args.load_in_8bit and torch.cuda.is_available(),
        "gradient_checkpointing": args.gradient_checkpointing,
        "report_to": ["none"],
        "seed": args.seed,
        "optim": args.optim,
    }
    try:
        training_args = TrainingArguments(
            evaluation_strategy=args.evaluation_strategy,
            **training_kwargs,
        )
    except TypeError:
        LOGGER.warning(
            "TrainingArguments does not accept 'evaluation_strategy'; using defaults and setting attribute manually."
        )
        training_args = TrainingArguments(**training_kwargs)
        if hasattr(training_args, "evaluation_strategy"):
            setattr(training_args, "evaluation_strategy", args.evaluation_strategy)
        elif hasattr(training_args, "eval_strategy"):
            setattr(training_args, "eval_strategy", args.evaluation_strategy)
    if args.gradient_checkpointing and hasattr(training_args, "gradient_checkpointing_kwargs"):
        training_args.gradient_checkpointing_kwargs = {"use_reentrant": False}

    formatting_func = formatting_func_factory(tokenizer)

    def build_trainer(packing: bool) -> SFTTrainer:
        return SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset["train"],
            eval_dataset=dataset["eval"],
            formatting_func=formatting_func,
            max_seq_length=args.max_seq_length,
            peft_config=peft_config,
            packing=packing,
            args=training_args,
        )

    trainer = None
    if args.packing:
        try:
            trainer = build_trainer(packing=True)
        except ValueError as exc:
            LOGGER.warning("Packing failed (%s). Falling back to non-packed batches.", exc)
            trainer = build_trainer(packing=False)
    else:
        trainer = build_trainer(packing=False)

    trainer.train()
    trainer.model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    LOGGER.info("Training completed. Artifacts stored in %s", args.output_dir)


def main() -> None:
    args = tyro.cli(TrainArgs)
    run_train(args)


if __name__ == "__main__":
    main()
