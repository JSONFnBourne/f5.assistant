from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import tyro

LOGGER = logging.getLogger("irule.export")


@dataclass
class ExportArgs:
    model_dir: Path = Path("data/models/irule-merged")
    llama_cpp_path: Path = Path("~/src/llama.cpp")
    output_path: Path = Path("data/models/irule.gguf")
    quantization: str | None = "q4_k_m"
    vocab_only: bool = False
    python_executable: str = "python"


def run_export(args: ExportArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    llama_cpp_script = args.llama_cpp_path.expanduser() / "convert-hf-to-gguf.py"
    if not llama_cpp_script.exists():
        raise FileNotFoundError(f"convert script not found at {llama_cpp_script}")
    cmd = [
        args.python_executable,
        str(llama_cpp_script),
        "--model",
        str(args.model_dir),
        "--outfile",
        str(args.output_path),
    ]
    if args.quantization:
        cmd.extend(["--quantize", args.quantization])
    if args.vocab_only:
        cmd.append("--vocab-only")
    LOGGER.info("Running GGUF export: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    LOGGER.info("GGUF model written to %s", args.output_path)


def main() -> None:
    args = tyro.cli(ExportArgs)
    run_export(args)


if __name__ == "__main__":
    main()
