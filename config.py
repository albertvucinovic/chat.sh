import json
from pathlib import Path
from rich.console import Console

TEMPLATE_GUIDE = r'''
Your models.json can be organized by provider like this:
{
  "default_model": "OpenAI GPT-4o",
  "providers": {
    "openai": {
      "api_base": "https://api.openai.com/v1/chat/completions",
      "api_key_env": "OPENAI_API_KEY",
      "models": {
        "OpenAI GPT-4o": {"model_name": "gpt-4o-mini", "alias": ["g4o-mini"]},
        "OpenAI o3": {"model_name": "o3-mini", "reasoning": true}
      }
    },
    "anthropic": {
      "api_base": "https://api.anthropic.com/v1/messages",
      "api_key_env": "ANTHROPIC_API_KEY",
      "models": {
        "Claude 3.5 Sonnet": {"model_name": "claude-3-5-sonnet-20240620", "alias": ["c35s"]}
      }
    }
  }
}
Tips:
- Use /model to list models grouped by provider.
- Select using full name, provider:name, or any alias.
- default_model sets the starting model.
'''


def load_configs():
    """Load configuration from a single models.json file organized by provider.

    New format (preferred):
    {
      "default_model": "OpenAI GPT-4o",
      "providers": {
        "openai": {
          "api_base": "https://api.openai.com/v1/chat/completions",
          "api_key_env": "OPENAI_API_KEY",
          "models": {
            "OpenAI GPT-4o": {"model_name": "gpt-4o-mini", "alias": ["g4o-mini"]},
            "OpenAI o3": {"model_name": "o3-mini", "reasoning": true}
          }
        }
      }
    }

    Backward compatible behavior: If models.json does not contain the new
    structure, we fall back to old two-file layout (models.json + providers.json).

    Returns (models_config, providers_config)
    - models_config: flat mapping of display_name -> {provider, model_name, alias(list), ...}
    - providers_config: mapping provider -> {api_base, api_key_env}; plus optional _meta: {default_model}
    """
    console = Console()
    parent = Path(__file__).resolve().parent
    models_config = {}
    providers_config = {}

    try:
        with open(parent / "models.json", "r", encoding="utf-8") as f:
            models_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        console.print(f"[bold red]Error loading models.json: {e}[/bold red]")
        console.print("[bold cyan]Create or fix models.json using this template:[/bold cyan]\n" + TEMPLATE_GUIDE)
        return models_config, providers_config

    # Detect new format
    if isinstance(models_data, dict) and "providers" in models_data and isinstance(models_data["providers"], dict):
        # Parse providers
        providers = models_data.get("providers", {})
        default_model = models_data.get("default_model")
        for prov_name, prov_obj in providers.items():
            if not isinstance(prov_obj, dict):
                continue
            api_base = prov_obj.get("api_base", "")
            api_key_env = prov_obj.get("api_key_env", "")
            providers_config[prov_name] = {"api_base": api_base, "api_key_env": api_key_env}
            models_map = prov_obj.get("models", {})
            if isinstance(models_map, dict):
                for display_name, m in models_map.items():
                    entry = {"provider": prov_name}
                    if isinstance(m, str):
                        entry["model_name"] = m
                        entry["alias"] = []
                    elif isinstance(m, dict):
                        entry.update(m)
                        # Ensure alias is a list
                        alias = entry.get("alias", [])
                        if isinstance(alias, str):
                            alias = [alias]
                        elif not isinstance(alias, list):
                            alias = []
                        entry["alias"] = alias
                    else:
                        continue
                    models_config[display_name] = entry
        if default_model:
            providers_config.setdefault("_meta", {})["default_model"] = default_model
        return models_config, providers_config

    # Backward compatibility: old format
    # - models.json is a flat mapping {display_name: {provider, model_name}}
    # - providers.json contains provider settings
    try:
        with open(parent / "providers.json", "r", encoding="utf-8") as f:
            providers_config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        # If providers.json missing in old format, error out gracefully
        if models_data:
            console.print("[bold yellow]Warning: providers.json not found or invalid.\n"
                          "If you are migrating to the new single-file format, add a 'providers' section to models.json.\n"
                          "Falling back to partial configuration.[/bold yellow]")
        providers_config = {}

    # Copy as-is for models; normalize alias field
    if isinstance(models_data, dict):
        for display_name, m in models_data.items():
            if not isinstance(m, dict):
                continue
            entry = dict(m)
            alias = entry.get("alias", [])
            if isinstance(alias, str):
                alias = [alias]
            elif not isinstance(alias, list):
                alias = []
            entry["alias"] = alias
            models_config[display_name] = entry
    return models_config, providers_config
