"""User settings, stored per-user in %APPDATA%\\WoWZA (never in the shared files)."""
import copy
import json
import os
import shutil
import string
import sys
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "WoWZA"
OLD_APP_DIR = APP_DIR.with_name("ClaudeAdvisor")  # before the rename to WoWZA
SETTINGS_FILE = APP_DIR / "settings.json"
STATE_FILE = APP_DIR / "state.json"

FIELD_LABELS = {"api_key": "API key", "model": "Model", "base_url": "Server URL"}

PROVIDERS = {
    "claude_sub": {
        "label": "Claude subscription (via Claude Code)",
        "fields": ["model"],
        "defaults": {"model": "sonnet"},
        "models": ["sonnet", "opus", "haiku"],
        "url": "https://claude.com/product/claude-code",
        "instructions": (
            "Uses your own Claude Pro or Max plan. No API key needed.\n\n"
            "If you use the Claude desktop app's Code tab and are signed in there, just click "
            "Test connection. Otherwise:\n\n"
            "1. Install Claude Code: open PowerShell and run\n"
            "     irm https://claude.ai/install.ps1 | iex\n"
            "2. In the same window run:  claude auth login\n"
            "   and sign in with your Claude account.\n"
            "3. Click Test connection below.\n\n"
            "The free Claude plan does not include Claude Code. Questions count toward your plan's usage limits."
        ),
    },
    "claude_api": {
        "label": "Claude API (pay as you go)",
        "fields": ["api_key", "model"],
        "defaults": {"api_key": "", "model": "claude-opus-5-5"},
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"],
        "url": "https://console.anthropic.com/settings/keys",
        "instructions": (
            "Pay only for what you use, roughly 1-3 cents per question depending on the model "
            "(Haiku is cheapest, Opus is smartest).\n\n"
            "1. Sign in at console.anthropic.com (button below).\n"
            "2. Under Billing, add a few dollars of credit.\n"
            "3. Under API Keys, click Create Key and paste it above.\n\n"
            "Your key is saved only on this PC and is never put in the addon files."
        ),
    },
    "gemini": {
        "label": "Google Gemini (free tier)",
        "fields": ["api_key", "model"],
        "defaults": {"api_key": "", "model": "gemini-flash-latest"},
        "models": ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"],
        "url": "https://aistudio.google.com/apikey",
        "instructions": (
            "Free with daily limits. You only need a Google account.\n\n"
            "1. Open Google AI Studio (button below) and sign in.\n"
            "2. Click Create API key and paste it above.\n\n"
            "Note: on the free tier Google may use your questions to improve its products."
        ),
    },
    "ollama": {
        "label": "Local model (Ollama, free and private)",
        "fields": ["base_url", "model"],
        "defaults": {"base_url": "http://localhost:11434", "model": "llama3.1"},
        "models": ["llama3.1", "qwen2.5", "mistral", "gemma2"],
        "url": "https://ollama.com/download",
        "instructions": (
            "Runs entirely on your PC. Free and private, but needs a decent graphics card "
            "(8 GB+ VRAM recommended) and knows less about WoW than the cloud options.\n\n"
            "1. Install Ollama (button below).\n"
            "2. Open PowerShell and download a model, for example:\n"
            "     ollama pull llama3.1\n"
            "3. Keep Ollama running while you play."
        ),
    },
    "openai_compat": {
        "label": "Other provider (OpenAI-compatible)",
        "fields": ["base_url", "api_key", "model"],
        "defaults": {"base_url": "https://openrouter.ai/api/v1", "api_key": "", "model": ""},
        "models": [],
        "url": "",
        "instructions": (
            "For OpenRouter, Groq, LM Studio, or any service with an OpenAI-compatible API.\n\n"
            "1. Create an account and API key with that service.\n"
            "2. Enter its server URL (for example https://openrouter.ai/api/v1 or "
            "https://api.groq.com/openai/v1), your key, and a model name from its model list.\n\n"
            "Local servers such as LM Studio usually need no key."
        ),
    },
}

DEFAULT_PROVIDER = "claude_sub"


def migrate_app_dir():
    """Move settings, history and quest data over from the pre-rename folder, once."""
    if OLD_APP_DIR.is_dir() and not APP_DIR.exists():
        shutil.move(str(OLD_APP_DIR), str(APP_DIR))


def app_icon_path():
    """The companion's window/exe icon (next to app.py, or at the root of a PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "wowza.ico"


def addon_source_dir():
    """The bundled WoWZA addon folder (works from source and from a PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "WoWZA"


def is_wow_dir(path):
    if not path:
        return False
    p = Path(path)
    return (p / "Interface").is_dir() or any(p.glob("Wow*.exe"))


def find_wow_dirs():
    """Look for installed WoW game folders (e.g. ...\\World of Warcraft\\_classic_beta_)."""
    found = []
    for drive in string.ascii_uppercase[2:8]:
        for root in (r"World of Warcraft", r"Program Files (x86)\World of Warcraft",
                     r"Program Files\World of Warcraft", r"Games\World of Warcraft"):
            base = Path(f"{drive}:\\") / root
            if base.is_dir():
                found += [d for d in sorted(base.glob("_*_")) if is_wow_dir(d)]
    return found


def pick_wow_dir(candidates):
    for pref in ("forever", "_classic_beta_"):
        for d in candidates:
            if pref in d.name.lower():
                return d
    return candidates[0] if candidates else None


def defaults():
    wow = pick_wow_dir(find_wow_dirs())
    return {
        "wow_dir": str(wow) if wow else "",
        "provider": DEFAULT_PROVIDER,
        "providers": {pid: dict(meta["defaults"]) for pid, meta in PROVIDERS.items()},
        "always_on_top": False,
        "popup_on_answer": False,
        "play_sound": False,  # the game plays its own whisper sound when an answer arrives
    }


def load_settings():
    settings = defaults()
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        for key, value in saved.items():
            if key == "providers":
                for pid, cfg in value.items():
                    if pid in settings["providers"]:
                        settings["providers"][pid].update(cfg)
            else:
                settings[key] = value
    if settings["provider"] not in PROVIDERS:
        settings["provider"] = DEFAULT_PROVIDER
    return settings


def save_settings(settings):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def clone(settings):
    return copy.deepcopy(settings)
