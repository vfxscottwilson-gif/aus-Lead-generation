"""Model-agnostic AI client.

You only provide API keys (one secret per key: GEMINI_API_KEY_1, GROQ_API_KEY, ...; keys.py merges them). For every key this module:
  1. detects the provider (Gemini, Groq, OpenRouter, Mistral, OpenAI, Anthropic),
  2. asks that provider for its CURRENT model list,
  3. picks the newest usable chat models (cheap/free first),
  4. falls back to the next model / next provider automatically on any failure
     (rate limit, credit exhausted, outage, removed model, bad key).
No model name is ever hard-coded.
"""
import re
import requests

TIMEOUT = 45
SKIP = re.compile(
    r"whisper|guard|tts|audio|embed|moderation|transcri|speech|image|imagen|veo|orpheus|"
    r"playai|ocr|rerank|safety|robotics|aqa|learnlm|dall|sora|realtime|codex|search|computer|"
    r"davinci|babbage|instruct|vision-only", re.I)
BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}
# Cloudflare in front of Groq answers 403 "error code: 1010" to default library User-Agents: send our own.
UA = {"User-Agent": "lead-bot/3.0 (+https://github.com)"}
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC = "https://api.anthropic.com/v1"


def _hdr_anthropic(key):
    return {**UA, "x-api-key": key, "anthropic-version": "2023-06-01"}


def candidates(provider: str, key: str) -> list:
    """Return model ids for this key, best first."""
    if provider == "gemini":
        r = requests.get(f"{GEMINI}/models?pageSize=200", headers={**UA, "x-goog-api-key": key}, timeout=TIMEOUT)
        r.raise_for_status()
        rows = []
        for m in r.json().get("models", []):
            name = m["name"].split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in name or SKIP.search(name):
                continue
            nums = re.findall(r"\d+(?:\.\d+)?", name)
            rows.append((float(nums[0]) if nums else 0.0, "lite" not in name,
                         not re.search(r"preview|exp", name), name))
        rows.sort(reverse=True)
        return [r_[3] for r_ in rows]

    if provider == "anthropic":
        r = requests.get(f"{ANTHROPIC}/models?limit=100", headers=_hdr_anthropic(key), timeout=TIMEOUT)
        r.raise_for_status()
        rows = [(("opus" not in m["id"]), m.get("created_at", ""), m["id"]) for m in r.json().get("data", [])]
        rows.sort(reverse=True)
        return [x[2] for x in rows]

    r = requests.get(BASES[provider] + "/models", headers={**UA, "Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
    r.raise_for_status()
    rows = []
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        if not mid or SKIP.search(mid):
            continue
        if provider == "openai" and not re.match(r"(gpt|o\d)", mid):
            continue
        if provider == "openrouter":
            pr = m.get("pricing", {})
            if not (str(pr.get("prompt")) == "0" and str(pr.get("completion")) == "0"):
                continue  # free models only
            if "text" not in (m.get("architecture", {}).get("output_modalities") or ["text"]):
                continue
        if provider == "mistral" and not m.get("capabilities", {}).get("completion_chat", True):
            continue
        cheap = 1 if (provider == "openai" and "mini" in mid) else 0
        rows.append((cheap, m.get("created") or 0, mid))
    rows.sort(reverse=True)
    return [x[2] for x in rows]


def detect(key: str):
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("csk-"):
        return "cerebras"
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("sk-"):
        return "openai"
    for p in ("groq", "mistral", "gemini", "openrouter"):
        try:
            if candidates(p, key):
                return p
        except Exception:
            continue
    return None


class QuotaHit(RuntimeError):
    """Every AI key is rate-limited / out of quota / rejected: stop AI for the rest of the run."""


class AI:
    def __init__(self, raw_keys: str):
        self.pool = []  # [provider, key, [models], alive]
        self.notes = []
        keys = [k.strip() for k in re.split(r"[\n,; ]+", raw_keys or "") if k.strip()]
        for k in keys:
            p = detect(k)
            if not p:
                self.notes.append("One AI key was not recognised (check for typos).")
                continue
            try:
                models = candidates(p, k)
            except Exception as e:
                self.notes.append(f"{p}: could not list models ({str(e)[:80]}).")
                continue
            if models:
                self.pool.append([p, k, models, True])
            else:
                self.notes.append(f"{p}: key works but no usable chat models found.")
        if not self.pool:
            raise RuntimeError("No working AI key. " + " ".join(self.notes))

    def _call(self, provider, key, model, prompt, max_tokens, temp=0.9):
        if provider == "gemini":
            r = requests.post(
                f"{GEMINI}/models/{model}:generateContent", headers={**UA, "x-goog-api-key": key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": temp, "maxOutputTokens": max(max_tokens, 2048)}},
                timeout=TIMEOUT)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        elif provider == "anthropic":
            r = requests.post(f"{ANTHROPIC}/messages", headers=_hdr_anthropic(key),
                              json={"model": model, "max_tokens": max(max_tokens, 600), "temperature": temp,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=TIMEOUT)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        else:
            body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            if provider == "openai":  # newer OpenAI models reject max_tokens/temperature
                body["max_completion_tokens"] = max(max_tokens, 2000)
            else:
                body.update({"temperature": temp, "max_tokens": max(max_tokens, 600)})
            r = requests.post(BASES[provider] + "/chat/completions",
                              headers={**UA, "Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if not text:
            raise ValueError("empty reply")
        return text

    def generate(self, prompt: str, max_tokens: int = 300, temperature: float = 0.9) -> str:
        """ONE pass over the keys, no sleeping, no retry rounds. 429 / 401 / 402 / 403 switch that key off for the
        rest of the run and try the next key at once. Raises QuotaHit when every key is off (caller stops AI for
        the run), RuntimeError for other failures."""
        last = None
        for entry in self.pool:
            provider, key, models, alive = entry
            if not alive:
                continue
            for model in list(models[:3]):
                try:
                    text = self._call(provider, key, model, prompt, max_tokens, temperature)
                    models.remove(model)
                    models.insert(0, model)  # remember the one that works
                    return text
                except requests.HTTPError as e:
                    last = e
                    code = e.response.status_code if e.response is not None else 0
                    if code in (401, 403, 402, 429):
                        entry[3] = False
                        self.notes.append(f"{provider} key {'rate-limited/quota' if code == 429 else 'rejected'} "
                                          f"({code}): off for this run.")
                        break
                    if code == 404:                  # model removed: try the next model of this key
                        continue
                    break
                except Exception as e:
                    last = e
                    break
        if not any(e[3] for e in self.pool):
            raise QuotaHit("All AI keys rate-limited or rejected for this run.")
        raise RuntimeError(f"AI call failed: {str(last)[:120]}")
