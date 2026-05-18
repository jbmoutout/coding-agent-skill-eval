#!/usr/bin/env python3
"""
Layer 1 - Mechanical fact-check of a SETTLED_DESIGN.md against the repo.

Scope: no LLM, no judgment, just grep/find/count. Each claim becomes one of
PASS / FAIL / UNVERIFIABLE. Score = pass / (pass + fail).

The ground-truth data for a given task lives in a JSON config file. See
`tasks/<task-id>/facts_config.json.example` for the schema.

Usage:
    grade_facts.py <rep_dir> --config <config.json>
    grade_facts.py <rep_dir> --config <config.json> --print

Env:
    TARGET_REPO_ROOT - codebase being audited (overrides config's repo_root).

Categories of claims extracted from the SETTLED markdown:

1. existing_file:    paths the design references as if they exist.
                     Verify file exists in repo.
2. proposed_file:    paths the design proposes creating.
                     Verify file does NOT already exist.
3. count_claim:      "~N occurrences of X" - verify count is within tolerance
                     of the config's ground_truth_counts.
4. security_claim:   patterns indicating awareness of known vulnerabilities.
                     Verify against real_vulns in config.
5. route_existence:  explicit route paths the design references.
                     Verify exists.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    # Coerce lists into sets for membership checks
    cfg["known_existing_files"] = set(cfg.get("known_existing_files", []))
    cfg["common_proposed_files"] = set(cfg.get("common_proposed_files", []))
    cfg["real_vulns"] = set(cfg.get("real_vulns", []))
    cfg["ground_truth_counts"] = dict(cfg.get("ground_truth_counts", {}))
    cfg["count_target_map"] = dict(cfg.get("count_target_map", {}))
    return cfg


def file_exists_in_repo(rel_path: str, repo_root: Path) -> bool:
    return (repo_root / rel_path).exists()


def grep_count(pattern: str, paths: list, repo_root: Path) -> int:
    """Count grep matches across given paths."""
    cmd = ["grep", "-rn", pattern] + paths
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=repo_root, timeout=10,
        )
        return len([ln for ln in out.stdout.splitlines() if ln.strip()])
    except subprocess.SubprocessError:
        return -1


def extract_claims(text: str, cfg: dict) -> dict:
    """Pull concrete factual claims out of the SETTLED markdown."""
    claims = {
        "existing_file_refs": set(),
        "proposed_file_refs": set(),
        "count_claims": [],
        "security_claims": [],
        "route_refs": set(),
    }
    # File path mentions: anything that looks like a/b.ts or a/b/c.ts
    for m in re.finditer(r"`?([\w\-]+(?:/[\w\-]+)+\.tsx?)`?", text):
        path = m.group(1)
        if path in cfg["known_existing_files"]:
            claims["existing_file_refs"].add(path)
        elif path in cfg["common_proposed_files"]:
            claims["proposed_file_refs"].add(path)
        elif path.startswith("app/api/") and path.endswith("route.ts"):
            claims["route_refs"].add(path)

    # Count claims like "~15 occurrences", "~21 places", "16 files", "5-6 files"
    for m in re.finditer(
        r"(?:~|approximately\s+|about\s+)?(\d+)(?:[-–](\d+))?\s+"
        r"(?:occurrences?|places?|files?|routes?|instances?|calls?)\s*"
        r"(?:of\s+|with\s+|to\s+|that\s+)?[`']?([\w$.\-]+)[`']?",
        text, flags=re.IGNORECASE,
    ):
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else low
        target = m.group(3)
        claims["count_claims"].append({
            "low": low, "high": high, "target": target,
            "matched": m.group(0),
        })

    # Security claim patterns (configurable per task)
    sec_patterns = cfg.get("security_patterns", [])
    for pat in sec_patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            claims["security_claims"].append({
                "pattern": pat, "matched": m.group(0)[:120],
            })
    return claims


def map_count_target(target: str, matched: str, count_target_map: dict):
    """Look up which ground_truth key applies for a given count claim target.

    `count_target_map` is a dict of {substring_in_target: gt_key}. First match
    wins. Returns None if no key applies, which marks the claim UNVERIFIABLE.
    """
    target_l = target.lower()
    matched_l = matched.lower()
    for substr, gt_key in count_target_map.items():
        if substr in target_l or substr in matched_l:
            return gt_key
    return None


def verify_claims(claims: dict, cfg: dict, repo_root: Path) -> list:
    """Each claim -> {category, claim, verdict, expected, actual}."""
    results = []

    for path in sorted(claims["existing_file_refs"]):
        ok = file_exists_in_repo(path, repo_root)
        results.append({
            "category": "existing_file",
            "claim": f"references existing file `{path}`",
            "verdict": "PASS" if ok else "FAIL",
            "actual": "exists" if ok else "missing",
        })

    for path in sorted(claims["proposed_file_refs"]):
        exists = file_exists_in_repo(path, repo_root)
        results.append({
            "category": "proposed_file",
            "claim": f"proposes new file `{path}`",
            "verdict": "PASS" if not exists else "FAIL",
            "actual": "novel" if not exists else "already exists in repo",
        })

    for path in sorted(claims["route_refs"]):
        ok = file_exists_in_repo(path, repo_root)
        results.append({
            "category": "route_ref",
            "claim": f"references route `{path}`",
            "verdict": "PASS" if ok else "FAIL",
            "actual": "exists" if ok else "no such route",
        })

    for cc in claims["count_claims"]:
        gt_key = map_count_target(
            cc["target"], cc["matched"], cfg["count_target_map"],
        )
        if gt_key is None or gt_key not in cfg["ground_truth_counts"]:
            results.append({
                "category": "count_claim",
                "claim": f"`{cc['matched']}`",
                "verdict": "UNVERIFIABLE",
                "actual": "no ground-truth key matched",
            })
            continue
        gt = cfg["ground_truth_counts"][gt_key]
        # Tolerance: claim accepted if its range overlaps gt +/-20% (min 2).
        tol = max(2, int(gt * 0.2))
        in_range = (cc["low"] - tol) <= gt <= (cc["high"] + tol)
        results.append({
            "category": "count_claim",
            "claim": f"`{cc['matched']}` - design says {cc['low']}-{cc['high']}",
            "verdict": "PASS" if in_range else "FAIL",
            "expected": f"~{gt} (key={gt_key})",
            "actual": f"design range {cc['low']}-{cc['high']}",
        })

    if claims["security_claims"]:
        # The presence of any sec-pattern match means the design acknowledged
        # one of the known vulnerabilities. Layer 2 (constraints) handles the
        # negative case (design missed a vuln entirely).
        results.append({
            "category": "security_claim",
            "claim": "flags a known security vulnerability",
            "verdict": "PASS",
            "actual": f"{len(claims['security_claims'])} matching mention(s)",
        })
    return results


def score(results: list) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "UNVERIFIABLE": 0}
    for r in results:
        counts[r["verdict"]] += 1
    verifiable = counts["PASS"] + counts["FAIL"]
    facts_score = counts["PASS"] / verifiable if verifiable > 0 else None
    return {
        "facts_score": facts_score,
        "n_pass": counts["PASS"],
        "n_fail": counts["FAIL"],
        "n_unverifiable": counts["UNVERIFIABLE"],
        "n_total": len(results),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rep_dir", type=Path)
    ap.add_argument("--config", type=Path, required=True,
                    help="JSON config with ground-truth data for the task.")
    ap.add_argument("--print", action="store_true",
                    help="Also print summary to stdout.")
    args = ap.parse_args()

    rep_dir = args.rep_dir
    eval_dir = rep_dir / "eval"
    eval_dir.mkdir(exist_ok=True)

    settled = rep_dir / "extracted_files" / "SETTLED_DESIGN.md"
    if not settled.exists():
        print(f"NO SETTLED_DESIGN.md in {rep_dir}", file=sys.stderr)
        out = {"rep_dir": str(rep_dir), "skipped": "no_settled_design"}
        (eval_dir / "facts_report.json").write_text(json.dumps(out, indent=2))
        return

    cfg = load_config(args.config)
    repo_root = Path(
        os.environ.get("TARGET_REPO_ROOT")
        or cfg.get("repo_root")
        or ""
    )
    if not repo_root or not repo_root.is_dir():
        print(
            "TARGET_REPO_ROOT not set and config's repo_root is missing or "
            "not a directory. Set TARGET_REPO_ROOT=/path/to/audited-codebase "
            "or fix the config.",
            file=sys.stderr,
        )
        sys.exit(2)

    text = settled.read_text()
    claims = extract_claims(text, cfg)
    results = verify_claims(claims, cfg, repo_root)
    summary = score(results)
    summary["rep_dir"] = str(rep_dir)
    summary["repo_root"] = str(repo_root)
    summary["config"] = str(args.config)
    summary["results"] = results
    (eval_dir / "facts_report.json").write_text(json.dumps(summary, indent=2))

    if args.print:
        print(f"=== {rep_dir.name} ===")
        print(f"  facts_score: {summary['facts_score']}")
        print(
            f"  pass={summary['n_pass']} "
            f"fail={summary['n_fail']} "
            f"unverifiable={summary['n_unverifiable']}"
        )
        for r in results:
            sym = {"PASS": "+", "FAIL": "-", "UNVERIFIABLE": "?"}[r["verdict"]]
            print(f"  {sym} [{r['category']}] {r['claim']}")
            if r["verdict"] == "FAIL":
                print(f"      expected: {r.get('expected', '-')}")
                print(f"      actual:   {r.get('actual', '-')}")


if __name__ == "__main__":
    main()
