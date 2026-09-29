"""Switch the local .env from Anthropic to OpenAI settings IN PLACE, without printing any secret.

    python tools/update_env.py

ANTHROPIC_API_KEY -> OPENAI_API_KEY (value kept), LLM_MODEL/ANTHROPIC_MODEL with a claude model ->
OPENAI_MODEL=gpt-5.4-mini, any other ANTHROPIC_* name -> OPENAI_*. Adds OPENAI_MODEL,
LLM_TIMEOUT_SECONDS and LLM_MAX_OUTPUT_TOKENS if missing. Prints variable NAMES only.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".env")
DEFAULTS = {"OPENAI_MODEL": "gpt-5.4-mini", "LLM_TIMEOUT_SECONDS": "8", "LLM_MAX_OUTPUT_TOKENS": "400"}


def main() -> int:
    lines = open(PATH, encoding="utf-8").read().splitlines() if os.path.exists(PATH) else []
    out, names, changes = [], set(), []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(line.replace("Anthropic", "OpenAI").replace("ANTHROPIC", "OPENAI")); continue
        k, v = s.split("=", 1); k, v = k.strip(), v.strip()
        nk = k
        if k in ("LLM_MODEL", "ANTHROPIC_MODEL") or (k == "OPENAI_MODEL" and "claude" in v.lower()):
            nk = "OPENAI_MODEL"
            if "claude" in v.lower() or not v:
                v = DEFAULTS["OPENAI_MODEL"]
        elif "ANTHROPIC" in k:
            nk = k.replace("ANTHROPIC", "OPENAI")
        if nk != k:
            changes.append(f"{k} -> {nk}")
        if nk in names:
            changes.append(f"dropped duplicate {nk}"); continue
        names.add(nk)
        out.append(f"{nk}={v}")
    for k, v in DEFAULTS.items():
        if k not in names:
            out.append(f"{k}={v}"); changes.append(f"added {k}")
    if "OPENAI_API_KEY" not in names:
        out.append("OPENAI_API_KEY="); changes.append("added empty OPENAI_API_KEY (fill it in)")
    open(PATH, "w", encoding="utf-8").write("\n".join(out) + "\n")
    key = next((l.split("=", 1)[1].strip() for l in out if l.startswith("OPENAI_API_KEY=")), "")
    kind = ("missing" if not key else "looks like an Anthropic key (sk-ant-...) - replace it with an OpenAI key"
            if key.startswith("sk-ant-") else "looks like an OpenAI key" if key.startswith("sk-") else "unrecognised format")
    print("updated .env:", "; ".join(changes) or "no changes needed")
    print("variables now:", ", ".join(sorted(names | set(DEFAULTS) | {"OPENAI_API_KEY"})))
    print("OPENAI_API_KEY:", kind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
