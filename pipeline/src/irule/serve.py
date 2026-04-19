from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from peft import PeftModel, PeftConfig
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
import tyro

LOGGER = logging.getLogger("irule.serve")


class GenerateRequest(BaseModel):
    prompt: str
    max_new_tokens: int = 512
    temperature: float = 0.2
    top_p: float = 0.9


class GenerateResponse(BaseModel):
    response: str


@dataclass
class ServeArgs:
    model_path: Path = Path("data/models/irule-lora")
    base_model: str = "meta-llama/Llama-3.2-3B-Instruct"
    host: str = "127.0.0.1"
    port: int = 8000
    quantization: Literal["4bit", "8bit", "none"] = "4bit"
    max_gpu_memory: Optional[str] = "7GiB"
    max_cpu_memory: Optional[str] = "48GiB"
    trust_remote_code: bool = False


class IRuleServer:
    def __init__(self, args: ServeArgs) -> None:
        self.args = args
        self.model, self.tokenizer = self._load_model()

    def _load_model(self):
        kwargs = {"trust_remote_code": self.args.trust_remote_code}
        quant_mode = self.args.quantization
        if quant_mode == "4bit":
            if not torch.cuda.is_available():
                raise RuntimeError("4-bit inference requires a CUDA-capable device.")
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
            kwargs["device_map"] = "auto"
        elif quant_mode == "8bit":
            if not torch.cuda.is_available():
                raise RuntimeError("8-bit inference requires a CUDA-capable device.")
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
            kwargs["device_map"] = "auto"
        else:
            kwargs["torch_dtype"] = torch.float16 if torch.cuda.is_available() else torch.float32
            kwargs["device_map"] = "auto" if torch.cuda.is_available() else None
        if quant_mode in {"4bit", "8bit"} and (self.args.max_gpu_memory or self.args.max_cpu_memory):
            device_id = torch.cuda.current_device()
            max_memory = {}
            if self.args.max_gpu_memory:
                max_memory[device_id] = self.args.max_gpu_memory
            if self.args.max_cpu_memory:
                max_memory["cpu"] = self.args.max_cpu_memory
            kwargs["max_memory"] = max_memory
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


def create_app(server: IRuleServer) -> FastAPI:
    app = FastAPI(title="irule Inference")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.post("/generate", response_model=GenerateResponse)
    def generate(request: GenerateRequest) -> GenerateResponse:
        text = server.generate(request)
        return GenerateResponse(response=text)

    return app


def serve(args: ServeArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    server = IRuleServer(args)
    app = create_app(server)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def main() -> None:
    args = tyro.cli(ServeArgs)
    serve(args)


if __name__ == "__main__":
    main()
