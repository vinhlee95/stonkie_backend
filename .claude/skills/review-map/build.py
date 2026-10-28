"""Build an interactive review map for a PR: spec (boxes -> diff hunks / functions / tests) + diff -> one HTML page.

python3 -I build.py hunks --diff pr.diff
    List every hunk per file with its index, for writing a spec's ("hunk", path, [i], label) refs.

python3 -I build.py page --repo . --ref origin/<head-branch> --diff pr.diff --spec spec.py --out map.html
    Write the page. See SKILL.md for the spec format; example_spec.py is a full example.
"""

import argparse
import ast
import json
import os
import re
import runpy
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
_CLT_GIT = "/Library/Developer/CommandLineTools/usr/bin/git"  # /usr/bin/git can be blocked by the Xcode licence
GIT = os.environ.get("GIT") or (_CLT_GIT if os.path.exists(_CLT_GIT) else "git")


def parse_diff(text: str) -> dict[str, list[dict]]:
    files: dict[str, list[dict]] = {}
    hunks = None
    hunk = None
    old = new = 0
    for line in text.split("\n"):
        if line.startswith("diff --git"):
            hunks = files.setdefault(line.split(" b/", 1)[1], [])
            hunk = None
            continue
        if hunks is None:
            continue
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
            old, new = int(m[1]), int(m[2])
            hunk = {"ctx": m[3].strip(), "start": new, "lines": []}
            hunks.append(hunk)
            continue
        if hunk is None or not line or line.startswith("\\"):
            continue
        sign, body = line[0], line[1:]
        if sign == "+":
            hunk["lines"].append({"t": "+", "o": None, "n": new, "s": body})
            new += 1
        elif sign == "-":
            hunk["lines"].append({"t": "-", "o": old, "n": None, "s": body})
            old += 1
        else:
            hunk["lines"].append({"t": " ", "o": old, "n": new, "s": body})
            old += 1
            new += 1
    return files


def list_hunks(diff: dict[str, list[dict]]) -> None:
    for path, hunks in diff.items():
        print(path)
        for i, h in enumerate(hunks):
            adds = sum(line["t"] == "+" for line in h["lines"])
            dels = sum(line["t"] == "-" for line in h["lines"])
            print(f"  [{i}] +{adds} -{dels}  new line {h['start']}  {h['ctx']}")


class Builder:
    def __init__(self, repo: str, ref: str, diff: dict[str, list[dict]]):
        self.repo, self.ref, self.diff = repo, ref, diff
        self._sources: dict[str, str] = {}
        self.used_hunks: set[tuple[str, int]] = set()

    def source(self, path: str) -> str:
        if path not in self._sources:
            self._sources[path] = subprocess.run(
                [GIT, "-C", self.repo, "show", f"{self.ref}:{path}"], check=True, capture_output=True, text=True
            ).stdout
        return self._sources[path]

    def def_blocks(self, path: str, names: list[str], label: str | None, sign: str) -> list[dict]:
        """Top-level Python defs/classes/assignments by name; contiguous ones merged; leading comments kept."""
        src = self.source(path)
        lines = src.split("\n")
        ranges = []
        for node in ast.parse(src).body:
            name = _top_level_name(node)
            if name not in names:
                continue
            start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            while start > 1 and lines[start - 2].lstrip().startswith("#"):
                start -= 1
            ranges.append([start, node.end_lineno, [name]])
        missing = set(names) - {n for r in ranges for n in r[2]}
        if missing:
            raise SystemExit(f"{path}: top-level names not found at {self.ref}: {sorted(missing)}")
        merged: list[list] = []
        for r in sorted(ranges):
            if merged and r[0] - merged[-1][1] <= 3:
                merged[-1][1] = r[1]
                merged[-1][2] += r[2]
            else:
                merged.append(r)
        return [
            {"file": path, "label": label or ", ".join(found), "lines": self._lines(lines, start, end, sign)}
            for start, end, found in merged
        ]

    def line_block(self, path: str, start: int, end: int, label: str, sign: str = " ") -> dict:
        """Lines of the head version, any language: unchanged (" ") or all added ("+", for new files)."""
        return {"file": path, "label": label, "lines": self._lines(self.source(path).split("\n"), start, end, sign)}

    @staticmethod
    def _lines(lines: list[str], start: int, end: int, sign: str) -> list[dict]:
        return [{"t": sign, "o": None if sign == "+" else i, "n": i, "s": lines[i - 1]} for i in range(start, end + 1)]

    def hunk_block(self, path: str, indexes: list[int], label: str) -> dict:
        hunks = self.diff.get(path)
        if hunks is None:
            raise SystemExit(f"{path}: not in the diff")
        lines: list[dict] = []
        for i, index in enumerate(indexes):
            if index >= len(hunks):
                raise SystemExit(f"{path}: no hunk [{index}] (has {len(hunks)}); run `build.py hunks`")
            if i:
                lines.append({"t": "gap", "o": None, "n": None, "s": ""})
            lines.extend(hunks[index]["lines"])
            self.used_hunks.add((path, index))
        return {"file": path, "label": label, "lines": lines}

    def blocks(self, refs: list[tuple]) -> list[dict]:
        out = []
        for ref in refs:
            kind = ref[0]
            if kind == "hunk":  # ("hunk", path, [indexes], label)
                out.append(self.hunk_block(ref[1], ref[2], ref[3]))
            elif kind == "new":  # ("new", path, [names], label?) Python top-level defs added whole by the PR
                out += self.def_blocks(ref[1], ref[2], ref[3] if len(ref) > 3 else None, "+")
            elif kind == "ctx":  # ("ctx", path, [names], label?) unchanged Python defs, for context
                out += self.def_blocks(ref[1], ref[2], ref[3] if len(ref) > 3 else None, " ")
            elif kind == "lines":  # ("lines", path, start, end, label, sign?) any language; sign "+" in a new file
                out.append(self.line_block(*ref[1:6]))
            else:
                raise SystemExit(f"unknown ref kind {kind!r} in {ref}")
        return out


def _top_level_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _check_ids(spec: dict, ids: set[str]) -> None:
    used = [x for flow in spec["FLOWS"] for fr in flow["frames"] for row in fr["rows"] for x in row]
    used += [
        x for flow in spec["FLOWS"] for fr in flow["frames"] for kids in fr.get("children", {}).values() for x in kids
    ]
    used += spec["TOUR"] + [c["node"] for c in spec["PR"].get("causes", [])]
    unknown = sorted(set(used) - ids)
    if unknown:
        raise SystemExit(f"spec refers to unknown node ids: {unknown}")
    unplaced = sorted(ids - set(used))
    if unplaced:
        print(f"warning: nodes not on the map: {unplaced}")


def _check_coverage(builder: Builder, nodes: list[dict]) -> None:
    """Warn about hunks no box shows. A hunk counts as shown if a "new" block covers its added lines."""
    shown = {
        (block["file"], line["n"])
        for node in nodes
        for block in node["code"] + node["tests"]
        for line in block["lines"]
        if line["t"] == "+"
    }
    for path, hunks in builder.diff.items():
        for i, hunk in enumerate(hunks):
            if (path, i) in builder.used_hunks:
                continue
            added = [line for line in hunk["lines"] if line["t"] == "+" and line["s"].strip()]
            missing = [line for line in added if (path, line["n"]) not in shown]
            if missing or not any(line["t"] == "+" for line in hunk["lines"]):
                first = f": {missing[0]['n']} {missing[0]['s'].strip()[:60]}" if missing else ""
                print(f"warning: {path} hunk [{i}] not shown ({len(missing)} added lines missing{first})")


def build_page(args: argparse.Namespace) -> None:
    spec = runpy.run_path(args.spec)
    diff_text = Path(args.diff).read_text()
    builder = Builder(args.repo, args.ref, parse_diff(diff_text))
    nodes = []
    for node in spec["NODES"]:
        node = {"note": None, "checks": [], "asks": [], **node}
        node["code"] = builder.blocks(node.pop("code_refs", []))
        node["tests"] = builder.blocks(node.pop("test_refs", []))
        seen: set[tuple] = set()
        for block in node["code"]:
            for line in block["lines"]:
                seen.add((block["file"], line["t"], line["o"], line["n"]))
        node["adds"] = sum(key[1] == "+" for key in seen)
        node["dels"] = sum(key[1] == "-" for key in seen)
        nodes.append(node)
    _check_ids(spec, {n["id"] for n in nodes})
    _check_coverage(builder, nodes)

    pr = {"results": [], "causes": [], "asks": [], **spec["PR"]}
    data = {**pr, "nodes": nodes, "flows": spec["FLOWS"], "tour": spec["TOUR"], "diff": diff_text}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (HERE / "template.html").read_text()
    html = html.replace("__TITLE__", f"Review Map · PR {pr['number']}").replace("/*__DATA__*/null", payload)
    Path(args.out).write_text(html)
    print(f"wrote {args.out}: {len(nodes)} nodes, {len(html) // 1024} KiB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    hunks = sub.add_parser("hunks", help="list hunks per file")
    hunks.add_argument("--diff", required=True)
    page = sub.add_parser("page", help="build the HTML page")
    page.add_argument("--repo", default=".")
    page.add_argument("--ref", required=True, help="git ref of the PR head, e.g. origin/fix/foo")
    page.add_argument("--diff", required=True)
    page.add_argument("--spec", required=True)
    page.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.cmd == "hunks":
        list_hunks(parse_diff(Path(args.diff).read_text()))
    else:
        build_page(args)


if __name__ == "__main__":
    main()
