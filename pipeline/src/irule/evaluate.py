from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import torch
import tyro
from datasets import load_dataset
from peft import PeftConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

LOGGER = logging.getLogger("irule.evaluate")

# Reduce VRAM fragmentation during eval
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:64")


@dataclass
class EvaluateArgs:
    model_path: Path = Path("data/models/irule-lora")
    base_model: str = "meta-llama/Llama-3.2-3B-Instruct"
    eval_path: Path = Path("data/datasets/eval.jsonl")
    judge_model: str = "meta-llama/Llama-3.2-3B-Instruct"
    output_metrics: Path = Path("data/models/eval_metrics.json")
    quantization: Literal["4bit", "8bit", "none"] = "4bit"
    max_gpu_memory: str | None = None
    max_cpu_memory: str | None = "48GiB"
    temperature: float = 0.0
    top_p: float = 0.9
    max_new_tokens: int = 512
    judge_threshold: float = 0.85
    trust_remote_code: bool = False
    max_samples: int | None = None
    judge_on_cpu: bool = False
    gen_batch_size: int = 4
    judge_batch_size: int = 8
    candidates_cache: Path | None = Path("data/models/eval_candidates.jsonl")
    use_cache: bool = True
    overwrite_cache: bool = False


def load_peft_model(args: EvaluateArgs):
    kwargs = {"trust_remote_code": args.trust_remote_code, "low_cpu_mem_usage": True}
    use_cuda = torch.cuda.is_available()
    target_device = "cuda:0" if use_cuda else "cpu"
    quant_mode = args.quantization
    if quant_mode == "4bit":
        if not use_cuda:
            raise RuntimeError("4-bit inference requires a CUDA-capable GPU.")
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
        )
        kwargs["device_map"] = {"": target_device}
    elif quant_mode == "8bit":
        if not use_cuda:
            raise RuntimeError("8-bit inference requires a CUDA-capable GPU.")
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_8bit=True, llm_int8_enable_fp32_cpu_offload=True
        )
        kwargs["device_map"] = {"": target_device}
    else:
        dtype = torch.float16 if use_cuda else torch.float32
        kwargs["torch_dtype"] = dtype
        if use_cuda:
            kwargs["device_map"] = {"": target_device}
    if args.max_gpu_memory and use_cuda:
        kwargs.setdefault("max_memory", {})[0] = args.max_gpu_memory
    if args.max_cpu_memory:
        kwargs.setdefault("max_memory", {})["cpu"] = args.max_cpu_memory

    LOGGER.info(
        "Eval model load plan: base=%s quant=%s use_cuda=%s device_map=%s",
        args.base_model,
        quant_mode,
        use_cuda,
        kwargs.get("device_map"),
    )

    try:
        peft_config = PeftConfig.from_pretrained(args.model_path)
        base_model = AutoModelForCausalLM.from_pretrained(args.base_model, **kwargs)
        tokenizer = AutoTokenizer.from_pretrained(
            args.base_model, trust_remote_code=args.trust_remote_code, use_fast=True
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        tokenizer.padding_side = "left"
        model = PeftModel.from_pretrained(base_model, args.model_path)
    except (OSError, ValueError):
        LOGGER.info(
            "No PEFT adapters detected at %s; loading merged model directly.", args.model_path
        )
        peft_config = None
        model = AutoModelForCausalLM.from_pretrained(args.model_path, **kwargs)
        tokenizer = AutoTokenizer.from_pretrained(
            args.model_path, trust_remote_code=args.trust_remote_code, use_fast=True
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        tokenizer.padding_side = "left"

    model.eval()
    if use_cuda:
        try:
            device_map = getattr(model, "hf_device_map", None)
            if device_map:
                LOGGER.info("Eval model device_map: %s", device_map)
            else:
                LOGGER.info("Eval model device: %s", getattr(model, "device", "unknown"))
        except Exception:
            pass

        def _is_cuda(mod):
            try:
                return next(mod.parameters()).is_cuda
            except StopIteration:
                return False

        if not _is_cuda(model):
            try:
                model.to("cuda")
                LOGGER.info("Eval model moved to cuda manually.")
            except Exception as exc:
                LOGGER.warning("Failed to move eval model to cuda: %s", exc)

    return model, tokenizer, peft_config


def setup_judge(args: EvaluateArgs):
    judge_kwargs = {"trust_remote_code": args.trust_remote_code, "low_cpu_mem_usage": True}
    if args.judge_on_cpu:
        judge_kwargs.update({"device_map": {"": "cpu"}, "dtype": torch.float32})
    else:
        quant_mode = args.quantization
        if quant_mode == "4bit":
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "4-bit judge mode requires a CUDA-capable GPU or enable --judge-on-cpu."
                )
            judge_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
            judge_kwargs["device_map"] = {"": "cuda:0"}
        elif quant_mode == "8bit":
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "8-bit judge mode requires a CUDA-capable GPU or enable --judge-on-cpu."
                )
            judge_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_8bit=True, llm_int8_enable_fp32_cpu_offload=True
            )
            judge_kwargs["device_map"] = {"": "cuda:0"}
        else:
            if torch.cuda.is_available():
                judge_kwargs["dtype"] = torch.float16
                judge_kwargs["device_map"] = {"": "cuda:0"}
            else:
                judge_kwargs["dtype"] = torch.float32
                judge_kwargs["device_map"] = {"": "cpu"}
        if args.max_gpu_memory and torch.cuda.is_available():
            judge_kwargs.setdefault("max_memory", {})[0] = args.max_gpu_memory
        if args.max_cpu_memory:
            judge_kwargs.setdefault("max_memory", {})["cpu"] = args.max_cpu_memory
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
    judge_tokenizer.padding_side = "left"
    judge_model.eval()
    if not args.judge_on_cpu and torch.cuda.is_available():
        try:
            device_map = getattr(judge_model, "hf_device_map", None)
            if device_map:
                LOGGER.info("Judge model device_map: %s", device_map)
            else:
                LOGGER.info("Judge model device: %s", getattr(judge_model, "device", "unknown"))
        except Exception:
            pass

        def _is_cuda(mod):
            try:
                return next(mod.parameters()).is_cuda
            except StopIteration:
                return False

        if not _is_cuda(judge_model):
            try:
                judge_model.to("cuda")
                LOGGER.info("Judge model moved to cuda manually.")
            except Exception as exc:
                LOGGER.warning("Failed to move judge model to cuda: %s", exc)
    return judge_model, judge_tokenizer


def build_eval_prompt(tokenizer, question: str) -> str:
    system_prompt = (
        "You are irule, an expert assistant for F5 BIG-IP, VELOS, and rSeries. "
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


def make_judge_prompt(
    question: str, reference_answer: str, candidate_answer: str, context: str
) -> str:
    return (
        "You are irule-judge comparing model output with the reference answer.\n"
        "Assign a score between 0.0 and 1.0 where 1.0 matches the reference and is grounded in context.\n"
        "Return ONLY JSON with 'score', 'verdict', and 'analysis'.\n\n"
        f"Question: {question}\n"
        f"Reference Answer: {reference_answer}\n"
        f"Candidate Answer: {candidate_answer}\n"
        f"Context: {context}\n"
    )


def parse_judge_result(text: str) -> dict | None:
    """Robustly extract a JSON object from judge output (mirrors grade.py)."""

    def iter_candidates(response: str):
        if "```" in response:
            parts = response.split("```")
            for idx, part in enumerate(parts):
                header = part.strip().lower()
                if header == "json" and idx + 1 < len(parts):
                    yield parts[idx + 1]
        yield response

    def iter_objects(snippet: str):
        snippet = snippet.strip()
        try:
            obj = json.loads(snippet)
            if isinstance(obj, dict):
                yield obj
        except json.JSONDecodeError:
            pass
        in_string = False
        escape = False
        depth = 0
        start_idx = None
        for i, ch in enumerate(snippet):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        candidate = snippet[start_idx : i + 1]
                        try:
                            obj = json.loads(candidate)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(obj, dict):
                            yield obj
                        start_idx = None

    for candidate_text in iter_candidates(text):
        for obj in iter_objects(candidate_text):
            if {"score", "verdict"}.issubset(obj.keys()):
                return obj
    return None


def run_evaluate(args: EvaluateArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    dataset = load_dataset("json", data_files={"eval": str(args.eval_path)})["eval"]

    # Phase 1: load fine-tuned model and generate candidates (GPU if available/4bit)
    generations: list[dict] = []
    # Optional cache reuse
    if (
        args.candidates_cache
        and args.use_cache
        and not args.overwrite_cache
        and Path(args.candidates_cache).exists()
    ):
        with Path(args.candidates_cache).open("r", encoding="utf-8") as fh:
            for line in fh:
                generations.append(json.loads(line))
        LOGGER.info("Loaded %s cached candidates from %s", len(generations), args.candidates_cache)
    else:
        model, tokenizer, _ = load_peft_model(args)
        model.eval()
        # Collect samples up to max_samples
        eval_samples = []
        for idx, example in enumerate(dataset):
            if args.max_samples and idx >= args.max_samples:
                break
            q = example.get("question") or example.get("prompt")
            if not q:
                LOGGER.warning("Sample %s is missing question/prompt; skipping.", idx)
                continue
            eval_samples.append(
                {
                    "question": q,
                    "reference": example.get("answer") or example.get("completion") or "",
                    "context": example.get("context", ""),
                }
            )

        do_sample = args.temperature > 0.0
        generation_kwargs = {
            "max_new_tokens": args.max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": tokenizer.pad_token_id,
        }
        if do_sample:
            generation_kwargs["temperature"] = max(args.temperature, 1e-5)
            generation_kwargs["top_p"] = args.top_p

        bs = max(1, args.gen_batch_size)
        # Left-pad so every row's generated tokens start at the same index; this
        # lets us slice off the prompt cleanly instead of string-splitting on the
        # question (which left the chat template's "assistant" header glued to
        # every candidate and skewed the judge).
        tokenizer.padding_side = "left"
        for i in range(0, len(eval_samples), bs):
            batch = eval_samples[i : i + bs]
            prompts = [build_eval_prompt(tokenizer, b["question"]) for b in batch]
            inputs = tokenizer(prompts, return_tensors="pt", padding=True)
            inputs = {k: v.to(model.device) for k, v in inputs.items()}
            input_len = inputs["input_ids"].shape[1]
            with torch.no_grad():
                outputs = model.generate(**inputs, **generation_kwargs)
            # Decode only the newly generated continuation, not the prompt.
            texts = tokenizer.batch_decode(outputs[:, input_len:], skip_special_tokens=True)
            for b, out in zip(batch, texts):
                generations.append(
                    {
                        "question": b["question"],
                        "candidate": out.strip(),
                        "reference": b["reference"],
                        "context": b["context"],
                    }
                )
        # Write cache
        if args.candidates_cache:
            p = Path(args.candidates_cache)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", encoding="utf-8") as fh:
                for item in generations:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            LOGGER.info("Cached %s candidates to %s", len(generations), p)

    # Free model to reclaim VRAM before loading judge
    try:
        del model
        torch.cuda.empty_cache()
    except Exception:
        pass

    # Phase 2: load judge model (enable GPU if args.judge_on_cpu is False)
    judge_model, judge_tokenizer = setup_judge(args)
    metrics = {"samples": 0, "passes": 0, "scores": [], "skipped": 0}
    jbs = max(1, args.judge_batch_size)

    def judge_batch(prompts: list[str]) -> list[str]:
        inputs = judge_tokenizer(prompts, return_tensors="pt", padding=True)
        device = (
            judge_model.device
            if hasattr(judge_model, "device")
            else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = judge_model.generate(
                **inputs,
                max_new_tokens=128,
                temperature=0.0,
                top_p=0.9,
                do_sample=False,
                pad_token_id=judge_tokenizer.pad_token_id,
            )
        # The judge tokenizer left-pads, so every row's generation starts at the
        # uniform padded prompt length — slice there (mirrors the generation
        # phase above), not at the per-row unpadded length.
        input_len = inputs["input_ids"].shape[1]
        return judge_tokenizer.batch_decode(outputs[:, input_len:], skip_special_tokens=True)

    for i in range(0, len(generations), jbs):
        batch = generations[i : i + jbs]
        prompts = [
            make_judge_prompt(b["question"], b["reference"], b["candidate"], b["context"])
            for b in batch
        ]
        texts = judge_batch(prompts)
        for b, t in zip(batch, texts):
            parsed = parse_judge_result(t)
            if not parsed:
                retry = (
                    make_judge_prompt(b["question"], b["reference"], b["candidate"], b["context"])
                    + "\n\nReminder: respond with a single JSON object containing 'score', 'verdict', and 'analysis'."
                )
                t2 = judge_batch([retry])[0]
                parsed = parse_judge_result(t2)
            if not parsed:
                LOGGER.warning("Judge returned unparsable result after retry; skipping sample")
                metrics["skipped"] += 1
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
        "skipped": metrics["skipped"],
    }
    args.output_metrics.parent.mkdir(parents=True, exist_ok=True)
    args.output_metrics.write_text(json.dumps(report, indent=2))
    LOGGER.info("Evaluation complete: %s", report)


def main() -> None:
    args = tyro.cli(EvaluateArgs)
    run_evaluate(args)


if __name__ == "__main__":
    main()
