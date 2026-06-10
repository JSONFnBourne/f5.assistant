from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from .rulebook import (
    Rulebook,
    DEFAULT_OPERATORS,
    discover_events,
    extract_command_tokens,
    extract_operator_tokens,
    load_rulebook,
)
import tyro

LOGGER = logging.getLogger("irule.generate")


def load_chunks(path: Path) -> List[dict]:
    chunks: List[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            chunks.append(json.loads(line))
    return chunks


def extract_json_block(text: str) -> List[dict]:
    """Extract a JSON array from model output.

    Tries fenced blocks first, then searches for the first balanced array. Only emits a
    warning if every strategy fails so the log isn't spammed for partial successes.
    """

    def parse_candidate(snippet: str) -> Optional[List[dict]]:
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None

    # 1) Try fenced code blocks (```json ... ```)
    if "```" in text:
        parts = text.split("```")
        for idx in range(len(parts)):
            header = parts[idx].strip().lower()
            if header in {"json", "jsonl"} and idx + 1 < len(parts):
                candidate = parse_candidate(parts[idx + 1])
                if candidate is not None:
                    return candidate

    # 2) Scan for a balanced JSON array manually (handles trailing prose)
    bracket_level = 0
    start_idx = None
    for i, ch in enumerate(text):
        if ch == "[":
            if bracket_level == 0:
                start_idx = i
            bracket_level += 1
        elif ch == "]" and bracket_level:
            bracket_level -= 1
            if bracket_level == 0 and start_idx is not None:
                candidate = parse_candidate(text[start_idx : i + 1])
                if candidate is not None:
                    return candidate
                # keep searching in case later arrays parse correctly
                start_idx = None

    LOGGER.warning("Generator response lacked a parsable JSON array; skipping chunk.")
    return []


def normalize_command_spacing(text: str) -> str:
    """Collapse spaces around double-colon namespaces like STREAM :: match."""

    def replacer(match: re.Match[str]) -> str:
        left = match.group(1)
        right = match.group(2)
        return f"{left}::{right}"

    # Collapse "NAME :: identifier" patterns (case-sensitive for the left part in caps)
    text = re.sub(r"\b([A-Z][A-Z0-9_:]*)\s+::\s+([A-Za-z0-9_:-]+)", replacer, text)
    return text


def build_context_from_entity(entity: dict) -> str:
    parts: List[str] = []
    description = entity.get("description") or entity.get("summary")
    if description:
        parts.append(description.strip())
    syntax = entity.get("syntax")
    if syntax:
        parts.append(f"Syntax: {syntax.strip()}")
    valid_events = entity.get("valid_events") or []
    if valid_events:
        parts.append("Valid Events: " + ", ".join(valid_events))
    examples = entity.get("examples") or []
    if examples:
        parts.append("Examples:\n" + "\n\n".join(examples))
    event_order = entity.get("event_order") or {}
    if event_order:
        formatted = []
        for key in ("phase", "occurs_when", "fires", "notes"):
            value = event_order.get(key)
            if value:
                formatted.append(f"{key.replace('_', ' ').title()}: {value}")
        if formatted:
            parts.append("Event Order:\n" + "\n".join(formatted))
    return "\n\n".join(part for part in parts if part).strip()


def build_qas_from_entity(entity: dict) -> List[dict]:
    name = entity.get("name", "").strip()
    if not name:
        return []
    kind = entity.get("kind")
    module = entity.get("module")
    context = build_context_from_entity(entity)
    source_url = entity.get("source_url")
    qas: List[dict] = []

    def add(question: str, answer: Optional[str]) -> None:
        if not answer:
            return
        qas.append(
            {
                "question": question.strip(),
                "answer": answer.strip(),
                "reference": name,
                "context": context,
                "source_url": source_url,
                "module": module,
            }
        )

    if kind == "command":
        description = entity.get("description") or entity.get("summary")
        if description:
            add(f"What does the {name} command do?", description)
        syntax = entity.get("syntax")
        if syntax:
            add(f"What is the syntax of the {name} command?", syntax)
        valid_events = entity.get("valid_events") or []
        if valid_events:
            add(
                f"Which events can be used with the {name} command?",
                ", ".join(valid_events),
            )
    elif kind == "event":
        summary = entity.get("description") or entity.get("summary")
        if summary:
            add(f"When does the {name} event fire?", summary)
        order_meta = entity.get("event_order") or {}
        occurs_when = order_meta.get("occurs_when")
        if occurs_when:
            add(f"What happens during the {name} event?", occurs_when)
        phase = order_meta.get("phase")
        if phase:
            add(f"During which phase does the {name} event occur?", phase)
    elif kind == "module":
        description = entity.get("description")
        if description:
            add(f"What does the {name} module cover?", description)

    return qas


def run_generate_from_entities(args: GenerateArgs) -> None:
    if not args.entities_path or not args.entities_path.exists():
        raise FileNotFoundError(f"Entities file {args.entities_path} not found")
    with args.entities_path.open("r", encoding="utf-8") as fh:
        entities = [json.loads(line) for line in fh if line.strip()]
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    seen_questions: set[str] = set()
    with args.output_path.open("w", encoding="utf-8") as out_fh:
        for entity in entities:
            for record in build_qas_from_entity(entity):
                question = record["question"].strip()
                if question in seen_questions:
                    continue
                seen_questions.add(question)
                out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                if args.max_records and written >= args.max_records:
                    LOGGER.info(
                        "Generated %s records from entities (target=%s)",
                        written,
                        args.max_records,
                    )
                    return
    LOGGER.info("Generated %s records from entities", written)


def build_prompt(context: str, pairs: int) -> str:
    return (
        "You are irule, an expert on F5 BIG-IP platforms. "
        "Given the context below, produce tightly scoped question and answer pairs. "
        "Questions must rely on the provided context and answers must quote or paraphrase it faithfully. "
        "Respond ONLY with a JSON array; no markdown fences, no commentary. "
        "Each item MUST contain keys 'question', 'answer', and 'reference' where 'reference' is a short citation pointing to the relevant section title or heading.\n\n"
        f"Produce exactly {pairs} items.\n\n"
        "Example format:\n"
        "[\n"
        "  {\"question\": \"...\", \"answer\": \"...\", \"reference\": \"...\"}\n"
        "]\n\n"
        "Context:\n"
        "-----\n"
        f"{context}\n"
        "-----"
    )


@dataclass
class GenerateArgs:
    chunks_path: Path = Path("data/chunks/chunks.jsonl")
    output_path: Path = Path("data/datasets/qa_raw.jsonl")
    generator_model: str = "meta-llama/Llama-3.2-3B-Instruct"
    quantization: Literal["4bit", "8bit", "none"] = "4bit"
    max_gpu_memory: Optional[str] = None
    max_cpu_memory: Optional[str] = "48GiB"
    device: str = "cuda"
    pairs_per_chunk: int = 2
    max_records: Optional[int] = None
    rulebook_path: Optional[Path] = Path("data/rulebook/command_tokens.json")
    temperature: float = 0.0
    top_p: float = 0.9
    max_new_tokens: int = 512
    seed: int = 42
    trust_remote_code: bool = False
    entities_path: Optional[Path] = None
    overwrite: bool = False
    skip_if_exists: bool = False


def setup_generator(args: GenerateArgs):
    kwargs = {"low_cpu_mem_usage": True}
    use_cuda = torch.cuda.is_available() and args.device.startswith("cuda")
    target_device = "cuda:0" if use_cuda else "cpu"
    quant_mode = args.quantization
    if quant_mode == "4bit":
        if not use_cuda:
            raise RuntimeError("4-bit generation requires --device cuda and a CUDA-capable GPU.")
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
        )
        kwargs["device_map"] = {"": target_device}
    elif quant_mode == "8bit":
        if not use_cuda:
            raise RuntimeError("8-bit generation requires --device cuda and a CUDA-capable GPU.")
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        kwargs["device_map"] = {"": target_device}
    elif use_cuda:
        kwargs["torch_dtype"] = torch.float16
        kwargs["device_map"] = {"": target_device}
    else:
        kwargs["torch_dtype"] = torch.float32
    if quant_mode in {"4bit", "8bit"} and (args.max_gpu_memory or args.max_cpu_memory):
        device_id = torch.cuda.current_device()
        max_memory = {}
        if args.max_gpu_memory:
            max_memory[device_id] = args.max_gpu_memory
        if args.max_cpu_memory:
            max_memory["cpu"] = args.max_cpu_memory
        kwargs["max_memory"] = max_memory
    LOGGER.info(
        "Generator load plan: model=%s quant=%s use_cuda=%s device_map=%s",
        args.generator_model,
        quant_mode,
        use_cuda,
        kwargs.get("device_map"),
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.generator_model, trust_remote_code=args.trust_remote_code, **kwargs
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.generator_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    model.eval()
    # Log placement/quantization so users can confirm GPU usage
    try:
        device_map = getattr(model, "hf_device_map", None)
        if device_map:
            LOGGER.info("Generator loaded with device_map: %s", device_map)
        else:
            LOGGER.info("Generator primary device: %s", getattr(model, "device", "unknown"))
    except Exception:
        pass

    def _is_cuda(mod) -> bool:
        try:
            return next(mod.parameters()).is_cuda
        except StopIteration:
            return False

    if use_cuda and not _is_cuda(model):
        try:
            model.to("cuda")
            LOGGER.info("Generator moved to cuda manually.")
        except Exception as exc:
            LOGGER.warning("Failed to move generator to cuda: %s", exc)
    return model, tokenizer


def generate_completion(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    device_pref: str,
) -> str:
    use_cuda = torch.cuda.is_available() and device_pref.startswith("cuda")
    target_device = "cuda" if use_cuda else "cpu"
    device = getattr(model, "device", torch.device(target_device))
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    input_len = inputs["input_ids"].shape[1]
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            do_sample=temperature > 0.0,
            pad_token_id=tokenizer.pad_token_id,
        )
    generated_tokens = output[0]
    if generated_tokens.size(0) > input_len:
        generated_tokens = generated_tokens[input_len:]
    return tokenizer.decode(generated_tokens, skip_special_tokens=True)


def run_generate(args: GenerateArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if args.entities_path:
        run_generate_from_entities(args)
        return
    if args.output_path.exists():
        if args.skip_if_exists:
            LOGGER.info("Generation output %s already exists; skipping.", args.output_path)
            return
        if not args.overwrite:
            LOGGER.error("Output %s exists. Remove it or rerun with --overwrite.", args.output_path)
            return
        args.output_path.unlink()
    chunks = load_chunks(args.chunks_path)
    if not chunks:
        LOGGER.error("No chunks found at %s", args.chunks_path)
        return
    random.Random(args.seed).shuffle(chunks)
    rulebook: Rulebook = Rulebook(set(), set(), set(DEFAULT_OPERATORS), {})
    if args.rulebook_path:
        rulebook = load_rulebook(args.rulebook_path)
        if args.rulebook_path.exists():
            LOGGER.info(
                "Loaded rulebook with %s commands, %s events, %s operators from %s",
                len(rulebook.commands),
                len(rulebook.events),
                len(rulebook.operators),
                args.rulebook_path,
            )
        else:
            LOGGER.warning("Rulebook not found at %s; domain validation limited.", args.rulebook_path)
    else:
        LOGGER.warning("Rulebook path not provided; domain validation limited.")
    skipped_chunks = 0
    skipped_commands = 0
    skipped_duplicates = 0
    skipped_events = 0
    skipped_operators = 0
    seen_questions: set[str] = set()
    if args.quantization in {"4bit", "8bit"} and not (
        torch.cuda.is_available() and args.device.startswith("cuda")
    ):
        raise RuntimeError(
            f"{args.quantization} generation requires a CUDA-capable device. "
            "Switch --device to cuda or set --quantization none."
        )
    model, tokenizer = setup_generator(args)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    written = 0
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    failure_log_path = args.output_path.with_name("qa_failures.jsonl")
    if args.overwrite and failure_log_path.exists():
        failure_log_path.unlink()
    with args.output_path.open("w", encoding="utf-8") as fh, failure_log_path.open(
        "a", encoding="utf-8"
    ) as failure_fh:
        for chunk in chunks:
            context_text = normalize_command_spacing(chunk["chunk"])
            if "deprecated" in context_text.lower():
                skipped_chunks += 1
                continue
            context_commands = extract_command_tokens(context_text)
            context_events = discover_events(context_text)
            context_ops = extract_operator_tokens(context_text, rulebook.operators)
            prompt = build_prompt(context_text, args.pairs_per_chunk)
            completion = generate_completion(
                model,
                tokenizer,
                prompt,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
                device_pref=args.device,
            )
            qa_items = extract_json_block(completion)
            # extract_json_block always returns a list ([] on failure), so test
            # emptiness, not type — otherwise the retry/failure paths never fire.
            if not qa_items:
                LOGGER.debug("Initial generation failed to yield JSON; retrying deterministically.")
                retry_prompt = (
                    prompt
                    + "\n\nRemember: respond ONLY with a JSON array of objects with keys 'question', 'answer', and 'reference'."
                )
                completion = generate_completion(
                    model,
                    tokenizer,
                    retry_prompt,
                    max_new_tokens=args.max_new_tokens,
                    temperature=0.0,
                    top_p=1.0,
                    device_pref=args.device,
                )
                qa_items = extract_json_block(completion)
            if not qa_items:
                json.dump(
                    {
                        "url": chunk["url"],
                        "prompt": prompt,
                        "completion": completion,
                        "reason": "unparsable",
                    },
                    failure_fh,
                    ensure_ascii=False,
                )
                failure_fh.write("\n")
                skipped_chunks += 1
                continue
            filtered = []
            for item in qa_items:
                if not isinstance(item, dict):
                    continue
                question = item.get("question")
                answer = item.get("answer")
                reference = item.get("reference", "")
                if not question or not answer:
                    continue
                question = normalize_command_spacing(question.strip())
                answer = normalize_command_spacing(answer.strip())
                reference = normalize_command_spacing(reference.strip())
                q_lower = question.lower()
                a_lower = answer.lower()

                question_commands = extract_command_tokens(question)
                answer_commands = extract_command_tokens(answer)
                combined_commands = question_commands.union(answer_commands)
                if combined_commands and not combined_commands.issubset(context_commands):
                    json.dump(
                        {
                            "url": chunk["url"],
                            "prompt": prompt,
                            "completion": completion,
                            "question": question,
                            "reason": "command_not_in_context",
                            "unknown_tokens": sorted(combined_commands - context_commands),
                        },
                        failure_fh,
                        ensure_ascii=False,
                    )
                    failure_fh.write("\n")
                    skipped_commands += 1
                    continue
                if rulebook.commands and combined_commands and not combined_commands.issubset(rulebook.commands):
                    json.dump(
                        {
                            "url": chunk["url"],
                            "prompt": prompt,
                            "completion": completion,
                            "question": question,
                            "reason": "command_not_in_rulebook",
                            "unknown_tokens": sorted(combined_commands - rulebook.commands),
                        },
                        failure_fh,
                        ensure_ascii=False,
                    )
                    failure_fh.write("\n")
                    skipped_commands += 1
                    continue

                potential_events = {tok for tok in re.findall(r"\b([A-Z][A-Z0-9_]+)\b", question + " " + answer) if "_" in tok}
                if potential_events and not potential_events.issubset(context_events):
                        json.dump(
                            {
                                "url": chunk["url"],
                                "prompt": prompt,
                                "completion": completion,
                                "question": question,
                                "reason": "event_not_in_context",
                                "unknown_tokens": sorted(potential_events - context_events),
                            },
                            failure_fh,
                            ensure_ascii=False,
                        )
                        failure_fh.write("\n")
                        skipped_events += 1
                        continue

                # Terminology consistency: flag events mislabeled as commands
                mislabelled_event = False
                for event_token in potential_events:
                    token_lower = event_token.lower()
                    if f"{token_lower} command" in q_lower or f"{token_lower} command" in a_lower:
                        mislabelled_event = True
                        break
                if mislabelled_event:
                    json.dump(
                        {
                            "url": chunk["url"],
                            "prompt": prompt,
                            "completion": completion,
                            "question": question,
                            "reason": "event_labeled_as_command",
                        },
                        failure_fh,
                        ensure_ascii=False,
                    )
                    failure_fh.write("\n")
                    skipped_events += 1
                    continue

                if question in seen_questions:
                    skipped_duplicates += 1
                    continue
                seen_questions.add(question)
                filtered.append(
                    {
                        "question": question,
                        "answer": answer,
                        "reference": reference,
                        "context": context_text,
                        "source_url": chunk["url"],
                    }
                )
            if not filtered:
                skipped_chunks += 1
                continue
            for entry in filtered:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1
                if args.max_records and written >= args.max_records:
                    LOGGER.info("Generated %s records (target=%s)", written, args.max_records)
                    return
    if args.max_records:
        LOGGER.info("Generated %s records (target=%s)", written, args.max_records)
    else:
        LOGGER.info("Generated %s records", written)
    if skipped_chunks:
        LOGGER.warning("Generator skipped %s chunk(s) because no valid QA pairs were parsed", skipped_chunks)
    if skipped_commands:
        LOGGER.warning("Skipped %s QA pair(s) due to command/context mismatch", skipped_commands)
    if skipped_events:
        LOGGER.warning("Skipped %s QA pair(s) due to event validation", skipped_events)
    if skipped_duplicates:
        LOGGER.info("Skipped %s duplicate question(s)", skipped_duplicates)


def main() -> None:
    args = tyro.cli(GenerateArgs)
    run_generate(args)


if __name__ == "__main__":
    main()
