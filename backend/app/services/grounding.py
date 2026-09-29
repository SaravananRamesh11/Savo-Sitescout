"""Stops the LLM from inventing numbers.

The LLM receives a `facts` dict of verified values. After it answers we extract every number from
its text and require each one to appear in the facts (in any sensible rounding). Anything else is
rejected; after one retry the caller uses the deterministic template instead.
"""
import re

_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)")
# words that make a small integer a harmless ordinal ("top 3", "first 2 hotspots", "#1")
_ORDINAL = re.compile(r"(?:top|first|#|rank|hotspot|no\.)\s*$", re.I)


def _variants(v: float) -> set[str]:
    out = {f"{v:.0f}", f"{v:.1f}", f"{v:.2f}", f"{v:.1f}".rstrip("0").rstrip("."), f"{v:,.0f}".replace(",", "")}
    out.add(f"{v / 1000:.1f}")  # metres -> km
    out.add(f"{v / 1000:.2f}")
    out.add(f"{v / 1000:.0f}")
    return out


def _collect(node, acc: set[str]) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        acc.update(_variants(float(node)))
    elif isinstance(node, str):
        for m in _NUM.findall(node):
            acc.add(m.replace(",", ""))
    elif isinstance(node, dict):
        for k, v in node.items():
            _collect(k, acc)
            _collect(v, acc)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _collect(v, acc)


def allowed_numbers(facts: dict) -> set[str]:
    acc: set[str] = set()
    _collect(facts, acc)
    return acc


def find_ungrounded(text_parts: list[str], facts: dict) -> list[str]:
    allowed = allowed_numbers(facts)
    bad = []
    for text in text_parts:
        for m in _NUM.finditer(text):
            tok = m.group(1).replace(",", "")
            if tok in allowed:
                continue
            try:
                f = float(tok)
                if any(abs(f - float(a)) < 1e-9 for a in allowed if re.fullmatch(r"\d+(\.\d+)?", a)):
                    continue
            except ValueError:
                pass
            if tok.isdigit() and int(tok) <= 5 and _ORDINAL.search(text[max(0, m.start() - 12):m.start()]):
                continue
            bad.append(tok)
    return sorted(set(bad))
