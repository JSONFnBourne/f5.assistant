"""Generate synthetic QA pairs with the Judge LLM."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..processing.chunker import Chunk

DEFAULT_JUDGE_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
PROMPT_TEMPLATE = """You are an expert on F5 tmsh and iRule syntax. Using the provided documentation chunk produce concise QA pairs.\nChunk:\n{chunk}\n---\nRespond with JSON containing `question`, `answer`, `domain`, `name`, `module`, `syntax`, `description`, `options`, `examples`, and `see_also`."""


@dataclass
class QAPair:
    question: str
    answer: str
    domain: str
    name: str | None = None
    module: str | None = None
    syntax: str | None = None
    description: str | None = None
    options: list[str] | None = None
    examples: list[str] | None = None
    see_also: list[str] | None = None
    source: str | None = None

    def to_json(self) -> str:
        payload = {
            "question": self.question,
            "answer": self.answer,
            "domain": self.domain,
            "name": self.name,
            "module": self.module,
            "syntax": self.syntax,
            "description": self.description,
            "options": self.options,
            "examples": self.examples,
            "see_also": self.see_also,
            "source": self.source,
        }
        return json.dumps({k: v for k, v in payload.items() if v is not None})


class JudgeLLM:
    """Thin wrapper around the Judge model used for QA generation."""

    def __init__(self, model_name: str = DEFAULT_JUDGE_MODEL) -> None:
        self.model_name = model_name
        self._pipeline = None

    @property
    def pipeline(self):  # type: ignore[override]
        from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

        if self._pipeline is None:
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForCausalLM.from_pretrained(
                self.model_name,
                torch_dtype="auto",
                device_map="auto",
            )
            self._pipeline = pipeline(
                "text-generation",
                model=model,
                tokenizer=tokenizer,
                max_new_tokens=1024,
                do_sample=True,
                temperature=0.3,
            )
        return self._pipeline

    def generate(self, chunk: Chunk, domain: str) -> list[QAPair]:
        """Generate QA pairs for a single documentation chunk."""

        prompt = PROMPT_TEMPLATE.format(chunk=chunk.text)
        responses = self.pipeline(prompt)
        qa_pairs: list[QAPair] = []
        for response in responses:
            text = response["generated_text"][len(prompt) :].strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            qa_pairs.append(
                QAPair(
                    question=payload.get("question", ""),
                    answer=payload.get("answer", ""),
                    domain=payload.get("domain", domain),
                    name=payload.get("name"),
                    module=payload.get("module"),
                    syntax=payload.get("syntax"),
                    description=payload.get("description"),
                    options=payload.get("options"),
                    examples=payload.get("examples"),
                    see_also=payload.get("see_also"),
                    source=chunk.source,
                )
            )
        return qa_pairs


def generate_dataset(
    chunks: Iterable[Chunk],
    output_path: Path,
    *,
    judge: JudgeLLM | None = None,
    domain: str = "tmsh",
) -> list[QAPair]:
    """Generate QA pairs for ``chunks`` and persist them as JSONL."""

    judge = judge or JudgeLLM()
    qa_pairs: list[QAPair] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            for pair in judge.generate(chunk, domain=domain):
                qa_pairs.append(pair)
                handle.write(pair.to_json() + "\n")
    return qa_pairs
