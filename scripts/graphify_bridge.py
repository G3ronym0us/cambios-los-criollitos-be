#!/usr/bin/env python3
"""Add cross-repo edges to the merged graphify graph (bot + frontend -> backend).

Why this exists
---------------
`graphify merge-graphs` unions the per-repo graphs and tags each node with its
`repo`, but it cannot see that the clients talk to the backend over HTTP: the
merged graph comes out with zero edges between repos. This script rebuilds that
boundary by matching literal (method, path) pairs across all three repos:

    backend/app/routers/*.py       APIRouter(prefix=) + @router.<verb>("<path>")
    whatsapp-bot/src/*.ts          request({ method, url })
    frontend/src/**/*.ts(x)        httpClient.<verb>("<path>")

Re-run it after every rebuild of the merged graph, or the cross-repo edges vanish.
`graphify_all.py` runs the whole pipeline (per-repo graphs -> merge -> bridge ->
html) and is the normal entry point; use this script directly to re-bridge only.

Idempotent: existing edges tagged `cross_repo` are dropped before rewriting.

Frontend calls arrive two ways and both are bridged: through the `httpClient`
singleton, and as raw `fetch()` (auth and a few pages bypass the interceptor).
Raw fetch to a third-party host is reported as external, never bridged.

Known limits
------------
- Bot: only `request({...})` call sites under `whatsapp-bot/src/*.ts` are scanned.
- URL expressions are read with a real JS string/template scanner, because paths
  like `/funds/movements/${uuid}/locate${query ? `?${query}` : ''}` nest a
  template inside a template. A naive `[^`'"]*` regex truncates at the inner
  quote and silently drops the call.
- The bot's call sites are multi-line object literals, so the argument object is
  read by balanced-brace scanning. A line-window approach silently borrows the
  previous call's `method` and produces wrong edges.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# backend/scripts/graphify_bridge.py -> tasas-project/
DEFAULT_ROOT = Path(__file__).resolve().parents[2]

# words that look like a call but are not a method declaration
_NOT_A_METHOD = {
    "if", "for", "while", "switch", "catch", "return", "await", "typeof",
    "function", "constructor", "super", "this", "new", "else", "do", "try",
}


def norm_path(p: str) -> str:
    """Normalize a URL path so client templates and FastAPI params compare equal."""
    p = p.strip().strip("'\"`")
    p = re.sub(r"\$\{[^}]*\}", "{}", p)   # client: ${uuid}
    p = re.sub(r"\{[^}]*\}", "{}", p)     # backend: {op_uuid}
    p = p.split("?")[0].split("#")[0]
    while p.startswith("{}"):             # `${baseUrl}/auth/login` -> /auth/login
        p = p[2:]
    # A placeholder glued to a segment (no separating slash) is an interpolated
    # query string, not a path param: `/locate${query ? '?'+query : ''}`.
    # A real param always follows a slash, so only the glued form is dropped.
    p = re.sub(r"(?<!/)\{\}$", "", p)
    p = p.rstrip("/")
    if not p.startswith("/"):
        p = "/" + p
    return p or "/"


def read_js_string(text: str, i: int):
    """Read the JS string or template literal at text[i].

    Returns (value, index_after_close) with every `${...}` collapsed to `{}`.
    Handles templates nested inside interpolations, which a regex cannot.
    """
    if i >= len(text) or text[i] not in "'\"`":
        return None, i
    q, j, out = text[i], i + 1, []
    while j < len(text):
        c = text[j]
        if c == "\\":
            out.append(text[j:j + 2])
            j += 2
            continue
        if c == q:
            return "".join(out), j + 1
        if q != "`" and c == "\n":
            return None, j          # unterminated plain string
        if q == "`" and c == "$" and text[j + 1:j + 2] == "{":
            depth, k = 1, j + 2
            while k < len(text) and depth:
                ch = text[k]
                if ch in "'\"`":
                    _, k = read_js_string(text, k)
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                k += 1
            out.append("{}")
            j = k
            continue
        out.append(c)
        j += 1
    return None, j


def read_call_args(text: str, open_paren: int):
    """Source text of a call's argument list, given the index of its `(`."""
    depth, k = 0, open_paren
    while k < len(text):
        c = text[k]
        if c in "'\"`":
            _, k = read_js_string(text, k)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:k], k + 1
        k += 1
    return text[open_paren + 1:], len(text)


def skip_ws_and_generics(text: str, i: int) -> int:
    """Advance past whitespace and a `<...>` type argument list."""
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "<":
        depth = 0
        while i < len(text):
            if text[i] == "<":
                depth += 1
            elif text[i] == ">":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        while i < len(text) and text[i].isspace():
            i += 1
    return i


# --------------------------------------------------------------------------
# backend
# --------------------------------------------------------------------------
def parse_backend_routes(backend_root: Path) -> dict:
    """Map (METHOD, full_path) -> [{handler, file, line}] across every router.

    The prefix comes from `APIRouter(prefix="...")` in each router module;
    `include_router` in main.py adds none. Two modules may share a prefix
    (clients.py and client_accounts.py both use /clients), so values are lists.
    """
    routes = defaultdict(list)
    for py in sorted((backend_root / "app/routers").glob("*.py")):
        text = py.read_text(encoding="utf-8")
        pm = re.search(r"""APIRouter\([^)]*prefix\s*=\s*["']([^"']+)["']""", text)
        prefix = pm.group(1).rstrip("/") if pm else ""
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"@router\.(get|post|put|patch|delete)\(", line)
            if not m:
                continue
            blob = "\n".join(lines[i:i + 6])  # decorator may wrap onto later lines
            pathm = re.search(r"""@router\.\w+\(\s*["']([^"']*)["']""", blob)
            if pathm is None:
                continue
            full = norm_path(prefix + pathm.group(1))
            for j in range(i, min(i + 12, len(lines))):
                dm = re.match(r"\s*async def (\w+)|^def (\w+)", lines[j])
                if dm:
                    routes[(m.group(1).upper(), full)].append({
                        "handler": dm.group(1) or dm.group(2),
                        "file": py.relative_to(backend_root).as_posix(),
                        "line": j + 1,
                    })
                    break
    return dict(routes)


# --------------------------------------------------------------------------
# clients
# --------------------------------------------------------------------------
def _enclosing(text: str, pos: int):
    """(class_name, member_name) for the declaration nearest above `pos`."""
    cls = None
    for m in re.finditer(r"\bclass\s+(\w+)", text):
        if m.start() < pos:
            cls = m.group(1)
        else:
            break
    best = None
    patterns = (
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)",
        r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(",
        r"^[ \t]{2,}(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*[<(]",
    )
    for pat in patterns:
        for m in re.finditer(pat, text, re.MULTILINE):
            if m.start() < pos and m.group(1) not in _NOT_A_METHOD:
                if best is None or m.start() > best[0]:
                    best = (m.start(), m.group(1))
    return cls, (best[1] if best else None)


def parse_bot_calls(bot_root: Path) -> list:
    """request({ method, url }) call sites, argument object read by brace balance."""
    calls = []
    for ts in sorted((bot_root / "src").glob("*.ts")):
        text = ts.read_text(encoding="utf-8")
        for m in re.finditer(r"\brequest\s*(?:<[^>]*>)?\s*\(\s*\{", text):
            start = text.index("{", m.end() - 1)
            depth, k = 0, start
            while k < len(text):
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            obj = text[start:k + 1]
            um = re.search(r"""url:\s*([`'"])([^`'"]*)\1""", obj)
            mm = re.search(r"""method:\s*['"](\w+)['"]""", obj)
            if not um or not mm:
                continue
            cls, fn = _enclosing(text, m.start())
            if not fn:
                continue
            calls.append({
                "repo": "whatsapp-bot",
                "file": ts.relative_to(bot_root).as_posix(),
                "cls": cls, "fn": fn,
                "method": mm.group(1).upper(),
                "path": norm_path(um.group(2)),
                "line": text[:m.start()].count("\n") + 1,
            })
    return calls


_EXTERNAL_HOST = re.compile(r"^https?://(?!localhost|127\.0\.0\.1)", re.I)
_LOCAL_HOST = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?", re.I)


def parse_frontend_calls(fe_root: Path):
    """Frontend -> backend HTTP calls, via httpClient and via raw fetch().

    Returns (calls, external, hardcoded) where `external` are third-party fetches
    (never bridged) and `hardcoded` are backend fetches with a literal host.
    """
    calls, external, hardcoded = [], [], []
    src = fe_root / "src"
    files = sorted(list(src.rglob("*.ts")) + list(src.rglob("*.tsx")))

    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(fe_root).as_posix()

        def add(pos, method, path, via):
            cls, fn = _enclosing(text, pos)
            if not fn:
                return
            calls.append({
                "repo": "frontend", "file": rel, "cls": cls, "fn": fn,
                "method": method, "path": path, "via": via,
                "line": text[:pos].count("\n") + 1,
            })

        # 1) httpClient.<verb>(<path>, ...)
        for m in re.finditer(r"httpClient\.(get|post|put|patch|delete)", text):
            i = skip_ws_and_generics(text, m.end())
            if i >= len(text) or text[i] != "(":
                continue
            i += 1
            while i < len(text) and text[i].isspace():
                i += 1
            val, _ = read_js_string(text, i)
            if val is None:
                continue
            add(m.start(), m.group(1).upper(), norm_path(val), "httpClient")

        # 2) raw fetch(<url expr>, { method }) - auth and a few pages bypass httpClient
        if rel == "src/utils/httpInterceptor.ts":
            continue
        for m in re.finditer(r"\bfetch\s*\(", text):
            args, _ = read_call_args(text, m.end() - 1)
            line = text[:m.start()].count("\n") + 1
            # concatenate every literal piece of the url expression
            pieces, k = [], 0
            while k < len(args):
                if args[k] == ",":
                    break
                if args[k] in "'\"`":
                    val, k = read_js_string(args, k)
                    if val is not None:
                        pieces.append(val)
                    continue
                k += 1
            url = "".join(pieces)
            if not url:
                continue
            if _EXTERNAL_HOST.match(url):
                external.append(f"{rel}:{line}  {url}")
                continue
            if _LOCAL_HOST.match(url):
                hardcoded.append(f"{rel}:{line}  {url}")
                url = _LOCAL_HOST.sub("", url)
            mm = re.search(r"""method:\s*['"](\w+)['"]""", args[k:])
            add(m.start(), mm.group(1).upper() if mm else "GET",
                norm_path(url), "fetch")

    return calls, external, hardcoded


# --------------------------------------------------------------------------
def make_finder(nodes):
    by_repo = defaultdict(list)
    for n in nodes:
        by_repo[n.get("repo")].append(n)

    def find_node(repo, source_suffix, symbol, cls=None):
        cands = [n for n in by_repo.get(repo, [])
                 if str(n.get("source_file", "")).endswith(source_suffix)]
        sym = symbol.lower()
        if cls:  # class methods carry the class in the id: ..._<class>_<method>
            want = f"_{cls.lower()}_{sym}"
            for n in cands:
                if n["id"].endswith(want):
                    return n["id"]
        for n in cands:
            if n["id"].endswith("_" + sym):
                return n["id"]
        for n in cands:
            if str(n.get("label", "")).lower().strip(".") in (sym, sym + "()"):
                return n["id"]
        # Fall back to the file node, whose label is the bare filename. Calls in
        # nested arrow functions inside React components often have no symbol node
        # of their own; anchoring to the file keeps the boundary visible instead of
        # dropping the call entirely.
        # graphify disambiguates same-named files by prefixing the parent dir, so
        # the label is `page.tsx` or `create/page.tsx` depending on collisions.
        base = source_suffix.rsplit("/", 1)[-1]
        for n in cands:
            if str(n.get("label", "")).endswith(base):
                return n["id"]
        return None
    return find_node


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                    help="project root holding backend/, whatsapp-bot/, frontend/")
    ap.add_argument("--graph", type=Path, default=None,
                    help="merged graph.json to patch (default: <root>/graphify-out/graph.json)")
    args = ap.parse_args()

    root = args.root.resolve()
    merged = args.graph or root / "graphify-out/graph.json"
    backend, bot, fe = root / "backend", root / "whatsapp-bot", root / "frontend"
    if not merged.exists():
        sys.exit(f"error: merged graph not found: {merged}\n"
                 f"run graphify merge-graphs first (see graphify_all.py)")

    backend_routes = parse_backend_routes(backend)
    client_calls = []
    if (bot / "src").exists():
        client_calls += parse_bot_calls(bot)
    external, hardcoded = [], []
    if (fe / "src").exists():
        fe_calls, external, hardcoded = parse_frontend_calls(fe)
        client_calls += fe_calls

    graph = json.loads(merged.read_text(encoding="utf-8"))
    nodes = graph["nodes"]
    edge_key = "edges" if "edges" in graph else "links"
    graph[edge_key] = [e for e in graph[edge_key] if not e.get("cross_repo")]
    find_node = make_finder(nodes)

    new_edges, unmatched, used = [], [], set()
    for call in client_calls:
        key = (call["method"], call["path"])
        targets = backend_routes.get(key)
        if not targets:
            unmatched.append(call)
            continue
        used.add(key)
        src = find_node(call["repo"], call["file"], call["fn"], call["cls"])
        if not src:
            unmatched.append({**call, "reason": "client node not found"})
            continue
        hit = False
        for route in targets:
            tgt = find_node("backend", route["file"], route["handler"])
            if not tgt:
                continue
            hit = True
            new_edges.append({
                "source": src, "target": tgt, "relation": "calls",
                "confidence": "INFERRED", "confidence_score": 0.95,
                "source_file": f"{call['repo']}/{call['file']}",
                "source_location": f"L{call['line']}", "weight": 1.0,
                "context": f"HTTP {call['method']} {call['path']}",
                "cross_repo": True, "client": call["repo"],
                "via": call.get("via", "request"),
            })
        if not hit:
            unmatched.append({**call, "reason": "backend handler node not found"})

    # routing.json fixtures name the endpoints they exercise; wire them to handlers
    ids = {n["id"] for n in nodes}
    for frag, method, path in (
        ("backend_post_operations", "POST", "/whatsapp/operations"),
        ("backend_patch_cancel", "PATCH", "/whatsapp/operations/{}/cancel"),
    ):
        s = next((n for n in ids if n.endswith(f"src_test_cases_routing_{frag}")), None)
        targets = backend_routes.get((method, path)) or []
        for route in targets:
            t = find_node("backend", route["file"], route["handler"])
            if s and t:
                new_edges.append({
                    "source": s, "target": t, "relation": "calls",
                    "confidence": "INFERRED", "confidence_score": 0.95,
                    "source_file": "whatsapp-bot/src/test-cases/routing.json",
                    "source_location": None, "weight": 1.0,
                    "context": f"fixture asserts HTTP {method} {path}",
                    "cross_repo": True, "client": "whatsapp-bot",
                    "fixture_bridge": True,
                })

    seen, deduped = set(), []
    for e in new_edges:
        k = (e["source"], e["target"], e["context"])
        if k not in seen:
            seen.add(k)
            deduped.append(e)

    graph[edge_key] += deduped
    merged.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    per_client = defaultdict(int)
    for e in deduped:
        per_client[e["client"]] += 1
    callers = defaultdict(set)
    for c in client_calls:
        if (c["method"], c["path"]) in backend_routes:
            callers[(c["method"], c["path"])].add(c["repo"])

    print(f"backend routes parsed:   {len(backend_routes)}")
    print(f"client call sites:       {len(client_calls)}  "
          f"({sum(1 for c in client_calls if c['repo'] == 'whatsapp-bot')} bot, "
          f"{sum(1 for c in client_calls if c['repo'] == 'frontend')} frontend)")
    print(f"BRIDGE EDGES WRITTEN:    {len(deduped)}  "
          f"({dict(per_client)})  -> {merged}")

    orphans = sorted(k for k in backend_routes if k not in used)
    print(f"\n--- backend routes no client calls ({len(orphans)}/{len(backend_routes)}):")
    for m, p in orphans:
        print(f"    {m:6} {p}")

    shared = sorted(k for k, v in callers.items() if len(v) > 1)
    print(f"\n--- routes called by BOTH bot and frontend ({len(shared)}):")
    for m, p in shared:
        print(f"    {m:6} {p}")

    print(f"\n--- BROKEN? client calls with no matching backend route ({len(unmatched)}):")
    for c in unmatched:
        print(f"    [{c['repo']:12}] {c['method']:6} {c['path']:42} "
              f"{c['file']}:{c['line']} {c.get('reason', '')}")

    if hardcoded:
        print(f"\n--- backend calls with a hardcoded host ({len(hardcoded)}):")
        for s in hardcoded:
            print(f"    {s}")

    if external:
        print(f"\n--- third-party fetch, not bridged ({len(external)}):")
        for s in external:
            print(f"    {s}")


if __name__ == "__main__":
    main()
