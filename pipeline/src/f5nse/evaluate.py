from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
from datasets import load_dataset
from peft import PeftModel, PeftConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
import tyro

LOGGER = logging.getLogger("f5nse.evaluate")


@dataclass
class EvaluateArgs:
    model_path: Path = Path("data/models/f5nse-lora")
    base_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    eval_path: Path = Path("data/datasets/eval.jsonl")
    judge_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    output_metrics: Path = Path("data/models/eval_metrics.json")
    load_in_8bit: bool = True
    temperature: float = 0.0
    top_p: float = 0.9
    max_new_tokens: int = 512
    judge_threshold: float = 0.8
    trust_remote_code: bool = False
    max_samples: Optional[int] = None
    judge_on_cpu: bool = True


def load_peft_model(args: EvaluateArgs):
    kwargs = {"trust_remote_code": args.trust_remote_code}
    if args.load_in_8bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_8bit=True, llm_int8_enable_fp32_cpu_offload=True
        )
        kwargs["device_map"] = "auto"
    else:
        kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        kwargs["device_map"] = "auto" if torch.cuda.is_available() else None
    base_model = AutoModelForCausalLM.from_pretrained(args.base_model, **kwargs)
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    peft_config = PeftConfig.from_pretrained(args.model_path)
    model = PeftModel.from_pretrained(base_model, args.model_path)
    return model, tokenizer, peft_config


def setup_judge(args: EvaluateArgs):
    judge_kwargs = {"trust_remote_code": args.trust_remote_code}
    if args.judge_on_cpu:
        judge_kwargs.update({"device_map": {"": "cpu"}, "dtype": torch.float32})
    else:
        if args.load_in_8bit:
            judge_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_8bit=True, llm_int8_enable_fp32_cpu_offload=True
            )
            judge_kwargs["device_map"] = "auto"
        else:
            judge_kwargs["dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            judge_kwargs["device_map"] = "auto" if torch.cuda.is_available() else {"": "cpu"}
    try:
        judge_model = AutoModelForCausalLM.from_pretrained(args.judge_model, **judge_kwargs)
    except (ValueError, NotImplementedError) as exc:
        LOGGER.warning("Judge model loading failed (%s); retrying on CPU in float32.", exc)
        fallback_kwargs = {
            "trust_remote_code": args.trust_remote_code,
            "device_map": {"": "cpu"},
            "dtype": torch.float32,
        }
        judge_model = AutoModelForCausalLM.from_pretrained(args.judge_model, **fallback_kwargs)
    judge_tokenizer = AutoTokenizer.from_pretrained(
        args.judge_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if judge_tokenizer.pad_token_id is None:
        judge_tokenizer.pad_token_id = judge_tokenizer.eos_token_id
    judge_model.eval()
    return judge_model, judge_tokenizer


def build_eval_prompt(tokenizer, question: str) -> str:
    system_prompt = (
        "You are f5nse, an expert assistant for F5 BIG-IP, VELOS, and rSeries. "
        "Answer in precise US English with actionable guidance grounded in F5 documentation."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def make_judge_prompt(question: str, reference_answer: str, candidate_answer: str, context: str) -> str:
    return (
        "You are f5nse-judge comparing model output with the reference answer.\n"
        "Assign a score between 0.0 and 1.0 where 1.0 matches the reference and is grounded in context.\n"
        "Return ONLY JSON with 'score', 'verdict', and 'analysis'.\n\n"
        f"Question: {question}\n"
        f"Reference Answer: {reference_answer}\n"
        f"Candidate Answer: {candidate_answer}\n"
        f"Context: {context}\n"
    )


def parse_judge_result(text: str) -> Optional[dict]:
    if "```" in text:
        parts = text.split("```")
        for idx, part in enumerate(parts):
            if part.strip().lower() == "json" and idx + 1 < len(parts):
                try:
                    return json.loads(parts[idx + 1])
                except json.JSONDecodeError:
                    continue
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def run_evaluate(args: EvaluateArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    model, tokenizer, _ = load_peft_model(args)
    judge_model, judge_tokenizer = setup_judge(args)
    dataset = load_dataset("json", data_files={"eval": str(args.eval_path)})["eval"]
    metrics = {"samples": 0, "passes": 0, "scores": []}
    for idx, example in enumerate(dataset):
        if args.max_samples and idx >= args.max_samples:
            break
        question = example.get("question") or example.get("prompt")
        if not question:
            LOGGER.warning("Sample %s is missing question/prompt; skipping.", idx)
            continue
        prompt = build_eval_prompt(tokenizer, question)
        inputs = tokenizer(prompt, return_tensors="pt")
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        do_sample = args.temperature > 0.0
        generation_kwargs = {
            "max_new_tokens": args.max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": tokenizer.pad_token_id,
        }
        if do_sample:
            generation_kwargs["temperature"] = max(args.temperature, 1e-5)
            generation_kwargs["top_p"] = args.top_p
        generation = model.generate(
            **inputs,
            **generation_kwargs,
        )
        generated_text = tokenizer.decode(generation[0], skip_special_tokens=True)
        # Remove prompt portion if present
        if question in generated_text:
            generated_text = generated_text.split(question, 1)[-1].strip()
        reference_answer = example.get("answer") or example.get("completion") or ""
        judge_prompt = make_judge_prompt(
            question,
            reference_answer,
            generated_text,
            example.get("context", ""),
        )
        inputs = judge_tokenizer(judge_prompt, return_tensors="pt")
        device = judge_model.device if hasattr(judge_model, "device") else ("cuda" if torch.cuda.is_available() else "cpu")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = judge_model.generate(
                **inputs,
                max_new_tokens=256,
                temperature=0.0,
                top_p=0.9,
                do_sample=False,
                pad_token_id=judge_tokenizer.pad_token_id,
            )
        input_len = inputs["input_ids"].shape[1]
        judge_tokens = outputs[0][input_len:]
        judge_output = judge_tokenizer.decode(judge_tokens, skip_special_tokens=True)
        parsed = parse_judge_result(judge_output)
        if not parsed:
            continue
        score = float(parsed.get("score", 0.0))
        verdict = parsed.get("verdict", "").lower()
        metrics["samples"] += 1
        metrics["scores"].append(score)
        if score >= args.judge_threshold and verdict == "pass":
            metrics["passes"] += 1
    accuracy = metrics["passes"] / metrics["samples"] if metrics["samples"] else 0.0
    avg_score = sum(metrics["scores"]) / len(metrics["scores"]) if metrics["scores"] else 0.0
    report = {
        "samples": metrics["samples"],
        "passes": metrics["passes"],
        "accuracy": accuracy,
        "average_score": avg_score,
        "threshold": args.judge_threshold,
    }
    args.output_metrics.parent.mkdir(parents=True, exist_ok=True)
    args.output_metrics.write_text(json.dumps(report, indent=2))
    LOGGER.info("Evaluation complete: %s", report)


def main() -> None:
    args = tyro.cli(EvaluateArgs)
    run_evaluate(args)


if __name__ == "__main__":
    main()
