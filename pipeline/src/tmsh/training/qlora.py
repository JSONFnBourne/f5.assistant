"""QLoRA fine-tuning utilities."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    Trainer,
    TrainingArguments,
)

BASE_MODEL = "meta-llama/Llama-3.2-3B-Instruct"


@dataclass
class TrainingExample:
    """Conversation formatted training example."""

    prompt: str
    response: str

    def to_dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "response": self.response,
        }


@dataclass
class QLoRAConfig:
    data_path: Path
    output_dir: Path
    base_model: str = BASE_MODEL
    batch_size: int = 1
    gradient_accumulation_steps: int = 16
    max_steps: int | None = None
    learning_rate: float = 2e-4
    warmup_steps: int = 50
    logging_steps: int = 10
    save_steps: int = 200


def load_training_examples(path: Path) -> list[TrainingExample]:
    examples: list[TrainingExample] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            question = payload.get("question", "")
            answer = payload.get("answer", "")
            if not question or not answer:
                continue
            domain = payload.get("domain", "tmsh")
            syntax = payload.get("syntax")
            metadata = []
            if domain:
                metadata.append(f"Domain: {domain}")
            if syntax:
                metadata.append(f"Syntax: {syntax}")
            prefix = "\n".join(metadata)
            prompt = f"{prefix}\nQuestion: {question}" if prefix else f"Question: {question}"
            examples.append(TrainingExample(prompt=prompt, response=answer))
    return examples


def build_dataset(examples: Iterable[TrainingExample]) -> Dataset:
    records = [example.to_dict() for example in examples]
    return Dataset.from_list(records)


def prepare_model_and_tokenizer(base_model: str):
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=quant_config,
        device_map="auto",
    )
    model.config.use_cache = False
    return model, tokenizer


def formatting_function(batch):
    return [
        f"<s>[INST] {prompt} [/INST] {response} </s>"
        for prompt, response in zip(batch["prompt"], batch["response"])
    ]


def train_qlora(config: QLoRAConfig) -> Trainer:
    """Launch a QLoRA training run."""

    examples = load_training_examples(config.data_path)
    dataset = build_dataset(examples)
    model, tokenizer = prepare_model_and_tokenizer(config.base_model)

    training_args = TrainingArguments(
        output_dir=str(config.output_dir),
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        max_steps=config.max_steps,
        warmup_steps=config.warmup_steps,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps,
        bf16=True,
        optim="paged_adamw_32bit",
        lr_scheduler_type="cosine",
        report_to=["tensorboard"],
    )

    dataset = dataset.map(
        lambda batch: {"text": formatting_function(batch)},
        batched=True,
        remove_columns=dataset.column_names,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        tokenizer=tokenizer,
    )

    return trainer
