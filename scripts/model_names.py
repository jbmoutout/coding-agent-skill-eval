#!/usr/bin/env python3
"""Canonical full model names for grilling cells.

Cell convention is `arch-001-grilling-<agent>-<sim>` (or with `-hardened`
suffix for the persona variant). Cell labels strip the prefix.

Use `resolve_cell(cell_label)` to get a {"agent": ..., "sim": ...,
"persona": ..., "human": ...} dict with explicit model names.
"""

# Single source of truth for short → full model names.
# Add new model slugs here when introducing new agents/SIMs.
MODEL_FULL_NAMES = {
    "opus": "Claude Opus 4.7",
    "deepseek": "Deepseek-v4-Flash",
    "deepseek-flash": "Deepseek-v4-Flash",
    "deepseek-pro": "Deepseek-v4-Pro",
    "qwen": "Qwen 3.6 27B",
    "qwen-plus": "Qwen 3.6 Plus",
    "kimi": "Kimi K2.6",
    "gpt55": "GPT-5.5",
    "gpt-5.5": "GPT-5.5",
}

# Cells whose names don't follow the simple agent-sim pattern.
SPECIAL_CELLS = {
    "baseline": {
        "agent": "Claude Opus 4.7",
        "sim": "GPT-5.5",
        "persona": "original",
    },
}


def resolve_cell(cell_label: str) -> dict:
    """Given a cell label like 'deepseek-gpt55' or 'deepseek-qwen-hardened',
    return {agent, sim, persona, human}.

    Naming rule for current cells: <agent>-<sim>[-hardened]. We split from
    the LEFT (agent first), the rest is the SIM, then optional persona suffix.
    Known multi-word model parts (deepseek-pro, qwen-plus, qwen-deepseek): we
    use a small lookup table since splitting is ambiguous.
    """
    if cell_label in SPECIAL_CELLS:
        sc = SPECIAL_CELLS[cell_label]
        return {**sc, "human": f"{sc['agent']} × {sc['sim']}"}

    # No-grilling baselines: cell = "no-grilling-<agent_short>"
    if cell_label.startswith("no-grilling-"):
        agent_key = cell_label[len("no-grilling-"):]
        agent = MODEL_FULL_NAMES.get(agent_key, agent_key)
        return {
            "agent": agent,
            "sim": "(none - no-grilling baseline)",
            "persona": "(none)",
            "human": f"{agent} × (no SIM)",
        }

    persona = "original"
    rest = cell_label
    if rest.endswith("-hardened"):
        persona = "hardened (anti-fabrication)"
        rest = rest[: -len("-hardened")]

    # Known cell decompositions (hand-mapped to avoid split ambiguity).
    cell_map = {
        "deepseek-qwen": ("deepseek-flash", "qwen"),
        "qwen-deepseek": ("qwen", "deepseek-flash"),
        "deepseek-gpt55": ("deepseek-flash", "gpt55"),
        "deepseek-qwen-plus": ("deepseek-flash", "qwen-plus"),
        "kimi-deepseek-pro": ("kimi", "deepseek-pro"),
        "kimi-gpt55": ("kimi", "gpt55"),
    }
    if rest in cell_map:
        agent_key, sim_key = cell_map[rest]
    else:
        # Best-effort fallback: split on first '-'
        parts = rest.split("-", 1)
        agent_key = parts[0]
        sim_key = parts[1] if len(parts) > 1 else "?"

    agent = MODEL_FULL_NAMES.get(agent_key, agent_key)
    sim = MODEL_FULL_NAMES.get(sim_key, sim_key)
    human = f"{agent} × {sim}"
    if persona != "original":
        human += f" ({persona})"
    return {
        "agent": agent,
        "sim": sim,
        "persona": persona,
        "human": human,
    }


if __name__ == "__main__":
    # Quick self-test
    samples = [
        "deepseek-qwen", "qwen-deepseek", "deepseek-gpt55",
        "deepseek-qwen-hardened", "deepseek-qwen-plus",
        "kimi-deepseek-pro", "kimi-gpt55", "baseline",
    ]
    for s in samples:
        r = resolve_cell(s)
        print(f"  {s:30} → {r['human']}")
