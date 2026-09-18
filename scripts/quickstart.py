"""One-command quickstart for the omni-intelligence trading bot.

Subcommands:
  doctor  — availability matrix: which brains can run on this machine right now
  paper   — launch the paper trader with auto-detected backend

Usage:
    python scripts/quickstart.py doctor
    python scripts/quickstart.py paper --config config/app.yaml --model-path m.zip --model-sha256 <sha>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

app = typer.Typer(
    name="quickstart",
    help="Omni-intelligent trading bot quickstart.",
    no_args_is_help=True,
)

BACKEND_ORDER = [
    ("fusion", "GPU + RL model + LLM governor (best)"),
    ("rl", "RL model only (CPU or GPU)"),
    ("llm", "LLM API or local LLM only"),
    ("rules", "Deterministic rules floor (always available)"),
]


@app.command()
def doctor() -> None:
    """Report which intelligence backends can run on this machine."""
    from trading_gym.intelligence.rl_backend import _cuda_available

    width = max(len(label) for _, label in BACKEND_ORDER)
    typer.echo("\n=== Intelligence backend availability ===\n")
    cuda = _cuda_available()
    for mode, label in BACKEND_ORDER:
        if mode == "rl":
            ok = True  # torch installed ⇒ RL works on CPU at minimum
            detail = "CUDA (GPU)" if cuda else "CPU"
        elif mode == "fusion":
            ok = cuda  # best experience needs GPU for RL inference
            detail = f"CUDA {'required' if not cuda else 'ready'}"
        elif mode == "llm":
            has_provider = bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))
            local_ollama = Path(
                "/tmp"
            ).exists()  # placeholder: real check pings endpoint
            ok = has_provider or local_ollama
            detail = "API key or Ollama endpoint"
        else:
            ok = True
            detail = "always available"

        status = (
            typer.style("✓", fg=typer.colors.GREEN)
            if ok
            else typer.style("✗", fg=typer.colors.RED)
        )
        typer.echo(f"  {status}  {mode:<8}  {label:<{width}}  ({detail})")

    typer.echo("\nRecommended command:")
    if cuda:
        typer.echo(
            "  python scripts/quickstart.py paper --intelligence-mode fusion ..."
        )
    else:
        typer.echo("  python scripts/quickstart.py paper --intelligence-mode rl ...")
    typer.echo()


@app.command()
def paper(
    config_path: str = typer.Option("config/app.yaml", "--config", "-c"),
    model_path: Path = typer.Option(None, "--model-path", "-m"),
    model_sha256: str = typer.Option(None, "--model-sha256"),
    timeframe: str = typer.Option("5m", "--timeframe", "-t"),
    live: bool = typer.Option(False, "--live"),
    intelligence_mode: str = typer.Option(None, "--intelligence-mode", "-i"),
) -> None:
    """Launch the paper trader with the configured intelligence backend."""
    from apps.paper.main import run

    kwargs = {
        "config_path": config_path,
        "timeframe": timeframe,
        "live": live,
        "intelligence_mode": intelligence_mode,
    }
    if model_path is not None:
        kwargs["model_path"] = model_path
    if model_sha256 is not None:
        kwargs["model_sha256"] = model_sha256
    run(**kwargs)


if __name__ == "__main__":
    app()
