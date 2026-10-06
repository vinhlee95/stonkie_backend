#!/usr/bin/env python3
"""Map a diff to the HTTP routes whose handlers can reach the changed code.

Used by the smoke-test gate (.claude/hooks/require-smoke-test.sh, .claude/skills/smoke-test/smoke.sh).
Stdlib only, so it runs with any python3 and no venv.

1. Changed symbols: diff hunks vs the base are mapped onto the functions, methods, classes and
   module-level variables they touch. Comment/blank lines and import statements are ignored, as
   are whole-symbol deletions (their callers change too). Any other module-level statement
   marks every symbol in that module as changed.
2. Reference graph: each symbol references what its body names (resolving imports, package
   re-exports and `module.attr` chains), including decorators and argument defaults, so
   `Depends(get_portfolio_service)` counts. Methods are reached when their class is reached and
   the method name is used as an attribute somewhere on the path (`service.get_portfolio(...)`),
   which follows instances without type inference.
3. A route is affected if its handler reaches a changed symbol.

Static analysis: dynamic dispatch (getattr, string registries) is invisible, and attribute-name
matching over-approximates within reached classes.

Usage: smoke_routes.py --repo DIR --base SHA --exclude REGEX [--state FILE] < changed-files
Prints JSON: {routes: [...], changed_symbols: [...], required, covered, ok}.
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
IMPLICIT_METHODS = {
    "__init__",
    "__post_init__",
    "__new__",
    "__call__",
    "__enter__",
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__iter__",
    "__aiter__",
    "__next__",
    "__anext__",
    "__getattr__",
    "__getitem__",
}
DEFAULT_MAX_REQUIRED = 6
# `task.delay(...)` runs the task in a Celery worker, not in the API process: curling the endpoint
# never executes the task body, so the graph does not follow references through these.
CELERY_DISPATCH = {"delay", "apply_async", "s", "si", "signature"}


class Symbol:
    __slots__ = ("id", "module", "name", "kind", "start", "end", "chains", "attrs", "cls")

    def __init__(self, module, name, kind, start, end, cls=None):
        self.id = f"{module}:{name}"
        self.module, self.name, self.kind, self.start, self.end, self.cls = module, name, kind, start, end, cls
        self.chains = []  # [["name", "attr", ...]] referenced from this symbol
        self.attrs = set()  # attribute names used (for method reachability)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def module_name(path):
    mod = path[:-3]
    if mod.endswith("/__init__"):
        mod = mod[: -len("/__init__")]
    return mod.replace("/", ".")


def collect_refs(sym, nodes):
    """Record name/attribute chains and attribute names used anywhere under `nodes`."""
    skip = set()  # Name nodes that are the base of a Celery dispatch (ast.walk visits parents first)
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Attribute):
                sym.attrs.add(node.attr)
                chain, cur = [], node
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name) and id(cur) not in skip:
                    if CELERY_DISPATCH & set(chain):
                        skip.add(id(cur))
                    else:
                        sym.chains.append([cur.id, *reversed(chain)])
            elif isinstance(node, ast.Name) and id(node) not in skip:
                sym.chains.append([node.id])


def def_start(node):
    return min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])


class Module:
    def __init__(self, path, source):
        self.path = path
        self.name = module_name(path)
        self.is_pkg = path.endswith("__init__.py")
        self.lines = source.splitlines()
        self.tree = ast.parse(source, filename=path)
        self.symbols = {}  # local name -> Symbol (methods as "Class.meth")
        self.imports = {}  # local name -> ("mod", module) | ("from", module, name)
        self.import_lines = set()  # import statements and the module docstring: never behavior
        body = self.tree.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self.import_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
        self.routers = {}  # local name -> prefix
        self.routes = []  # (method, path, handler symbol name, line)
        self._collect_imports()
        self._collect_symbols()

    def _resolve_relative(self, level, module):
        if not level:
            return module or ""
        parts = self.name.split(".") if self.is_pkg else self.name.split(".")[:-1]
        parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
        return ".".join([*parts, module] if module else parts)

    def _collect_imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                self.import_lines.update(range(node.lineno, node.end_lineno + 1))
                for alias in node.names:
                    if alias.asname:
                        self.imports[alias.asname] = ("mod", alias.name)
                    else:
                        self.imports[alias.name.split(".")[0]] = ("mod", alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                self.import_lines.update(range(node.lineno, node.end_lineno + 1))
                base = self._resolve_relative(node.level, node.module)
                for alias in node.names:
                    if alias.name != "*":
                        self.imports[alias.asname or alias.name] = ("from", base, alias.name)

    def _add(self, sym, nodes):
        collect_refs(sym, nodes)
        self.symbols[sym.name] = sym

    def _collect_symbols(self):
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add(Symbol(self.name, node.name, "func", def_start(node), node.end_lineno), [node])
                self._collect_route(node)
            elif isinstance(node, ast.ClassDef):
                cls = Symbol(self.name, node.name, "class", def_start(node), node.end_lineno)
                class_level = [*node.bases, *node.keywords, *node.decorator_list]
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        meth = Symbol(
                            self.name,
                            f"{node.name}.{item.name}",
                            "method",
                            def_start(item),
                            item.end_lineno,
                            cls=node.name,
                        )
                        self._add(meth, [item])
                    else:
                        class_level.append(item)
                self._add(cls, class_level)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for name_node in ast.walk(target):
                        if isinstance(name_node, ast.Name):
                            sym = Symbol(self.name, name_node.id, "var", node.lineno, node.end_lineno)
                            self._add(sym, [node.value] if node.value is not None else [])
                self._collect_router(node)

    def _collect_router(self, node):
        value = node.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, (ast.Name, ast.Attribute))):
            return
        ctor = value.func.id if isinstance(value.func, ast.Name) else value.func.attr
        if ctor not in ("APIRouter", "FastAPI"):
            return
        prefix = ""
        for kw in value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                self.routers[target.id] = prefix

    def _collect_route(self, func):
        for dec in func.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
            ):
                continue
            verb, path = dec.func.attr, None
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = dec.args[0].value
            for kw in dec.keywords:
                if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                    path = kw.value.value
            if not isinstance(path, str):
                continue
            if verb in HTTP_METHODS:
                methods = [verb.upper()]
            elif verb == "api_route":
                methods = ["GET"]
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
            else:
                continue
            for method in methods:
                self.routes.append((method, dec.func.value.id, path, func.name, def_start(func)))

    def symbol_at(self, line):
        """Most specific symbol containing `line` (a method beats its class)."""
        best = None
        for sym in self.symbols.values():
            if sym.start <= line <= sym.end and (best is None or sym.end - sym.start < best.end - best.start):
                best = sym
        return best

    def is_noise(self, line):
        text = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        return not text or text.startswith("#") or line in self.import_lines


class Project:
    def __init__(self, repo, exclude):
        self.repo = repo
        self.modules = {}
        files = git(repo, "ls-files", "-co", "--exclude-standard", "--", "*.py").split()
        for path in files:
            if re.search(exclude, path) or not os.path.isfile(os.path.join(repo, path)):
                continue
            with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as fh:
                source = fh.read()
            try:
                mod = Module(path, source)
            except SyntaxError as exc:
                raise SystemExit(f"cannot parse {path}: {exc}")
            self.modules[mod.name] = mod
        self.symbols = {s.id: s for m in self.modules.values() for s in m.symbols.values()}
        self.methods = {}  # class symbol id -> {method name: method symbol id}
        for sym in self.symbols.values():
            if sym.kind == "method":
                cls_id = f"{sym.module}:{sym.cls}"
                self.methods.setdefault(cls_id, {})[sym.name.split(".", 1)[1]] = sym.id
        self.edges = {sid: self._resolve_refs(sym) for sid, sym in self.symbols.items()}

    def _resolve_name(self, module, name, seen=frozenset()):
        """Resolve `name` in `module`'s namespace -> symbol id, ("module", name) or None."""
        mod = self.modules.get(module)
        if mod is None:
            return None
        if name in mod.symbols:
            return mod.symbols[name].id
        imp = mod.imports.get(name)
        if imp is None:
            return ("module", f"{module}.{name}") if f"{module}.{name}" in self.modules else None
        if imp[0] == "mod":
            return ("module", imp[1]) if imp[1] in self.modules else None
        src, attr = imp[1], imp[2]
        if f"{src}.{attr}" in self.modules:
            return ("module", f"{src}.{attr}")
        if (src, attr) in seen:
            return None
        return self._resolve_name(src, attr, seen | {(src, attr)})

    def _resolve_refs(self, sym):
        out = set()
        for chain in sym.chains:
            if sym.kind == "method" and chain[0] in ("self", "cls"):
                continue
            target = self._resolve_name(sym.module, chain[0])
            for attr in chain[1:] + [None]:
                if isinstance(target, str):
                    out.add(target)
                if attr is None or target is None:
                    break
                if isinstance(target, tuple):
                    target = self._resolve_name(target[1], attr)
                elif target in self.methods and attr in self.methods[target]:
                    target = self.methods[target][attr]
                else:
                    break
        out.discard(sym.id)
        return out

    def reach(self, start):
        """Symbols reachable from `start` -> {symbol id: parent id} (for explaining the path)."""
        parent, attrs, frontier = {start: None}, set(), [start]
        while frontier:
            while frontier:
                sid = frontier.pop()
                attrs |= self.symbols[sid].attrs
                for ref in self.edges[sid]:
                    if ref not in parent:
                        parent[ref] = sid
                        frontier.append(ref)
            for sid in list(parent):  # methods of reached classes whose name is used as an attribute
                for name, meth in self.methods.get(sid, {}).items():
                    if meth not in parent and (name in attrs or name in IMPLICIT_METHODS):
                        parent[meth] = sid
                        frontier.append(meth)
        return parent


def changed_symbols(project, base, changed_files):
    """Map changed lines onto symbol ids."""
    changed = {}  # symbol id -> reason
    tracked = set()
    diff_args = ["diff", "-U0", "--no-color", "--no-ext-diff", base or "HEAD", "--", *changed_files]
    try:
        diff = git(project.repo, *diff_args) if changed_files else ""
    except subprocess.CalledProcessError:
        diff = ""
    current, hunks = None, {}
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line[4:] == "/dev/null" else line[6:]
            if current:
                tracked.add(current)
                hunks.setdefault(current, [])
        elif line.startswith("@@") and current:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            hunks[current].append([int(m.group(1)), int(m.group(2) or 1), []])
        elif line.startswith("-") and not line.startswith("---") and current and hunks[current]:
            hunks[current][-1][2].append(line[1:])

    by_path = {m.path: m for m in project.modules.values()}

    def mark_module(mod, why):
        for sym in mod.symbols.values():
            changed.setdefault(sym.id, why)

    for path in changed_files:
        mod = by_path.get(path)
        if mod is None:
            continue  # deleted, or not a runtime module
        if path not in tracked:
            mark_module(mod, f"new file {path}")  # untracked: every line is new
            continue
        for start, count, removed in hunks[path]:
            if count:
                for n in range(start, start + count):
                    if mod.is_noise(n):
                        continue
                    sym = mod.symbol_at(n)
                    if sym is not None:
                        changed.setdefault(sym.id, f"{path}:{n}")
                    else:
                        mark_module(mod, f"module-level change {path}:{n}")
                continue
            # pure deletion after line `start`
            meaningful = [t for t in removed if t.strip() and not t.strip().startswith("#")]
            if not meaningful or all(re.match(r"\s*(from\s+\S+\s+)?import\s", t) for t in meaningful):
                continue  # only comments or imports removed
            if re.match(r"(async\s+def|def|class)\s|@", meaningful[0]):
                continue  # a whole top-level symbol removed: its callers change too
            sym = mod.symbol_at(start) or mod.symbol_at(start + 1)
            if sym is not None:
                changed.setdefault(sym.id, f"{path}:{start}")
            else:
                mark_module(mod, f"module-level deletion {path}:{start}")
    return changed


def route_regex(path):
    parts = re.split(r"(\{[^}]+\})", normalise(path))
    out = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            out.append(".+" if part.endswith(":path}") else "[^/]+")
        else:
            out.append(re.escape(part))
    return "^" + "".join(out) + "$"


def normalise(path):
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path.rstrip("/") or "/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", default="")
    ap.add_argument("--exclude", required=True)
    ap.add_argument("--state", default="")
    args = ap.parse_args()
    changed_files = [f for f in sys.stdin.read().split("\n") if f]

    project = Project(args.repo, args.exclude)
    changed = changed_symbols(project, args.base, changed_files)

    routes = []
    if changed:
        for mod in project.modules.values():
            for method, router, path, handler, line in mod.routes:
                if router not in mod.routers:
                    continue
                handler_id = mod.symbols[handler].id
                parent = project.reach(handler_id)
                hit = sorted(s for s in parent if s in changed)
                if not hit:
                    continue
                via, cur = [], hit[0]
                while cur is not None:
                    via.append(cur)
                    cur = parent[cur]
                routes.append(
                    {
                        "method": method,
                        "path": mod.routers[router] + path,
                        "handler": handler_id,
                        "file": mod.path,
                        "line": line,
                        "via": list(reversed(via)),
                    }
                )
    routes.sort(key=lambda r: (r["path"], r["method"]))

    checks = []
    if args.state and os.path.isfile(args.state):
        try:
            with open(args.state) as fh:
                checks = json.load(fh).get("checks") or []
        except (OSError, ValueError, AttributeError):
            checks = []
    for route in routes:
        rx = re.compile(route_regex(route["path"]))
        route["covered"] = any(
            c.get("pass") is True
            and str(c.get("method", "")).upper() == route["method"]
            and rx.match(normalise(str(c.get("path", ""))))
            for c in checks
            if isinstance(c, dict)
        )

    max_required = int(os.environ.get("SMOKE_MAX_REQUIRED_ROUTES", DEFAULT_MAX_REQUIRED))
    required = min(len(routes), max_required)
    covered = sum(r["covered"] for r in routes)
    json.dump(
        {
            "changed_symbols": sorted(changed),
            "routes": routes,
            "required": required,
            "covered": covered,
            "ok": covered >= required,
        },
        sys.stdout,
        indent=1,
    )


if __name__ == "__main__":
    main()
