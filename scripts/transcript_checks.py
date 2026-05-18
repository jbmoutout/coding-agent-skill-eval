#!/usr/bin/env python3
"""
transcript_checks.py - extract machine-checkable signals from a v2 opencode run.

Usage:
  transcript_checks.py <results-dir>

Reads <results-dir>/raw_export.jsonl, writes <results-dir>/transcript_checks.json.

Per-task spec & rubric: checks candidate count, structure markers, gate question,
forbidden vocab, TS signatures in Solution sections, file references and line
citations (both styles), word count.

Calibration notes (from a 3-cell paired ablation pilot):
- Citation regex accepts both `file.ts:N` colon-line and `file.ts (lines N-M)`
  paren-style; observed in different harness output formats.
- Bare TS type names in prose (e.g. `Result<T, E>`) trigger generic-bracket
  pattern - flagged as TS-sigs by the skill's letter, but reviewers should
  distinguish "type-name in prose" from "function signature pre-proposed".
"""
import json
import re
import sys
import pathlib

def extract(text):
    # --- 1. Candidates: H2 OR H3 headers like "## 1. Name" / "### 1. Name" / "### Candidate 1:" ---
    # Patterns observed across the pilot:
    #   "## 1. Name"
    #   "### 1. Name"
    #   "## Candidate 1:" / "## **Candidate N**"
    #   "### Candidate 1:" / "**1. Name**"
    # Allows optional emoji/symbol prefix between header marker and number/Candidate keyword
    cand_pattern = r'^(?:#{2,3}\s+(?:[^\w\s]+\s+)?(?:\*\*)?(?:Candidate\s+)?\d+(?:\.|:)?(?:\*\*)?\s+|\*\*\d+\.\s+[A-Z])'
    cand_positions = [m.start() for m in re.finditer(cand_pattern, text, re.MULTILINE)]
    cand_blocks = []
    for i, start in enumerate(cand_positions):
        end = cand_positions[i+1] if i+1 < len(cand_positions) else len(text)
        cand_blocks.append(text[start:end])

    # --- 2. Structure markers (Files / Problem / Solution / Benefits) ---
    # Accept BOTH "**Files**" (bold-only) and "**Files:**" (period or colon inside bold).
    # Also accept "Files:" as a section header in table-format candidates.
    def has_section(block, section_re):
        # bold with optional trailing punctuation inside the bolding
        return bool(re.search(r'\*\*\s*' + section_re + r'\s*[:.]?\s*\*\*', block, re.IGNORECASE)) or \
               bool(re.search(r'(?:^|\n)\s*' + section_re + r'\s*:', block, re.IGNORECASE))
    structure = []
    for i, block in enumerate(cand_blocks):
        s = {
            "candidate_idx": i+1,
            "files":    has_section(block, r'Files?'),
            "problem":  has_section(block, r'Problem'),
            # Accept "Solution", "Proposed change", "Proposed", or bare "Change"
            # (table-format candidates use **Change** | **Benefit** as compact pair)
            "solution": has_section(block, r'(?:Solution|Proposed change|Proposed|Change)'),
            "benefits": has_section(block, r'Benefits?'),
        }
        s["all_four"] = all([s["files"], s["problem"], s["solution"], s["benefits"]])
        structure.append(s)
    structure_pass = (sum(1 for s in structure if s["all_four"]) / len(structure)) >= 0.6 if structure else False

    # --- 3. Gate question ---
    gate_patterns = [
        r'which.*(?:would you like|want).*(?:explore|expand|dive|deepen|drill|focus|tackle|elaborate)',
        r'which (?:one|candidate)\??',
        r'want me to (?:expand|explore|deepen|dive|elaborate)',
        r'should (?:i|we) (?:expand|explore|deepen|dive)',
    ]
    gate_matches = []
    for pat in gate_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            gate_matches.append({"pattern": pat, "match": m.group(0)[:140]})
    gate_present = len(gate_matches) > 0

    # --- 4. Forbidden vocab ---
    forbidden_hits = []
    for m in re.finditer(r'\bboundar(?:y|ies)\b', text, re.IGNORECASE):
        ctx = text[max(0, m.start()-50):min(len(text), m.end()+50)].replace('\n', ' ')
        forbidden_hits.append({"term": m.group(0).lower(), "context": ctx})
    service_arch_patterns = [
        r'\bservice layer\b',
        r'\bservice module\b',
        # Slash-variant: "service/repository layer", "services/repository layer", etc.
        r'\bservices?/\w+\s+layer\b',
        r'\b\w+/services?\s+layer\b',
        r'\b(?:the|a|each|every)\s+\w+\s+service\b(?!\s+(?:worker|account|provider|key))',
        r'\bservices?\s+(?:that|which|module|layer|pattern)\b',
    ]
    for pat in service_arch_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            ctx = text[max(0, m.start()-50):min(len(text), m.end()+50)].replace('\n', ' ')
            forbidden_hits.append({"term": "service-as-arch", "match": m.group(0), "context": ctx})

    # --- 5. TS sigs in Solution sections (skill explicitly forbids during candidate stage) ---
    ts_patterns = [
        (r'function\s+\w+\s*\([^)]*:\s*\w+',                       'function-with-typed-params'),
        (r'(?:const|let)\s+\w+\s*:\s*\w+\s*=',                     'typed-const'),
        (r'\binterface\s+\w+\s*\{',                                'interface-decl'),
        (r'\btype\s+\w+\s*=',                                      'type-alias'),
        (r'\w+\s*\([^)]*:\s*\w+[^)]*\)\s*(?::\s*\w+)?\s*=>',       'arrow-with-typed-params'),
        (r'\w+\s*<[A-Z]\w*(?:,\s*[A-Z]\w*)*>',                     'generic-bracket'),
    ]
    ts_sigs = []
    for cb_idx, block in enumerate(cand_blocks):
        sol = re.search(r'\*\*Solution\*\*(.*?)(?:\*\*Benefits?\*\*|\Z)', block, re.IGNORECASE | re.DOTALL)
        if not sol:
            continue
        for pat, kind in ts_patterns:
            for m in re.finditer(pat, sol.group(1)):
                ts_sigs.append({"candidate": cb_idx+1, "kind": kind, "match": m.group(0)[:80]})

    # --- 6. Citations: file-name mentions + file:line precision in either style ---
    # File-name mentions (backticked or not)
    file_refs = re.findall(r'`?([\w/.-]+\.(?:tsx?|jsx?|mjs|prisma|json|md))`?', text)
    # Filter spurious matches (only keep ones that have a slash or known extension)
    file_refs = [f for f in file_refs if '.' in f]
    file_refs_unique = sorted(set(file_refs))

    # Line-precision citations: any line-number expression near a filename
    # Accepts: file.ts:N, file.ts:N-M, file.ts (lines N-M), file.ts (line N), lines N-M of file.ts
    line_cite_patterns = [
        r'([\w/.-]+\.(?:tsx?|jsx?|mjs|prisma))\s*:\s*~?\s*\d+(?:[-–-]\d+)?',
        r'([\w/.-]+\.(?:tsx?|jsx?|mjs|prisma))\s*\([^)]*lines?\s*~?\s*\d+(?:[-–-]\d+)?[^)]*\)',
        r'lines?\s*~?\s*\d+(?:[-–-]\d+)?\s+(?:of|in)\s+([\w/.-]+\.(?:tsx?|jsx?|mjs|prisma))',
    ]
    line_citations = []
    for pat in line_cite_patterns:
        for m in re.finditer(pat, text):
            line_citations.append(m.group(0)[:120])
    # Also: bare "lines N-M" inside a paragraph right after a filename - caught loosely
    for m in re.finditer(r'`[\w/.-]+\.(?:tsx?|jsx?|prisma)`[^.\n]{0,80}lines?\s*~?\s*\d+(?:[-–-]\d+)?', text):
        line_citations.append(m.group(0)[:120])

    # --- 7. Prose-coherence-degradation signals ---
    # CJK characters mid-English-prose are the unambiguous training-data-leak signal.
    # Other signals (broken concatenations like "RecipeThe") are too false-positive-prone
    # against legitimate camelCase identifiers in technical text - skipped.
    degradation_signals = {}
    cjk_chars = re.findall(r'[一-鿿぀-ゟ゠-ヿ]', text)
    if cjk_chars:
        degradation_signals["cjk_chars"] = len(cjk_chars)
        cjk_examples = []
        for m in re.finditer(r'.{0,20}[一-鿿぀-ゟ゠-ヿ]+.{0,20}', text):
            cjk_examples.append(m.group(0).replace('\n', ' '))
            if len(cjk_examples) >= 3:
                break
        degradation_signals["cjk_examples"] = cjk_examples

    return {
        "final_text_chars": len(text),
        "word_count": len(text.split()),
        "candidate_count": len(cand_blocks),
        "structure_pass": structure_pass,
        "structure_per_candidate": structure,
        "gate_present": gate_present,
        "gate_matches": gate_matches,
        "forbidden_vocab_hit_count": len(forbidden_hits),
        "forbidden_vocab_hits": forbidden_hits,
        "ts_sigs_in_candidates_count": len(ts_sigs),
        "ts_sigs_in_candidates": ts_sigs,
        "file_mentions_count": len(file_refs),
        "file_mentions_unique_count": len(file_refs_unique),
        "file_mentions_unique": file_refs_unique,
        "line_citations_count": len(line_citations),
        "line_citations": sorted(set(line_citations)),
        "prose_degradation_signals": degradation_signals,
    }

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir")
    ap.add_argument("--text-file", default=None,
                    help="Path to text file to score instead of the last text event in raw_export.jsonl (for synthesis_locus=subagent runs).")
    ap.add_argument("--task-id", default=None,
                    help="Task identifier to stamp into the output (e.g. 'arch-001'). Defaults to the parent directory name of the results dir.")
    args = ap.parse_args()
    results = pathlib.Path(args.results_dir)
    if args.text_file:
        final_text = pathlib.Path(args.text_file).read_text()
        text_source = args.text_file
    else:
        events = [
            json.loads(line)
            for line in (results / "raw_export.jsonl").read_text().splitlines()
            if line.strip()
        ]
        text_events = [e for e in events if e["type"] == "text"]
        if not text_events:
            print("no text events", file=sys.stderr)
            sys.exit(1)
        final_text = text_events[-1]["part"]["text"]
        text_source = "raw_export.jsonl last text event"
    checks = extract(final_text)
    checks["run_id"] = results.name
    checks["task_id"] = args.task_id or results.parent.name or "(unspecified)"
    checks["text_source"] = text_source
    (results / "transcript_checks.json").write_text(json.dumps(checks, indent=2))
    # Summary
    summary = {k: checks[k] for k in ["candidate_count", "structure_pass", "gate_present",
        "forbidden_vocab_hit_count", "ts_sigs_in_candidates_count",
        "file_mentions_unique_count", "line_citations_count", "word_count", "final_text_chars"]}
    if checks.get("prose_degradation_signals"):
        summary["prose_degradation_signals"] = checks["prose_degradation_signals"]
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
