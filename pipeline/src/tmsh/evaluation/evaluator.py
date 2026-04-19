"""Evaluation utilities for the tmsh fine-tuned model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer


@dataclass
class EvaluationConfig:
    model_path: str
    dataset_path: str
    max_new_tokens: int = 512


def load_jsonl_dataset(path: str):
    return load_dataset("json", data_files=path, split="train")


def evaluate_model(config: EvaluationConfig) -> Dict[str, float]:
    tokenizer = AutoTokenizer.from_pretrained(config.model_path)
    model = AutoModelForCausalLM.from_pretrained(config.model_path, device_map="auto")

    dataset = load_jsonl_dataset(config.dataset_path)
    total = 0
    exact_matches = 0
    for example in dataset:
        question = example.get("question", "")
        answer = example.get("answer", "")
        prompt = f"<s>[INST] Question: {question} [/INST]"
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        outputs = model.generate(**inputs, max_new_tokens=config.max_new_tokens)
        decoded = tokenizer.decode(outputs[0], skip_special_tokens=True)
        if answer.strip().lower() in decoded.strip().lower():
            exact_matches += 1
        total += 1

    return {"exact_match": exact_matches / max(total, 1)}
