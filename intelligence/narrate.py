"""Generative-AI layer (brief §7 "Generative AI"), OpenAI-backed, narration only (ADR-005).

  * explain_recommendation — human-readable decision explanation of a finished recommendation
  * explain_incident       — incident explanation from the signals/risks of the current tick
  * summarize_state        — supply-chain state summary (shift briefing)
  * investigate            — operator investigation assistant: answers questions from current state

Guardrails: the LLM never decides anything; it only rewrites facts it is given. Every number in
its text must match a number in the facts, otherwise the deterministic template is used. Timeouts,
missing key, API errors → template, never an exception. All text is marked as simulated.

Config (env or repo-root .env): OPENAI_API_KEY, OPENAI_MODEL (default gpt-5.4-mini),
LLM_TIMEOUT_SECONDS (default 8), LLM_MAX_OUTPUT_TOKENS (default 400).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_TAG = "[Simulated environment]"
DEFAULT_MODEL = "gpt-5.4-mini"


def load_env(path: str = os.path.join(ROOT, ".env")) -> None:
    """Minimal .env loader (no extra dependency). Existing environment variables win."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ----------------------------------------------------------------------------- LLM clients
class LLMUnavailable(RuntimeError):
    pass


class OpenAIClient:
    def __init__(self, api_key: str, model: str, timeout: float, max_tokens: int):
        from openai import OpenAI  # imported lazily: the rest of the system works without the package
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=1)
        self.model, self.max_tokens = model, max_tokens

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        kw = {"response_format": {"type": "json_object"}} if json_mode else {}
        r = self.client.chat.completions.create(
            model=self.model, max_completion_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)
        text = (r.choices[0].message.content or "").strip()
        if not text:
            raise LLMUnavailable("empty completion")
        return text


def client_from_env():
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None, "OPENAI_API_KEY not set"
    try:
        return OpenAIClient(key, os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                            float(os.environ.get("LLM_TIMEOUT_SECONDS", "8")),
                            int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "400"))), None
    except Exception as ex:  # package missing etc.
        return None, f"OpenAI client unavailable: {ex}"


# ------------------------------------------------------------------------ number guardrail
_NUM = re.compile(r"(?<![A-Za-z_])-?\d[\d,]*(?:\.\d+)?")


def _numbers_in(obj) -> set[float]:
    out: set[float] = set()
    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            v = float(x)
            out.update({v, round(v), round(v, 1), round(v, 2), abs(v)})
            if 0 <= v <= 1:
                out.update({round(v * 100), round(v * 100, 1)})          # 0.72 -> 72 %
            out.update({round(v / 1000, 1), round(v / 1000)})           # 12,000 -> 12k
        elif isinstance(x, str):
            for m in _NUM.findall(x):
                walk(float(m.replace(",", "")))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k); walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(obj)
    return out


def unverified_numbers(text: str, facts) -> list[str]:
    """Numbers in `text` that do not appear (within rounding) in `facts`. Small counts ≤ 10 allowed."""
    allowed = _numbers_in(facts)
    bad = []
    for m in _NUM.findall(text):
        v = float(m.replace(",", ""))
        if abs(v) <= 10 and v == int(v):
            continue
        if not any(abs(v - a) <= max(0.51, 0.01 * abs(a)) for a in allowed):
            bad.append(m)
    return bad


# ------------------------------------------------------------------------------ prompts
SYSTEM = (
    "You are the explanation layer of a fuel-supply decision-support system that runs on a SIMULATED "
    "network in Bangladesh (not real infrastructure). You never make or change decisions: you only "
    "explain the facts you are given, for a busy control-room operator. Rules: use ONLY numbers that "
    "appear in the facts (you may round them); never invent quantities, times, probabilities, "
    "stations or causes; if something is unknown, say so; plain English, no markdown headings; "
    "hours are simulated hours."
)

TASKS = {
    "recommendation": "Explain this allocation recommendation in 2-3 sentences: why the station is at risk, "
                      "what is recommended, what it changes (before -> after), and whether a human must review "
                      "it: review is required ONLY if review == HUMAN_REVIEW (then give review_reasons); if "
                      "AUTO_ELIGIBLE say it can be approved normally and mention confidence_notes only as caveats. Mention the main alternative in one short clause.",
    "incident": "Write an incident explanation in at most 5 sentences: what happened (the signals), which "
                "stations/fuels are affected and how badly, what the system recommends, and what to watch next.",
    "state": "Write a shift-handover summary in at most 6 short sentences: overall network status per fuel, the "
             "top risks, active disruptions/events, pending recommendations, and any bottleneck. State how many "
             "recommendations need human review = count of review == HUMAN_REVIEW (if 0, say none need review).",
    "question": "Answer the operator's question using only the facts. Cite station/depot/route ids you rely on. "
                "If the facts cannot answer it, say what is missing. You cannot take actions; if asked to, "
                "explain which recommendation or approval would do it. Max 6 sentences.",
}


# ---------------------------------------------------------------------------- compaction
def _compact_rec(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "station_id", "fuel_type", "action", "impact", "binding_constraints",
                                  "confidence", "review", "review_reasons", "confidence_notes", "policy")} | {
        "alternatives": [{k: a.get(k) for k in ("route_id", "source_depot_id", "quantity", "unmet_after_l", "why_not")}
                         for a in r.get("alternatives", [])[:3]]}


def build_context(out: dict, max_risks: int = 8, max_signals: int = 15) -> dict:
    seen, sig = set(), []
    rank = {"high": 0, "medium": 1, "low": 2}
    for s in sorted(out.get("signals", []), key=lambda s: rank.get(s.get("severity"), 3)):
        key = (s.get("type"), s.get("entity_id"))
        if key in seen or s.get("type") == "event_scheduled" and len(sig) > max_signals // 2:
            continue
        seen.add(key)
        sig.append({k: s.get(k) for k in ("type", "entity_id", "severity", "direction", "since_tick", "evidence")
                    if s.get(k) is not None})
    return {
        "tick": out.get("tick"), "policy": out.get("policy"), "degraded": out.get("degraded"),
        "network": out.get("network"), "projected": out.get("projected"),
        "top_risks": [r for r in out.get("risks", []) if r.get("risk") != "low"][:max_risks]
                     or out.get("risks", [])[:3],
        "signals": sig[:max_signals],
        "recommendations": [_compact_rec(r) for r in out.get("recommendations", [])[:6]],
        "bottlenecks": out.get("bottlenecks"),
        "supply_outlook": (out.get("supply_outlook") or [])[:5],
    }


# -------------------------------------------------------------------------------- narrator
@dataclass
class Narrator:
    client: object | None = None
    disabled_reason: str | None = None
    stats: dict = field(default_factory=lambda: {"llm_calls": 0, "llm_ok": 0, "llm_failed": 0,
                                                 "fallback_template": 0, "rejected_unverified": 0,
                                                 "latency_ms": []})
    cache: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Narrator":
        c, why = client_from_env()
        return cls(client=c, disabled_reason=why)

    # core call with guardrails
    def _run(self, task: str, facts: dict, template: str, cache_key: str | None = None,
             question: str | None = None) -> dict:
        if cache_key and cache_key in self.cache:
            return self.cache[cache_key]
        res = {"text": template, "source": "template", "reason": self.disabled_reason}
        if self.client is not None:
            user = f"TASK: {TASKS[task]}\n" + (f"QUESTION: {question}\n" if question else "") + \
                   "FACTS (JSON):\n" + json.dumps(facts, default=str, separators=(",", ":"))
            self.stats["llm_calls"] += 1
            t0 = time.perf_counter()
            try:
                text = self.client.complete(SYSTEM, user)
                self.stats["latency_ms"].append(round((time.perf_counter() - t0) * 1000, 1))
                bad = unverified_numbers(text, facts)
                if bad:
                    self.stats["rejected_unverified"] += 1
                    res["reason"] = f"LLM text rejected: numbers not in facts {bad[:5]}"
                else:
                    self.stats["llm_ok"] += 1
                    if SIM_TAG not in text:
                        text = f"{text} {SIM_TAG}"
                    res = {"text": text, "source": "llm", "reason": None}
            except Exception as ex:
                self.stats["llm_failed"] += 1
                res["reason"] = f"LLM error: {type(ex).__name__}: {str(ex)[:160]}"
        if res["source"] == "template":
            self.stats["fallback_template"] += 1
        if cache_key:
            self.cache[cache_key] = res
        return res

    # ---- 1. decision explanation
    def explain_recommendation(self, rec: dict) -> dict:
        facts = _compact_rec(rec)
        key = "rec:" + json.dumps([rec.get("id"), rec.get("action"), rec.get("review")], default=str)
        return self._run("recommendation", facts, rec.get("explanation", ""), cache_key=key)

    # ---- 2. incident explanation
    def explain_incident(self, out: dict) -> dict:
        ctx = build_context(out)
        incident_types = {"route_disrupted", "station_outage", "depot_constrained", "depot_closed", "supply_delayed",
                          "supply_shortfall", "demand_anomaly", "demand_anomaly_active", "event_active",
                          "inventory_anomaly", "allocation_at_risk", "systemic_shortage", "data_stale"}
        sig = [s for s in ctx["signals"] if s["type"] in incident_types]
        if not sig:
            return {"text": f"No active incident at tick {out.get('tick')}. {SIM_TAG}", "source": "template",
                    "reason": "no incident signals"}
        facts = {**ctx, "signals": sig}
        return self._run("incident", facts, template_incident(out, sig),
                         cache_key="inc:" + json.dumps(sorted({(s["type"], s["entity_id"]) for s in sig})))

    # ---- 3. state summary
    def summarize_state(self, out: dict) -> dict:
        ctx = build_context(out)
        return self._run("state", ctx, template_state(out), cache_key=f"state:{out.get('tick')}")

    # ---- 4. investigation assistant
    def investigate(self, question: str, out: dict) -> dict:
        ctx = build_context(out, max_risks=12, max_signals=25)
        ctx["all_risks"] = out.get("risks", [])
        return self._run("question", ctx, template_answer(question, out), question=question[:500])

    def enrich(self, out: dict, top_n: int = 3) -> dict:
        """Add LLM (or template) texts to an assessment output, in place."""
        for r in out.get("recommendations", [])[:top_n]:
            n = self.explain_recommendation(r)
            r["explanation_text"], r["explanation_source"] = n["text"], n["source"]
        out["incident_summary"] = self.explain_incident(out)
        out["state_summary"] = self.summarize_state(out)
        out["genai_stats"] = self.metrics()
        return out

    def metrics(self) -> dict:
        lat = sorted(self.stats["latency_ms"])
        return {**{k: v for k, v in self.stats.items() if k != "latency_ms"},
                "latency_p50_ms": lat[len(lat) // 2] if lat else None,
                "latency_p95_ms": lat[int(len(lat) * 0.95)] if lat else None,
                "enabled": self.client is not None, "disabled_reason": self.disabled_reason}


# ------------------------------------------------------------------------------ templates
def template_incident(out: dict, sig: list[dict]) -> str:
    rank = {"high": 0, "medium": 1, "low": 2}
    groups: dict = {}
    for s in sorted(sig, key=lambda s: rank.get(s.get("severity"), 3)):
        ev = s.get("evidence") or {}
        what = s["type"].replace("_", " ") + (f" ({ev['event_type']})" if ev.get("event_type") else "")
        prm = ev.get("parameters") or {}
        target = ", ".join(prm.get("region_ids") or prm.get("station_ids") or prm.get("route_ids")
                           or prm.get("depot_ids") or []) or ("network-wide" if "event_type" in ev else "")
        ent = target or ev.get("depot_id") or s["entity_id"]
        groups.setdefault(what, [])
        if ent not in groups[what]:
            groups[what].append(ent)
    parts = [f"{what}: {', '.join(ents[:4])}" + (f" (+{len(ents) - 4} more)" if len(ents) > 4 else "")
             for what, ents in list(groups.items())[:6]]
    high = [r for r in out.get("risks", []) if r.get("risk") == "high"][:3]
    risk_txt = "; ".join(f"{r['station_id']} {r['fuel_type'].lower()} stockout in "
                         f"{r['stockout_in_hours_p50']} h" for r in high) or "no station at high risk"
    n_rec = len(out.get("recommendations", []))
    return (f"Tick {out.get('tick')}: {len(sig)} active issue(s) — " + "; ".join(parts) +
            f". Highest risks: {risk_txt}. {n_rec} recommendation(s) generated. {SIM_TAG}")


def template_state(out: dict) -> str:
    net = out.get("network", {})
    fuels = "; ".join(f"{f.lower()} {'SHORTAGE' if v.get('systemic_shortage') else 'covered'} "
                      f"({v.get('stock_l'):,.0f} L vs {v.get('demand_next_24h_l'):,.0f} L demand next 24 h)" for f, v in net.items())
    high = [r for r in out.get("risks", []) if r.get("risk") == "high"]
    ev = sorted({(s.get("evidence") or {}).get("event_type") for s in out.get("signals", [])
                 if s["type"] == "event_active"} - {None})
    recs = out.get("recommendations", [])
    review = sum(r.get("review") == "HUMAN_REVIEW" for r in recs)
    unad = (out.get("bottlenecks") or {}).get("at_risk_not_addressed", [])
    return (f"Tick {out.get('tick')} summary — network: {fuels}. {len(high)} station-fuel pair(s) at high risk"
            + (f" (worst: {high[0]['station_id']} {high[0]['fuel_type'].lower()})" if high else "") +
            f". Active events: {', '.join(ev) or 'none'}. {len(recs)} recommendation(s), {review} awaiting review."
            + (f" {len(unad)} at-risk pair(s) not yet addressed." if unad else "") + f" {SIM_TAG}")


def template_answer(question: str, out: dict) -> str:
    q = question.lower()
    hits = [r for r in out.get("risks", []) if r["station_id"].split("-")[-1] in q]
    if hits:
        body = "; ".join(f"{r['station_id']} {r['fuel_type'].lower()}: inventory {r['inventory']:,.0f} L, "
                         f"stockout in {r['stockout_in_hours_p50']} h, risk {r['risk']}" for r in hits)
        return f"(Assistant offline — facts only) {body}. {SIM_TAG}"
    return f"(Assistant offline) {template_state(out)}"
