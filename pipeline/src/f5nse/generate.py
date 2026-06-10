from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import torch
import tyro
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

LOGGER = logging.getLogger("f5nse.generate")


def load_chunks(path: Path) -> list[dict]:
    chunks: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            chunks.append(json.loads(line))
    return chunks


def extract_json_block(text: str) -> list[dict]:
    candidates = []
    if "```" in text:
        parts = text.split("```")
        for i in range(len(parts)):
            if parts[i].strip().lower() in {"json", "jsonl"} and i + 1 < len(parts):
                snippet = parts[i + 1]
                try:
                    candidates = json.loads(snippet)
                    return candidates
                except json.JSONDecodeError:
                    continue
    # fallback: attempt to find first JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            candidates = json.loads(snippet)
        except json.JSONDecodeError:
            LOGGER.warning("Failed to parse JSON snippet")
    return candidates


def build_prompt(context: str, pairs: int) -> str:
    return (
        "You are f5nse, an expert on F5 BIG-IP platforms. "
        "Given the context below, produce tightly scoped question and answer pairs. "
        "Questions must rely on the provided context and answers must quote or paraphrase it faithfully. "
        "Respond ONLY with a JSON array; each item must contain keys 'question', 'answer', and 'reference' "
        "where 'reference' is a short citation pointing to the relevant section title or heading. "
        "Do not include prose outside the JSON array.\n\n"
        f"Produce exactly {pairs} items.\n\n"
        "Context:\n"
        "-----\n"
        f"{context}\n"
        "-----"
    )


@dataclass
class GenerateArgs:
    chunks_path: Path = Path("data/chunks/chunks.jsonl")
    output_path: Path = Path("data/datasets/qa_raw.jsonl")
    generator_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    load_in_8bit: bool = True
    device: str = "cuda"
    pairs_per_chunk: int = 2
    max_records: int = 250
    temperature: float = 0.2
    top_p: float = 0.9
    max_new_tokens: int = 512
    seed: int = 42
    trust_remote_code: bool = False
    overwrite: bool = False
    skip_if_exists: bool = False


def setup_generator(args: GenerateArgs):
    kwargs = {}
    if args.load_in_8bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        kwargs["device_map"] = "auto"
    elif args.device == "cuda":
        kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(
        args.generator_model, trust_remote_code=args.trust_remote_code, **kwargs
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.generator_model, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    model.eval()
    return model, tokenizer


def generate_completion(
    model,
    tokenizer,
    prompt: str,
    args: GenerateArgs,
) -> str:
    target_device = (
        "cuda" if torch.cuda.is_available() and args.device.startswith("cuda") else "cpu"
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    input_len = inputs["input_ids"].shape[1]
    inputs = {k: v.to(target_device) for k, v in inputs.items()}
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            do_sample=True,
            pad_token_id=tokenizer.pad_token_id,
        )
    generated_tokens = output[0]
    if generated_tokens.size(0) > input_len:
        generated_tokens = generated_tokens[input_len:]
    return tokenizer.decode(generated_tokens, skip_special_tokens=True)


def run_generate(args: GenerateArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
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
    if args.load_in_8bit and not (torch.cuda.is_available() and args.device.startswith("cuda")):
        raise RuntimeError(
            "8-bit generation requires a CUDA-capable device. Disable --load-in-8bit or set --device cuda."
        )
    model, tokenizer = setup_generator(args)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    written = 0
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with args.output_path.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            prompt = build_prompt(chunk["chunk"], args.pairs_per_chunk)
            completion = generate_completion(model, tokenizer, prompt, args)
            qa_items = extract_json_block(completion)
            if not isinstance(qa_items, list):
                LOGGER.warning("Generator returned non-list response; skipping chunk")
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
                filtered.append(
                    {
                        "question": question.strip(),
                        "answer": answer.strip(),
                        "reference": reference.strip(),
                        "context": chunk["chunk"],
                        "source_url": chunk["url"],
                    }
                )
            if not filtered:
                continue
            for entry in filtered:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1
                if written >= args.max_records:
                    LOGGER.info("Generated %s records (target=%s)", written, args.max_records)
                    return
    LOGGER.info("Generated %s records (target=%s)", written, args.max_records)


def main() -> None:
    args = tyro.cli(GenerateArgs)
    run_generate(args)


if __name__ == "__main__":
    main()
