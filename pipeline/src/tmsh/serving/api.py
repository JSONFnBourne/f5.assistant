"""FastAPI application for serving the fine-tuned tmsh model."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

app = FastAPI(title="tmsh Assistant")


class GenerationRequest(BaseModel):
    prompt: str
    max_new_tokens: int = 512


class GenerationResponse(BaseModel):
    prompt: str
    completion: str


@lru_cache(maxsize=1)
def load_model(model_path: str | None = None):
    model_path = model_path or str(Path("./artifacts/model").resolve())
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(model_path, device_map="auto")
    return tokenizer, model


@app.post("/generate", response_model=GenerationResponse)
async def generate(request: GenerationRequest) -> GenerationResponse:
    tokenizer, model = load_model()
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt must not be empty")
    inputs = tokenizer(request.prompt, return_tensors="pt").to(model.device)
    outputs = model.generate(**inputs, max_new_tokens=request.max_new_tokens)
    completion = tokenizer.decode(outputs[0], skip_special_tokens=True)
    return GenerationResponse(prompt=request.prompt, completion=completion)
