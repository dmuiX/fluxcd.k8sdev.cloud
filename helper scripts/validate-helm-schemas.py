#!/usr/bin/env python3
"""
validate-helm-schemas.py — Validate HelmReleases against generated schemas.

Walks the whole repository, finds every HelmRelease, loads the matching
values schema from `<repo>/values-schemas/<chart>/values.schema.json`, and
prints any validation errors in `file:line: error: …` format so they can
be surfaced by the VS Code problem matcher.

Requires: pip install pyyaml jsonschema

Usage:
    validate-helm-schemas.py [--path <dir>] [--schema-dir <path>]

All arguments are optional. Defaults:
    --path        repo root (discovered via git)
    --schema-dir  <repo>/values-schemas

Relative paths are resolved against the repo root, not the current working
directory, so the script works from any CWD.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import jsonschema
except ImportError:
    print("jsonschema required. Install with: pip install jsonschema", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Repo-root-aware path helpers
# ---------------------------------------------------------------------------

def discover_repo_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(script_dir), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: could not determine git repo root: {e}", file=sys.stderr)
        sys.exit(1)
    return Path(out.stdout.strip())


def resolve_repo_path(arg: str, root: Path) -> Path:
    p = Path(arg)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


# ---------------------------------------------------------------------------
# YAML scan
# ---------------------------------------------------------------------------

def find_yaml_docs(repo_root, schema_dir):
    """Yield (doc, filepath) for all YAML documents."""
    schema_dir_resolved = schema_dir.resolve()
    for ext in ("*.yaml", "*.yml"):
        for yaml_file in repo_root.rglob(ext):
            if any(p.startswith(".") or p == "node_modules" for p in yaml_file.parts):
                continue
            if schema_dir_resolved in yaml_file.resolve().parents or yaml_file.resolve() == schema_dir_resolved:
                continue
            try:
                with open(yaml_file) as f:
                    for doc in yaml.safe_load_all(f):
                        if doc and isinstance(doc, dict):
                            yield doc, yaml_file
            except Exception:
                continue


def find_helm_releases(repo_root, schema_dir):
    releases = []
    for doc, filepath in find_yaml_docs(repo_root, schema_dir):
        if doc.get("kind") == "HelmRelease":
            chart_name = doc.get("spec", {}).get("chart", {}).get("spec", {}).get("chart")
            if chart_name:
                releases.append((chart_name, filepath))
    return releases


def find_yaml_line(root_node, json_path_parts):
    """Walk a PyYAML composed node tree to find the line number for a JSON path."""
    node = root_node
    for part in json_path_parts:
        if node is None:
            return None
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if key_node.value == part:
                    node = value_node
                    break
            else:
                return None
        elif isinstance(node, yaml.SequenceNode):
            try:
                idx = int(part)
                if 0 <= idx < len(node.value):
                    node = node.value[idx]
                else:
                    return None
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node.start_mark.line + 1 if node and node.start_mark else None


def validate(repo_root, schema_dir):
    releases = find_helm_releases(repo_root, schema_dir)
    error_count = 0
    validated = 0

    for chart_name, hr_file in releases:
        schema_path = schema_dir / chart_name / "values.schema.json"
        if not schema_path.exists():
            continue

        with open(schema_path) as f:
            values_schema = json.load(f)

        with open(hr_file) as f:
            raw = f.read()

        try:
            root_node = yaml.compose(raw)
        except Exception:
            root_node = None

        try:
            docs = list(yaml.safe_load_all(raw))
        except Exception as e:
            rel = hr_file.relative_to(repo_root)
            print(f"{rel}:1: error: YAML parse error: {e}")
            error_count += 1
            continue

        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "HelmRelease":
                continue
            values = doc.get("spec", {}).get("values")
            if values is None:
                continue

            validated += 1
            rel = hr_file.relative_to(repo_root)
            validator = jsonschema.Draft7Validator(values_schema)

            for error in sorted(validator.iter_errors(values), key=lambda e: list(e.path)):
                error_count += 1
                json_path = ".".join(str(p) for p in error.absolute_path)
                path_display = f"spec.values.{json_path}" if json_path else "spec.values"

                line = None
                if root_node:
                    lookup = ["spec", "values"] + [str(p) for p in error.absolute_path]
                    line = find_yaml_line(root_node, lookup)

                print(f"{rel}:{line or 1}: error: {path_display}: {error.message}")

    if error_count == 0:
        print(f"All {validated} HelmReleases valid!", file=sys.stderr)
    else:
        print(f"\n{error_count} error(s) in {validated} HelmRelease(s)", file=sys.stderr)

    return error_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument("--path", help="Repo scan path (default: repo root)")
    parser.add_argument(
        "--schema-dir", help="Directory with values schemas (default: <repo>/values-schemas)"
    )
    args = parser.parse_args()

    root = discover_repo_root()
    scan_root = resolve_repo_path(args.path, root) if args.path else root
    schema_dir = (
        resolve_repo_path(args.schema_dir, root)
        if args.schema_dir
        else (root / "values-schemas")
    )

    if not schema_dir.exists():
        print(f"Schema dir not found: {schema_dir}", file=sys.stderr)
        print("Run fetch-and-patch-helm-schemas.py first.", file=sys.stderr)
        sys.exit(1)

    error_count = validate(scan_root, schema_dir)
    sys.exit(1 if error_count > 0 else 0)


if __name__ == "__main__":
    main()
