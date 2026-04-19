from __future__ import annotations

import logging
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:64")

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional

from datasets import load_dataset
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from transformers import TrainingArguments
from peft import LoraConfig, get_peft_model
from trl import SFTTrainer
import tyro

LOGGER = logging.getLogger("irule.train")


@dataclass
class TrainArgs:
    train_path: Path = Path("data/datasets/train.jsonl")
    eval_path: Path = Path("data/datasets/eval.jsonl")
    base_model: str = "meta-llama/Llama-3.2-3B-Instruct"
    output_dir: Path = Path("data/models/irule-lora")
    quantization: Literal["4bit", "8bit", "none"] = "4bit"
    max_gpu_memory: Optional[str] = None
    max_cpu_memory: Optional[str] = "48GiB"
    backend: Literal["auto", "transformers", "unsloth"] = "auto"
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
    num_train_epochs: float = 4.0
    warmup_ratio: float = 0.05
    per_device_train_batch_size: int = 1
    per_device_eval_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    max_seq_length: int = 512
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
    max_steps: int = -1


def prepare_model_and_tokenizer(args: TrainArgs):
    quant_mode = args.quantization
    backend = args.backend
    use_unsloth = quant_mode == "4bit" and backend == "unsloth"
    if use_unsloth:
        try:
            from unsloth import FastLanguageModel  # type: ignore

            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=args.base_model,
                max_seq_length=args.max_seq_length,
                load_in_4bit=True,
                device_map="auto",
            )
            model = FastLanguageModel.get_peft_model(
                model,
                r=args.lora_r,
                lora_alpha=args.lora_alpha,
                lora_dropout=args.lora_dropout,
                target_modules=args.lora_target_modules,
                bias="none",
                task_type="CAUSAL_LM",
            )
            if args.gradient_checkpointing and hasattr(model, "gradient_checkpointing_enable"):
                try:
                    model.gradient_checkpointing_enable(use_reentrant=False)
                except TypeError:
                    model.gradient_checkpointing_enable()
            if hasattr(model, "config"):
                model.config.use_cache = False
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
                tokenizer.pad_token_id = tokenizer.eos_token_id
            tokenizer.padding_side = "right"
            return model, tokenizer, True
        except ImportError as exc:
            raise RuntimeError(
                "Unsloth backend requested but library is not installed. Install unsloth or switch backend."
            ) from exc
        except NotImplementedError as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            raise RuntimeError(f"Unsloth backend failed: {exc}") from exc
    model_kwargs = {"trust_remote_code": args.trust_remote_code}
    if quant_mode in {"4bit", "8bit"}:
        if not torch.cuda.is_available():
            raise RuntimeError(f"{quant_mode} quantization requires a CUDA-capable GPU.")
        if quant_mode == "4bit":
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
        else:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        # Let Accelerate manage placement; we'll move to cuda explicitly after load.
        model_kwargs["device_map"] = None
        if args.max_gpu_memory or args.max_cpu_memory:
            max_memory = {}
            if args.max_gpu_memory:
                max_memory[torch.cuda.current_device()] = args.max_gpu_memory
            if args.max_cpu_memory:
                max_memory["cpu"] = args.max_cpu_memory
            model_kwargs["max_memory"] = max_memory
    else:
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        model_kwargs["torch_dtype"] = dtype
        model_kwargs["device_map"] = None
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "right"

    if torch.cuda.is_available():
        try:
            model.to("cuda")
            LOGGER.info("Training model moved to cuda")
        except Exception as exc:
            LOGGER.warning("Failed to move training model to cuda: %s", exc)
    return model, tokenizer, False


def formatting_func_factory(tokenizer):
    system_prompt = (
        "You are irule, an expert assistant dedicated to F5 BIG-IP, VELOS, and rSeries products. "
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
    # Pre-train summary to avoid accidental 1-step runs
    try:
        train_n = len(dataset["train"])  # type: ignore[index]
    except Exception:
        train_n = 0
    try:
        eval_n = len(dataset["eval"])  # type: ignore[index]
    except Exception:
        eval_n = 0
    eff_batch = max(1, args.per_device_train_batch_size)
    ga = max(1, args.gradient_accumulation_steps)
    num_batches = (train_n + eff_batch - 1) // eff_batch
    upd_per_epoch = max(1, num_batches // ga)
    total_upd = int(upd_per_epoch * max(1, int(args.num_train_epochs)))
    LOGGER.info(
        "Pre-train: train=%s eval=%s batch=%s grad_accum=%s -> updates/epoch=%s total_updates≈%s",
        train_n,
        eval_n,
        eff_batch,
        ga,
        upd_per_epoch,
        total_upd,
    )
    model, tokenizer, has_peft = prepare_model_and_tokenizer(args)

    if not has_peft:
        peft_config = LoraConfig(
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            target_modules=args.lora_target_modules,
            lora_dropout=args.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, peft_config)
    if args.gradient_checkpointing and hasattr(model, "gradient_checkpointing_enable"):
        try:
            model.gradient_checkpointing_enable(use_reentrant=False)
        except TypeError:
            model.gradient_checkpointing_enable()
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    if hasattr(model, "config"):
        model.config.use_cache = False

    use_fp16 = torch.cuda.is_available()
    training_kwargs = {
        "output_dir": str(args.output_dir),
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "num_train_epochs": args.num_train_epochs,
        "max_steps": args.max_steps,
        "warmup_ratio": args.warmup_ratio,
        "per_device_train_batch_size": args.per_device_train_batch_size,
        "per_device_eval_batch_size": args.per_device_eval_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "logging_steps": args.logging_steps,
        "eval_steps": args.eval_steps,
        "save_steps": args.save_steps,
        "save_total_limit": args.save_total_limit,
        "fp16": use_fp16,
        "bf16": False,
        "gradient_checkpointing": args.gradient_checkpointing,
        "report_to": ["none"],
        "seed": args.seed,
        "optim": args.optim,
        # Let TRL/SFTTrainer control column removal for non-packed datasets
        "remove_unused_columns": True,
        "group_by_length": False,
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

    # Build explicit 'text' fields to avoid TRL formatting/packing pitfalls
    system_prompt = (
        "You are irule, an expert assistant dedicated to F5 BIG-IP, VELOS, and rSeries products. "
        "Respond using precise, US English, citing relevant modules when helpful."
    )

    def to_text(example):
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
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    train_ds = dataset["train"].map(
        to_text,
        remove_columns=list(dataset["train"].features),
        desc="Formatting train",
    )
    eval_ds = dataset["eval"].map(
        to_text,
        remove_columns=list(dataset["eval"].features),
        desc="Formatting eval",
    )

    def build_trainer(packing: bool) -> SFTTrainer:
        return SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=train_ds,
            eval_dataset=eval_ds,
            dataset_text_field="text",
            max_seq_length=args.max_seq_length,
            packing=packing,
            args=training_args,
        )

    trainer = None
    if args.packing:
        try:
            trainer = build_trainer(packing=True)
        except Exception as exc:
            LOGGER.warning(
                "Packing failed (%s). Falling back to non-packed batches.", exc
            )
            trainer = build_trainer(packing=False)
    else:
        trainer = build_trainer(packing=False)

    # Inspect effective train loader size to diagnose "instant" training
    try:
        eff_ds_len = len(trainer.train_dataset) if hasattr(trainer, "train_dataset") else -1  # type: ignore[arg-type]
    except TypeError:
        eff_ds_len = -1
    try:
        train_dl = trainer.get_train_dataloader()
        eff_dl_len = len(train_dl) if hasattr(train_dl, "__len__") else -1
    except Exception:
        eff_dl_len = -1
    LOGGER.info(
        "Effective training set size: dataset_len=%s dataloader_len=%s",
        eff_ds_len,
        eff_dl_len,
    )

    trainer.train()
    trainer.model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    LOGGER.info("Training completed. Artifacts stored in %s", args.output_dir)


def main() -> None:
    args = tyro.cli(TrainArgs)
    run_train(args)


if __name__ == "__main__":
    main()
