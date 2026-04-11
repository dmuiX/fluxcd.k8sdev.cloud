#!/usr/bin/env python3
"""
split-flux-yamls.py — Split multi-document Flux YAML files into per-Kind files.

Walks the whole repository (auto-discovered via git) and splits every
multi-document YAML file it finds into one file per Kubernetes object.
Single-document files are left alone, so the script is safe to re-run
(idempotent).

For each multi-doc file a sub-folder is created next to it, named after the
chart in the contained HelmRelease — or, if there's no HelmRelease, after
the original filename stem. Within that folder:

  * a Kind that appears exactly once becomes `Kind.yml`
  * a Kind that appears multiple times becomes `Kind-<name>.yml` for every
    instance (consistent naming across siblings)
  * documents without `metadata.name` fall back to `Kind-1.yml`, `Kind-2.yml`

HelmRelease files get a yaml-language-server schema comment injected at the
top pointing to the matching values schema in `<repo>/values-schemas/`.

Usage:
    split-flux-yamls.py [--path <dir>] [--schema-dir <path>] [--dry-run]

All arguments are optional. Defaults:
    --path        repo root (discovered via git)
    --schema-dir  <repo>/values-schemas

Relative paths are resolved against the repo root, not the current working
directory, so the script behaves identically regardless of where it's run
from.
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

def repo_root() -> Path:
    """Discover the git repo root, anchored to this script's location.

    Using the script's directory (not CWD) means the script works regardless
    of where it is invoked from.
    """
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
    """Resolve a user-supplied path. Absolute paths are kept; relative paths
    are anchored to the repo root rather than the current working directory."""
    p = Path(arg)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


# ---------------------------------------------------------------------------
# Filename logic
# ---------------------------------------------------------------------------

def deduplicate_filenames(docs):
    """Assign unique filenames to each document.

    Two-pass so the result is stable:
      1. Count how often each Kind appears.
      2. If a Kind appears exactly once, use `Kind.yml`. Otherwise use
         `Kind-<metadata.name>.yml` for every instance. Documents without
         a name fall back to a numeric suffix.
    """
    kind_counts = {}
    for doc in docs:
        kind = doc.get("kind", "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    filenames = []
    fallback_counters = {}
    for doc in docs:
        kind = doc.get("kind", "unknown")
        name = doc.get("metadata", {}).get("name", "")

        if kind_counts[kind] == 1:
            fname = f"{kind}.yml"
        elif name:
            fname = f"{kind}-{name}.yml"
        else:
            fallback_counters[kind] = fallback_counters.get(kind, 0) + 1
            fname = f"{kind}-{fallback_counters[kind]}.yml"

        filenames.append(fname)

    return filenames


def doc_to_yaml(doc):
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)


def get_chart_name_from_helmrelease(doc):
    chart = doc.get("spec", {}).get("chart", {}).get("spec", {}).get("chart")
    if chart:
        return chart
    name = doc.get("metadata", {}).get("name", "")
    return name or doc.get("spec", {}).get("releaseName", "unknown")


def get_schema_comment(outpath: Path, chart_name: str, schema_dir: Path):
    """Build the yaml-language-server schema comment line."""
    if not schema_dir:
        return None
    schema_file = schema_dir / chart_name / "helmrelease.schema.json"
    if not schema_file.exists():
        schema_file = schema_dir / chart_name / "values.schema.json"
        if not schema_file.exists():
            return None
    rel = os.path.relpath(schema_file, outpath.parent)
    return f"# yaml-language-server: $schema={rel}"


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_file(filepath: Path, schema_dir: Path | None, dry_run: bool, rel_path: Path | None = None) -> bool:
    """Split one multi-document YAML file. Returns True when a split happened."""
    with open(filepath) as f:
        content = f.read()

    try:
        docs = list(yaml.safe_load_all(content))
    except Exception as e:
        print(f"  SKIP  {filepath} — parse error: {e}")
        return False

    docs = [d for d in docs if d and isinstance(d, dict)]
    if len(docs) <= 1:
        return False  # single-doc file — nothing to split, silent skip

    # Folder name: chart from HelmRelease, or file stem as fallback
    chart_name = None
    folder_name = None
    for doc in docs:
        if doc.get("kind") == "HelmRelease":
            chart_name = doc.get("spec", {}).get("chart", {}).get("spec", {}).get("chart")
            folder_name = get_chart_name_from_helmrelease(doc)
            break
    if not folder_name:
        folder_name = filepath.stem

    output_dir = filepath.parent / folder_name
    filenames = deduplicate_filenames(docs)

    if dry_run:
        print(f"  SPLIT {rel_path or filepath} -> {output_dir.name}/")
        for doc, fname in zip(docs, filenames):
            kind = doc.get("kind", "unknown")
            extra = ""
            if kind == "HelmRelease" and schema_dir and chart_name:
                dummy = output_dir / fname
                if get_schema_comment(dummy, chart_name, schema_dir):
                    extra = f"  (+ schema: {chart_name})"
            print(f"    -> {fname}{extra}")
        return True

    if output_dir.exists():
        print(f"  WARN  {output_dir} already exists — writing into existing folder")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Stage the original as a backup before writing the split files so we can
    # roll back if something goes wrong halfway through.
    backup = filepath.with_suffix(filepath.suffix + ".splitting")
    filepath.rename(backup)

    try:
        written = []
        for doc, fname in zip(docs, filenames):
            kind = doc.get("kind", "unknown")
            outpath = output_dir / fname

            with open(outpath, "w") as f:
                if kind == "HelmRelease" and schema_dir and chart_name:
                    comment = get_schema_comment(outpath, chart_name, schema_dir)
                    if comment:
                        f.write(f"{comment}\n")
                f.write("---\n")
                f.write(doc_to_yaml(doc))
            written.append(fname)

        backup.unlink()

        print(f"  SPLIT {filepath} -> {output_dir.name}/")
        for w in written:
            print(f"    -> {w}")
        return True

    except Exception as e:
        print(f"  FAIL  {filepath} — write error: {e}")
        if backup.exists():
            backup.rename(filepath)
            print("        Original restored from backup")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument(
        "--path",
        help="Directory to scan (default: repo root)",
    )
    parser.add_argument(
        "--schema-dir",
        help="Directory with values schemas (default: <repo>/values-schemas)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    scan_root = resolve_repo_path(args.path, root) if args.path else root
    schema_dir = (
        resolve_repo_path(args.schema_dir, root)
        if args.schema_dir
        else (root / "values-schemas")
    )

    if args.dry_run:
        print("=== DRY RUN — split-flux-yamls ===")
    else:
        print("=== split-flux-yamls ===")
    print(f"  Repo root:  {root}")
    print(f"  Scan path:  {scan_root}")
    if schema_dir.exists():
        print(f"  Schema dir: {schema_dir}")
    else:
        print(f"  Schema dir: {schema_dir} (not present — no schema comments will be injected)")
        schema_dir = None
    print()

    if not scan_root.exists() or not scan_root.is_dir():
        print(f"ERROR: scan path does not exist or is not a directory: {scan_root}", file=sys.stderr)
        sys.exit(1)

    schema_dir_resolved = schema_dir.resolve() if schema_dir else None

    split_count = 0
    scanned = 0
    for ext in ("*.yaml", "*.yml"):
        for filepath in sorted(scan_root.rglob(ext)):
            # Skip hidden dirs, node_modules, and .git
            if any(p.startswith(".") or p == "node_modules" for p in filepath.parts):
                continue
            # Skip files inside the schema output dir
            if schema_dir_resolved and (
                schema_dir_resolved in filepath.resolve().parents
                or filepath.resolve() == schema_dir_resolved
            ):
                continue
            scanned += 1
            if process_file(filepath, schema_dir, args.dry_run, rel_path=filepath.relative_to(root)):
                split_count += 1

    print()
    print(f"  {scanned} files scanned, {split_count} split.")
    if not args.dry_run and split_count > 0:
        print()
        print("  Don't forget to re-run fetch-and-patch-helm-schemas.py if you")
        print("  want schema mappings updated for the new HelmRelease locations.")


if __name__ == "__main__":
    main()
