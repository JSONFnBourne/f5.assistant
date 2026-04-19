from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from peft import PeftModel, PeftConfig
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
import tyro

LOGGER = logging.getLogger("f5nse.serve")


class GenerateRequest(BaseModel):
    prompt: str
    max_new_tokens: int = 512
    temperature: float = 0.2
    top_p: float = 0.9


class GenerateResponse(BaseModel):
    response: str


@dataclass
class ServeArgs:
    model_path: Path = Path("data/models/f5nse-lora")
    base_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    host: str = "0.0.0.0"
    port: int = 8000
    load_in_8bit: bool = True
    trust_remote_code: bool = False


class F5NSEServer:
    def __init__(self, args: ServeArgs) -> None:
        self.args = args
        self.model, self.tokenizer = self._load_model()

    def _load_model(self):
        kwargs = {"trust_remote_code": self.args.trust_remote_code}
        if self.args.load_in_8bit:
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
            kwargs["device_map"] = "auto"
        else:
            kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            kwargs["device_map"] = "auto" if torch.cuda.is_available() else None
        base_model = AutoModelForCausalLM.from_pretrained(self.args.base_model, **kwargs)
        tokenizer = AutoTokenizer.from_pretrained(
            self.args.base_model, trust_remote_code=self.args.trust_remote_code, use_fast=True
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        peft_config = PeftConfig.from_pretrained(self.args.model_path)
        model = PeftModel.from_pretrained(base_model, self.args.model_path)
        model.eval()
        return model, tokenizer

    def generate(self, request: GenerateRequest) -> str:
        prompt = request.prompt
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        outputs = self.model.generate(
            **inputs,
            max_new_tokens=request.max_new_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
        )
        return self.tokenizer.decode(outputs[0], skip_special_tokens=True)


def create_app(server: F5NSEServer) -> FastAPI:
    app = FastAPI(title="f5nse Inference")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/generate", response_model=GenerateResponse)
    def generate(request: GenerateRequest) -> GenerateResponse:
        text = server.generate(request)
        return GenerateResponse(response=text)

    return app


def serve(args: ServeArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    server = F5NSEServer(args)
    app = create_app(server)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def main() -> None:
    args = tyro.cli(ServeArgs)
    serve(args)


if __name__ == "__main__":
    main()
