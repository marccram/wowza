"""AI providers. Each one streams answer text for (system, messages).

messages: [{"role": "user" | "assistant", "content": str}, ...], ending with a user turn.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from settings import APP_DIR, PROVIDERS

TIMEOUT = 120


class ProviderError(Exception):
    pass


def stream_answer(settings, system, messages):
    pid = settings["provider"]
    cfg = settings["providers"][pid]
    return _STREAMERS[pid](cfg, system, messages)


def provider_label(settings):
    return PROVIDERS[settings["provider"]]["label"]


# --- Claude subscription via Claude Code (claude -p) ------------------------

def _find_claude():
    exe = shutil.which("claude")
    if exe:
        return exe
    appdata = Path(os.environ.get("APPDATA", ""))
    for p in (Path.home() / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd"):
        if p.exists():
            return str(p)
    # Copy bundled with the Claude desktop app (Code tab); keeps itself updated, so take the newest.
    bundled = sorted((appdata / "Claude" / "claude-code").glob("*/*/claude.exe"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if bundled:
        return str(bundled[0])
    raise ProviderError("Claude Code isn't installed. Open Settings for setup steps.")


def _flatten(messages):
    """Claude Code takes one prompt, so earlier turns are inlined as a transcript."""
    *earlier, current = messages
    if not earlier:
        return current["content"]
    lines = ["Earlier in this conversation:"]
    for m in earlier:
        who = "Player" if m["role"] == "user" else "You"
        lines.append(f"{who}: {m['content']}")
    lines += ["", "Current message:", current["content"]]
    return "\n".join(lines)


def _claude_sub(cfg, system, messages):
    # The login token is shared with any open Claude app/session; a concurrent refresh fails transiently.
    for attempt in range(3):
        try:
            yield from _claude_sub_once(cfg, system, messages)
            return
        except _TokenBusy:
            time.sleep(5 * (attempt + 1))
    raise ProviderError("Claude login is busy (another Claude app is refreshing it). Try again in a minute.")


class _TokenBusy(Exception):
    pass


def _claude_sub_once(cfg, system, messages):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        _find_claude(), "-p",
        "--model", cfg.get("model") or "sonnet",
        "--system-prompt", system,
        "--tools", "",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=APP_DIR, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    proc.stdin.write(_flatten(messages))
    proc.stdin.close()
    streamed = False
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "stream_event":
            delta = ev.get("event", {}).get("delta", {})
            if delta.get("type") == "text_delta":
                streamed = True
                yield delta.get("text", "")
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                msg = ev.get("result") or "Claude Code returned an error."
                if not streamed and "refresh" in msg.lower() and "token" in msg.lower():
                    raise _TokenBusy()
                raise ProviderError(msg)
            if not streamed and ev.get("result"):
                yield ev["result"]
                streamed = True
    proc.wait()
    if proc.returncode and not streamed:
        err = proc.stderr.read().strip()
        if "login" in err.lower() or "auth" in err.lower():
            raise ProviderError("Claude Code isn't signed in. Run 'claude auth login' in PowerShell.")
        raise ProviderError(err[-300:] or f"Claude Code exited with code {proc.returncode}.")


# --- Claude API (Anthropic SDK) ---------------------------------------------

def _claude_api(cfg, system, messages):
    import anthropic

    model = cfg.get("model") or "claude-opus-5-5"
    client = anthropic.Anthropic(api_key=cfg.get("api_key") or None)
    kwargs = dict(model=model, max_tokens=8000, system=system, messages=messages)
    if not model.startswith("claude-haiku"):
        # Short chat answers: low effort; reroute safety-classifier refusals automatically.
        kwargs.update(
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    try:
        with client.beta.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ProviderError("Your Claude API key was rejected. Check it in Settings.")
    except anthropic.PermissionDeniedError as e:
        raise ProviderError(f"Claude API refused the request: {e.message}")
    except anthropic.NotFoundError:
        raise ProviderError(f"Model '{model}' wasn't found. Pick another in Settings.")
    except anthropic.RateLimitError:
        raise ProviderError("Claude API rate limit hit. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        if "credit" in str(e.message).lower():
            raise ProviderError("Your Claude API account is out of credit (console.anthropic.com > Billing).")
        raise ProviderError(f"Claude API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise ProviderError("Couldn't reach the Claude API. Check your internet connection.")
    if final.stop_reason == "refusal":
        yield "\n(Claude declined to answer this one.)"


# --- Plain HTTP providers ---------------------------------------------------

def _http_lines(url, body, headers):
    req = Request(url, data=json.dumps(body).encode("utf-8"),
                  headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    yield line
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(detail)
            if isinstance(err, list):
                err = err[0]
            detail = err.get("error", {}).get("message") or err.get("error") or detail
        except (ValueError, AttributeError, IndexError):
            pass
        if e.code in (401, 403):
            raise ProviderError(f"The API key was rejected ({e.code}). Check it in Settings.")
        if e.code == 429:
            raise ProviderError("Rate limit or daily free quota reached. Try again later.")
        raise ProviderError(f"HTTP {e.code}: {str(detail)[:300]}")
    except URLError as e:
        raise ProviderError(f"Couldn't connect to {url.split('/')[2]}: {e.reason}")


def _sse_json(lines):
    for line in lines:
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except ValueError:
                continue


def _gemini(cfg, system, messages):
    key = cfg.get("api_key")
    if not key:
        raise ProviderError("Add your Gemini API key in Settings.")
    model = cfg.get("model") or "gemini-flash-latest"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ],
    }
    for ev in _sse_json(_http_lines(url, body, {"x-goog-api-key": key})):
        for cand in ev.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if part.get("text") and not part.get("thought"):
                    yield part["text"]


def _ollama(cfg, system, messages):
    base = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
    body = {
        "model": cfg.get("model") or "llama3.1",
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": True,
    }
    try:
        for line in _http_lines(f"{base}/api/chat", body, {}):
            ev = json.loads(line)
            if ev.get("error"):
                raise ProviderError(f"Ollama: {ev['error']}")
            text = ev.get("message", {}).get("content")
            if text:
                yield text
    except ProviderError as e:
        if "Couldn't connect" in str(e):
            raise ProviderError("Ollama isn't running. Start Ollama and try again.")
        raise


def _openai_compat(cfg, system, messages):
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base or not cfg.get("model"):
        raise ProviderError("Set the server URL and model name in Settings.")
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": True,
    }
    for ev in _sse_json(_http_lines(f"{base}/chat/completions", body, headers)):
        for choice in ev.get("choices", []):
            text = (choice.get("delta") or {}).get("content")
            if text:
                yield text


_STREAMERS = {
    "claude_sub": _claude_sub,
    "claude_api": _claude_api,
    "gemini": _gemini,
    "ollama": _ollama,
    "openai_compat": _openai_compat,
}
