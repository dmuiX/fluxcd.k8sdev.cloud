#!/usr/bin/env python3
"""
inject-schema-comments.py — Add yaml-language-server schema headers.

Scans the whole repo for HelmRelease files, finds the matching values
schema, and injects a `# yaml-language-server: $schema=...` comment at
the top of the file. Existing comments are updated in place, so the
script is idempotent and safe to re-run.

Usage:
    inject-schema-comments.py [--path <dir>] [--schema-dir <path>] [--dry-run]

All arguments are optional. Defaults:
    --path        repo root (discovered via git)
    --schema-dir  <repo>/values-schemas

Relative paths are resolved against the repo root, not the current working
directory, so the script works from any CWD.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
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
# HelmRelease discovery
# ---------------------------------------------------------------------------

def find_helmrelease_files(repo_root, schema_dir):
    """Find all files containing a HelmRelease document."""
    schema_dir_resolved = schema_dir.resolve()
    results = []
    for ext in ("*.yaml", "*.yml"):
        for filepath in repo_root.rglob(ext):
            if any(p.startswith(".") or p == "node_modules" for p in filepath.parts):
                continue
            if schema_dir_resolved in filepath.resolve().parents or filepath.resolve() == schema_dir_resolved:
                continue
            try:
                with open(filepath) as f:
                    docs = list(yaml.safe_load_all(f))

                hr_index = None
                chart = None
                for i, doc in enumerate(docs):
                    if doc and isinstance(doc, dict) and doc.get("kind") == "HelmRelease":
                        chart = doc.get("spec", {}).get("chart", {}).get("spec", {}).get("chart")
                        hr_index = i
                        break

                if chart and hr_index is not None:
                    results.append((filepath, chart, hr_index, len(docs)))
            except Exception:
                continue
    return results


def inject_comment(filepath, chart_name, schema_dir, hr_index, doc_count, dry_run=False):
    """Inject or update the schema comment in a file."""
    schema_file = schema_dir / chart_name / "helmrelease.schema.json"
    if not schema_file.exists():
        schema_file = schema_dir / chart_name / "values.schema.json"
    if not schema_file.exists():
        return False, "no schema found", None

    rel_path = os.path.relpath(schema_file, filepath.parent)
    comment = f"# yaml-language-server: $schema={rel_path}"

    warning = None
    if doc_count > 1 and hr_index > 0:
        warning = f"HelmRelease is document #{hr_index + 1} of {doc_count} — schema comment only applies to first document"

    with open(filepath) as f:
        content = f.read()

    had_trailing_newline = content.endswith("\n")
    lines = content.split("\n")

    if lines and lines[0].startswith("# yaml-language-server: $schema="):
        if lines[0] == comment:
            return False, "already correct", warning
        if dry_run:
            return True, f"would update -> {rel_path}", warning
        lines[0] = comment
    else:
        if dry_run:
            return True, f"would inject -> {rel_path}", warning
        lines.insert(0, comment)

    output = "\n".join(lines)
    if had_trailing_newline and not output.endswith("\n"):
        output += "\n"

    with open(filepath, "w") as f:
        f.write(output)

    return True, rel_path, warning


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument("--path", help="Repo scan path (default: repo root)")
    parser.add_argument(
        "--schema-dir", help="Directory with values schemas (default: <repo>/values-schemas)"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = discover_repo_root()
    scan_root = resolve_repo_path(args.path, root) if args.path else root
    schema_dir = (
        resolve_repo_path(args.schema_dir, root)
        if args.schema_dir
        else (root / "values-schemas")
    )

    if not schema_dir.exists():
        print(f"Error: schema directory does not exist: {schema_dir}", file=sys.stderr)
        print("Run fetch-and-patch-helm-schemas.py first to generate schemas.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=== DRY RUN — inject-schema-comments ===\n")
    else:
        print("=== inject-schema-comments ===\n")

    helmreleases = find_helmrelease_files(scan_root, schema_dir)
    print(f"Found {len(helmreleases)} HelmRelease files\n")

    injected = 0
    for filepath, chart_name, hr_index, doc_count in helmreleases:
        changed, msg, warning = inject_comment(
            filepath, chart_name, schema_dir, hr_index, doc_count, args.dry_run
        )
        rel = filepath.relative_to(scan_root)
        if changed:
            print(f"  OK    {rel} ({chart_name}) -> {msg}")
            injected += 1
        else:
            print(f"  SKIP  {rel} ({chart_name}) — {msg}")
        if warning:
            print(f"        WARN  {warning}")

    print(f"\n  {'Would inject' if args.dry_run else 'Injected'}: {injected}")


if __name__ == "__main__":
    main()
