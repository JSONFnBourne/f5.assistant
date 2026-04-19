from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
import tyro

LOGGER = logging.getLogger("f5nse.grade")


def load_qa(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


SCORE_KEYS = {"factual", "linguistic", "domain", "overall"}


def parse_grade_output(text: str) -> Optional[dict]:
    if "```" in text:
        parts = text.split("```")
        for idx, part in enumerate(parts):
            if part.strip().lower() == "json" and idx + 1 < len(parts):
                snippet = parts[idx + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    continue
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            LOGGER.debug("Failed to parse JSON grade from %s", text)
    return None


def validate_result(result: dict) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    scores = result.get("scores")
    if not isinstance(scores, dict):
        return None
    missing = SCORE_KEYS - scores.keys()
    if missing:
        return None
    try:
        normalized_scores = {k: float(scores[k]) for k in SCORE_KEYS}
    except (TypeError, ValueError, KeyError):
        return None
    verdict = str(result.get("verdict", "")).lower()
    if verdict not in {"pass", "fail"}:
        return None
    feedback = str(result.get("feedback", ""))
    notes = str(result.get("notes", ""))
    tags = result.get("tags", [])
    if not isinstance(tags, list):
        if isinstance(tags, str):
            tags = [tags]
        else:
            tags = []
    tags = [str(tag) for tag in tags]
    missing_facts = result.get("missing_facts", [])
    if not isinstance(missing_facts, list):
        missing_facts = []
    missing_facts = [str(fact) for fact in missing_facts]
    result = {
        "scores": normalized_scores,
        "verdict": verdict,
        "feedback": feedback,
        "notes": notes,
        "tags": tags,
        "missing_facts": missing_facts,
    }
    return result


def make_prompt(question: str, answer: str, context: str) -> str:
    return (
        "You are f5nse-judge, responsible for validating training pairs for an F5 expert assistant.\n"
        "Evaluate the proposed answer using the provided context.\n"
        "Return ONLY JSON with: \n"
        "{\n"
        '  "scores": {\n'
        '    "factual": float between 0 and 1,\n'
        '    "linguistic": float between 0 and 1,\n'
        '    "domain": float between 0 and 1,\n'
        '    "overall": float between 0 and 1 (average of the other three)\n'
        "  },\n"
        '  "verdict": "pass" or "fail",\n'
        '  "feedback": string,\n'
        '  "missing_facts": array of strings,\n'
        '  "tags": array of short strings,\n'
        '  "notes": string\n'
        "}\n"
        "Scoring guidance:\n"
        "- factual: grounding and correctness\n"
        "- linguistic: clarity and completeness\n"
        "- domain: alignment with F5 BIG-IP expertise\n"
        "- overall: arithmetic mean of factual, linguistic, and domain\n\n"
        "Context:\n"
        "-----\n"
        f"{context}\n"
        "-----\n\n"
        f"Question: {question}\n"
        f"Answer: {answer}\n"
    )


@dataclass
class GradeArgs:
    qa_path: Path = Path("data/datasets/qa_raw.jsonl")
    output_path: Path = Path("data/datasets/qa_graded.jsonl")
    judge_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    load_in_8bit: bool = True
    device: str = "cuda"
    min_overall: float = 0.8
    trust_remote_code: bool = False
    max_items: Optional[int] = None
    overwrite: bool = False
    skip_if_exists: bool = False


def setup_judge(args: GradeArgs):
    kwargs = {}
    if args.load_in_8bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        kwargs["device_map"] = "auto"
    elif args.device == "cuda":
        kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(
        args.judge_model, trust_remote_code=args.trust_remote_code, **kwargs
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.judge_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    model.eval()
    return model, tokenizer


def generate_judge_output(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int = 256,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> str:
    device = model.device if hasattr(model, "device") else ("cuda" if torch.cuda.is_available() else "cpu")
    inputs = tokenizer(prompt, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            do_sample=temperature > 0.0,
            pad_token_id=tokenizer.pad_token_id,
        )
    input_len = inputs["input_ids"].shape[1]
    completion_tokens = output[0][input_len:]
    return tokenizer.decode(completion_tokens, skip_special_tokens=True)


def run_grade(args: GradeArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if args.output_path.exists():
        if args.skip_if_exists:
            LOGGER.info("Graded file %s already exists; skipping judge run.", args.output_path)
            return
        if not args.overwrite:
            LOGGER.error("Output %s exists. Remove it or use --overwrite to regenerate.", args.output_path)
            return
        args.output_path.unlink()
    model, tokenizer = setup_judge(args)
    torch.manual_seed(0)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(0)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    retained = 0
    total = 0
    with args.output_path.open("w", encoding="utf-8") as fh:
        for qa in load_qa(args.qa_path):
            prompt = make_prompt(qa["question"], qa["answer"], qa["context"])
            completion = generate_judge_output(
                model,
                tokenizer,
                prompt,
                max_new_tokens=256,
                temperature=0.0,
                top_p=0.9,
            )
            parsed = parse_grade_output(completion)
            total += 1
            if not parsed:
                LOGGER.warning("Judge returned unparsable result; skipping")
                continue
            result = validate_result(parsed)
            if not result:
                LOGGER.warning("Judge returned incomplete or invalid schema; skipping")
                continue
            qa["judge"] = result
            overall = result["scores"]["overall"]
            verdict = result["verdict"]
            if overall >= args.min_overall and verdict == "pass":
                fh.write(json.dumps(qa, ensure_ascii=False) + "\n")
                retained += 1
            if args.max_items and total >= args.max_items:
                break
    LOGGER.info("Judge retained %s/%s items (%.2f%%)", retained, total, (retained / total * 100) if total else 0.0)


def main() -> None:
    args = tyro.cli(GradeArgs)
    run_grade(args)


if __name__ == "__main__":
    main()
