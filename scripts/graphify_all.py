#!/usr/bin/env python3
"""Rebuild the merged tasas-project knowledge graph across all three repos.

    python backend/scripts/graphify_all.py            # merge + bridge + html
    python backend/scripts/graphify_all.py --extract  # also re-run per-repo AST

The canonical graph is <root>/graphify-out/graph.json — the cross-repo artifact
lives at the project root, above the individual git repos. Each repo also keeps
its own graphify-out/graph.json; those are inputs to the merge, not the answer.

Pipeline:
  1. (--extract) re-run AST extraction per repo and rebuild its graph.json
  2. graphify merge-graphs  -> root graphify-out/graph.json
  3. graphify_bridge.py     -> cross-repo HTTP edges (merge alone yields zero)
  4. graphify export html   -> root graphify-out/graph.html

--extract only redoes the deterministic AST pass. Semantic extraction (docs, the
bot's test-case fixtures, OCR images) comes from the LLM subagent flow in the
graphify skill and is NOT re-run here; it is replayed from each repo's on-disk
semantic cache (graphify-out/cache/). Run /graphify in a repo to refresh it.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
REPOS = ("backend", "whatsapp-bot", "frontend")


def run(cmd, cwd, dry=False):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    if dry:
        return 0
    return subprocess.call(cmd, cwd=cwd)


def python_for(root: Path) -> str:
    """The interpreter graphify is installed into (written by the skill)."""
    marker = root / "backend/graphify-out/.graphify_python"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    return sys.executable


def extract_repo(root: Path, repo: str, py: str, dry=False):
    """Re-run AST extraction for one repo and rebuild its graph.json."""
    repo_dir = root / repo
    if not repo_dir.exists():
        print(f"  skip {repo}: not found")
        return
    # the extraction prompt the semantic cache entries are stamped with
    spec = str(root / "backend/.claude/skills/graphify/references"
                      "/extraction-spec.md")
    script = f"""
import json
from pathlib import Path
from graphify.detect import detect
from graphify.extract import collect_files, extract
from graphify.cache import check_semantic_cache
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.export import to_json
from collections import Counter

out = Path('graphify-out'); out.mkdir(exist_ok=True)
det = detect(Path('.'))
code = []
for f in det.get('files', {{}}).get('code', []):
    code.extend(collect_files(Path(f)) if Path(f).is_dir() else [Path(f)])
ast = extract(code, cache_root=Path('.')) if code else {{'nodes': [], 'edges': []}}

# Replay semantic extraction from the on-disk cache. Every detected file is
# offered, not just docs: the bot's test-case fixtures are detected as `code`
# (JSON has no AST) but were deliberately routed through semantic extraction.
all_files = [f for fl in det.get('files', {{}}).values() for f in fl]
sn, se, sh, _ = check_semantic_cache(all_files, root='.', prompt_file={spec!r})
sem = {{'nodes': sn, 'edges': se, 'hyperedges': sh}}

seen = {{n['id'] for n in ast['nodes']}}
merged = list(ast['nodes'])
for n in sem.get('nodes', []):
    if n['id'] not in seen:
        merged.append(n); seen.add(n['id'])
ex = {{'nodes': merged, 'edges': ast['edges'] + sem.get('edges', []),
       'hyperedges': sem.get('hyperedges', []), 'input_tokens': 0, 'output_tokens': 0}}
G = build_from_json(ex, root='.', directed=False)
comms = cluster(G)
labels = {{}}
for cid, ns in comms.items():
    srcs = Counter(str(G.nodes[x].get('source_file','')).rsplit('/',1)[0] for x in ns if G.nodes[x].get('source_file'))
    labels[cid] = '{repo}: ' + (srcs.most_common(1)[0][0].split('/')[-1] if srcs else str(cid))
to_json(G, comms, 'graphify-out/graph.json', force=True, community_labels=labels)
print(f'  {repo}: {{G.number_of_nodes()}} nodes, {{G.number_of_edges()}} edges '
      f'({{len(ast["nodes"])}} AST + {{len(sem.get("nodes", []))}} semantic)')
"""
    if dry:
        print(f"  $ ({repo}) re-extract AST + rebuild graph.json")
        return
    subprocess.call([py, "-c", script], cwd=repo_dir)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--extract", action="store_true",
                    help="re-run per-repo AST extraction before merging")
    ap.add_argument("--no-html", action="store_true", help="skip the html export")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = args.root.resolve()
    py = python_for(root)
    graphify = shutil.which("graphify")
    if not graphify:
        sys.exit("error: `graphify` not on PATH")

    present = [r for r in REPOS if (root / r).exists()]
    print(f"root: {root}\nrepos: {', '.join(present)}\n")

    if args.extract:
        print("[1/4] per-repo AST extraction")
        for r in present:
            extract_repo(root, r, py, args.dry_run)
    else:
        print("[1/4] per-repo extraction skipped (use --extract to refresh)")

    graphs = [root / r / "graphify-out/graph.json" for r in present]
    missing = [g for g in graphs if not g.exists()]
    if missing:
        sys.exit("error: missing per-repo graph(s):\n  " +
                 "\n  ".join(str(m) for m in missing) +
                 "\nrun with --extract, or /graphify in that repo first")

    print("\n[2/4] merge")
    (root / "graphify-out").mkdir(exist_ok=True)
    rc = run([graphify, "merge-graphs", *[str(g) for g in graphs],
              "--out", "graphify-out/graph.json"], cwd=root, dry=args.dry_run)
    if rc != 0:
        sys.exit(f"merge-graphs failed ({rc})")

    print("\n[3/4] cross-repo bridge")
    rc = run([py, str(root / "backend/scripts/graphify_bridge.py"),
              "--root", str(root)], cwd=root, dry=args.dry_run)
    if rc != 0:
        sys.exit(f"bridge failed ({rc})")

    if args.no_html:
        print("\n[4/4] html export skipped")
    else:
        print("\n[4/4] html export")
        run([graphify, "export", "html"], cwd=root, dry=args.dry_run)

    if not args.dry_run:
        g = json.loads((root / "graphify-out/graph.json").read_text(encoding="utf-8"))
        ek = "edges" if "edges" in g else "links"
        cross = sum(1 for e in g[ek] if e.get("cross_repo"))
        print(f"\ndone: {len(g['nodes'])} nodes, {len(g[ek])} edges, "
              f"{cross} cross-repo -> {root / 'graphify-out/graph.json'}")


if __name__ == "__main__":
    main()
