import json
from pathlib import Path
from rich.console import Console

def load_configs():
    """Loads models and providers configuration from JSON files."""
    console = Console()
    parent = Path(__file__).resolve().parent
    models_config = {}
    providers_config = {}
    try:
        with open(parent / "models.json", "r") as f:
            models_config = json.load(f)
        with open(parent / "providers.json", "r") as f:
            providers_config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        console.print(f"[bold red]Error loading config: {e}[/bold red]")
    return models_config, providers_config
