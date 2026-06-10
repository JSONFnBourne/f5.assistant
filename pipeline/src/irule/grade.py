from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import torch
import tyro
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from .rulebook import (
    DEFAULT_OPERATORS,
    Rulebook,
    discover_events,
    extract_command_tokens,
    load_rulebook,
)

LOGGER = logging.getLogger("irule.grade")


def load_qa(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


SCORE_KEYS = {"factual", "linguistic", "domain", "overall"}


def parse_grade_output(text: str) -> dict | None:
    """Extract and parse a judge JSON object from LLM output.

    More robust than naive slicing:
    - Accepts fenced blocks with or without a `json` tag
    - Falls back to balanced-brace extraction of JSON objects
    - Tries multiple candidates and returns the first valid schema
    """

    candidates: list[str] = []

    # 1) Pull out any fenced code blocks (```json ... ``` or ``` ... ```)
    for pattern in (r"```\s*json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"):
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            snippet = m.group(1).strip()
            if snippet:
                candidates.append(snippet)

    # 2) Add the whole text as a last-resort candidate
    candidates.append(text)

    def iter_json_objects(s: str):
        in_str = False
        esc = False
        depth = 0
        start_idx = None
        for i, ch in enumerate(s):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            else:
                if ch == '"':
                    in_str = True
                    continue
                if ch == "{":
                    if depth == 0:
                        start_idx = i
                    depth += 1
                elif ch == "}":
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and start_idx is not None:
                            yield s[start_idx : i + 1]
                            start_idx = None

    for candidate in candidates:
        # Try direct parse of candidate
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and "scores" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        # Try balanced-brace slices within candidate
        for obj_text in iter_json_objects(candidate):
            try:
                obj = json.loads(obj_text)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "scores" in obj:
                return obj

    LOGGER.debug("Failed to parse JSON grade from output: %r", text[:500])
    return None


def validate_result(result: dict) -> dict | None:
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
        "You are irule-judge, responsible for validating training pairs for an F5 expert assistant.\n"
        "Evaluate the proposed answer using the provided context.\n"
        "Respond with a SINGLE JSON object only. No code fences, no extra text.\n"
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
    judge_model: str = "meta-llama/Llama-3.2-3B-Instruct"
    quantization: Literal["4bit", "8bit", "none"] = "4bit"
    max_gpu_memory: str | None = None
    max_cpu_memory: str | None = "48GiB"
    device: str = "cuda"
    min_overall: float = 0.85
    trust_remote_code: bool = False
    max_items: int | None = None
    overwrite: bool = False
    skip_if_exists: bool = False
    rulebook_path: Path | None = Path("data/rulebook/command_tokens.json")


def setup_judge(args: GradeArgs):
    kwargs = {"trust_remote_code": args.trust_remote_code, "low_cpu_mem_usage": True}
    use_cuda = args.device == "cuda" and torch.cuda.is_available()
    target_device = "cuda:0" if use_cuda else "cpu"
    quant_mode = args.quantization
    if quant_mode == "4bit":
        if not use_cuda:
            raise RuntimeError("4-bit judging requires --device cuda and a CUDA-capable GPU.")
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
        )
        kwargs["device_map"] = {"": target_device}
    elif quant_mode == "8bit":
        if not use_cuda:
            raise RuntimeError("8-bit judging requires --device cuda and a CUDA-capable GPU.")
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        kwargs["device_map"] = {"": target_device}
    elif use_cuda:
        kwargs["torch_dtype"] = torch.float16
        kwargs["device_map"] = {"": target_device}
    else:
        kwargs["torch_dtype"] = torch.float32
    if use_cuda and args.max_gpu_memory:
        kwargs.setdefault("max_memory", {})[0] = args.max_gpu_memory
    if args.max_cpu_memory:
        kwargs.setdefault("max_memory", {})["cpu"] = args.max_cpu_memory
    model = AutoModelForCausalLM.from_pretrained(
        args.judge_model,
        **kwargs,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.judge_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    model.eval()
    if use_cuda:
        try:
            device_map = getattr(model, "hf_device_map", None)
            if device_map:
                LOGGER.info("Judge device_map: %s", device_map)
            else:
                LOGGER.info("Judge primary device: %s", getattr(model, "device", "unknown"))
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
                LOGGER.info("Judge moved to cuda manually.")
            except Exception as exc:
                LOGGER.warning("Failed to move judge to cuda: %s", exc)
    return model, tokenizer


def generate_judge_output(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int = 256,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> str:
    device = (
        model.device
        if hasattr(model, "device")
        else ("cuda" if torch.cuda.is_available() else "cpu")
    )
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
            LOGGER.error(
                "Output %s exists. Remove it or use --overwrite to regenerate.", args.output_path
            )
            return
        args.output_path.unlink()
    model, tokenizer = setup_judge(args)
    torch.manual_seed(0)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(0)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    retained = 0
    total = 0
    rulebook: Rulebook = Rulebook(set(), set(), set(DEFAULT_OPERATORS), {})
    if args.rulebook_path:
        rulebook = load_rulebook(args.rulebook_path)
        if args.rulebook_path.exists():
            LOGGER.info(
                "Loaded rulebook with %s commands, %s events, %s operators",
                len(rulebook.commands),
                len(rulebook.events),
                len(rulebook.operators),
            )
        else:
            LOGGER.warning(
                "Rulebook not found at %s; domain validation limited during grading.",
                args.rulebook_path,
            )
    else:
        LOGGER.warning("Rulebook path not provided; domain validation limited during grading.")
    with args.output_path.open("w", encoding="utf-8") as fh:
        for qa in load_qa(args.qa_path):
            question = qa.get("question", "")
            answer = qa.get("answer", "")
            context = qa.get("context", "")
            q_lower = question.lower()
            a_lower = answer.lower()

            # Command validation
            question_cmds = extract_command_tokens(question)
            answer_cmds = extract_command_tokens(answer)
            combined_cmds = question_cmds.union(answer_cmds)
            context_cmds = extract_command_tokens(context)
            if combined_cmds and not combined_cmds.issubset(context_cmds):
                LOGGER.warning(
                    "Skipping QA due to command/context mismatch: %s",
                    sorted(combined_cmds - context_cmds),
                )
                continue
            if (
                rulebook.commands
                and combined_cmds
                and not combined_cmds.issubset(rulebook.commands)
            ):
                LOGGER.warning(
                    "Skipping QA due to commands not present in rulebook: %s",
                    sorted(combined_cmds - rulebook.commands),
                )
                continue

            # Event validation
            potential_events = {
                tok
                for tok in re.findall(r"\b([A-Z][A-Z0-9_]+)\b", question + " " + answer)
                if "_" in tok
            }
            context_events = discover_events(context)
            if potential_events and not potential_events.issubset(context_events):
                LOGGER.warning(
                    "Skipping QA due to events not in context: %s",
                    sorted(potential_events - context_events),
                )
                continue

            # Terminology consistency (only flag events mislabeled as commands)
            mislabelled_event = False
            for event_token in potential_events:
                token_lower = event_token.lower()
                if f"{token_lower} command" in q_lower or f"{token_lower} command" in a_lower:
                    mislabelled_event = True
                    break
            if mislabelled_event:
                LOGGER.warning("Skipping QA due to event labeled as command: %s", question)
                continue

            prompt = make_prompt(question, answer, context)
            completion = generate_judge_output(
                model,
                tokenizer,
                prompt,
                max_new_tokens=256,
                temperature=0.0,
                top_p=0.9,
            )
            parsed = parse_grade_output(completion)
            if not parsed:
                retry_prompt = (
                    prompt
                    + "\n\nReminder: respond with a single JSON object exactly matching the specified schema."
                )
                completion = generate_judge_output(
                    model,
                    tokenizer,
                    retry_prompt,
                    max_new_tokens=256,
                    temperature=0.0,
                    top_p=0.9,
                )
                parsed = parse_grade_output(completion)
            total += 1
            if not parsed:
                LOGGER.warning("Judge returned unparsable result after retry; skipping")
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
    LOGGER.info(
        "Judge retained %s/%s items (%.2f%%)",
        retained,
        total,
        (retained / total * 100) if total else 0.0,
    )


def main() -> None:
    args = tyro.cli(GradeArgs)
    run_grade(args)


if __name__ == "__main__":
    main()
