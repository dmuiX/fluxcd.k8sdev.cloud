#!/usr/bin/env python3
"""
split-flux-yamls.py - Split multi-document Flux YAML files into separate files.

Takes all .yaml/.yml files in a directory, splits them by document,
and organizes them into folders by chart/app name.

Automatically injects yaml-language-server schema comments into
HelmRelease files, pointing to the matching values schema.

Before:
  infra/controller/cert-manager.yml  (Namespace + HelmRepo + HelmRelease)

After:
  infra/controller/cert-manager/Namespace.yml
  infra/controller/cert-manager/HelmRepository.yml
  infra/controller/cert-manager/HelmRelease.yml  (with schema comment)

Usage: python3 split-flux-yamls.py <directory> [--schema-dir <path>] [--dry-run]
"""

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def deduplicate_filenames(docs):
    """Assign unique filenames to each document, appending name or counter on collision."""
    filenames = []
    seen = {}

    for doc in docs:
        kind = doc.get("kind", "unknown")
        name = doc.get("metadata", {}).get("name", "")

        if kind not in seen:
            fname = f"{kind}.yml"
            seen[kind] = 1
        else:
            # Collision: use resource name if available, otherwise counter
            if name:
                fname = f"{kind}-{name}.yml"
            else:
                seen[kind] += 1
                fname = f"{kind}-{seen[kind]}.yml"

        filenames.append(fname)

    return filenames


def doc_to_yaml(doc):
    """Serialize a document back to YAML string."""
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)


def get_chart_name_from_helmrelease(doc):
    """Extract chart name from a HelmRelease document."""
    chart = doc.get("spec", {}).get("chart", {}).get("spec", {}).get("chart")
    if chart:
        return chart
    name = doc.get("metadata", {}).get("name", "")
    return name or doc.get("spec", {}).get("releaseName", "unknown")


def get_schema_comment(outpath, chart_name, schema_dir):
    """Build the yaml-language-server schema comment with relative path."""
    if not schema_dir:
        return None

    schema_file = schema_dir / chart_name / "helmrelease.schema.json"
    if not schema_file.exists():
        schema_file = schema_dir / chart_name / "values.schema.json"
        if not schema_file.exists():
            return None

    rel_path = os.path.relpath(schema_file, outpath.parent)
    return f"# yaml-language-server: $schema={rel_path}"


def process_file(filepath, schema_dir=None, dry_run=False):
    """Split a multi-document YAML file into separate files."""
    with open(filepath) as f:
        content = f.read()

    try:
        docs = list(yaml.safe_load_all(content))
    except Exception as e:
        print(f"  SKIP  {filepath} - parse error: {e}")
        return False

    # Filter out empty docs
    docs = [d for d in docs if d and isinstance(d, dict)]

    if len(docs) <= 1:
        print(f"  SKIP  {filepath} - single document, no split needed")
        return False

    # Extract chart name from HelmRelease for schema lookup
    chart_name = None
    for doc in docs:
        if doc.get("kind") == "HelmRelease":
            chart_name = doc.get("spec", {}).get("chart", {}).get("spec", {}).get("chart")
            break

    # Determine folder name from HelmRelease or fall back to file stem
    folder_name = None
    for doc in docs:
        if doc.get("kind") == "HelmRelease":
            folder_name = get_chart_name_from_helmrelease(doc)
            break
    if not folder_name:
        folder_name = filepath.stem

    output_dir = filepath.parent / folder_name
    filenames = deduplicate_filenames(docs)

    if dry_run:
        print(f"\n  Would create: {output_dir}/")
        if output_dir.exists():
            print(f"        WARN  directory already exists, files may be mixed in")
        for doc, fname in zip(docs, filenames):
            kind = doc.get("kind", "unknown")
            extra = ""
            if kind == "HelmRelease" and schema_dir and chart_name:
                dummy_path = output_dir / fname
                comment = get_schema_comment(dummy_path, chart_name, schema_dir)
                if comment:
                    extra = f"  (+ schema: {chart_name})"
            print(f"    -> {fname}{extra}")
        return True

    # Warn if output dir already exists
    if output_dir.exists():
        print(f"        WARN  {output_dir} already exists, files may be mixed in")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Safe delete: rename original before writing splits
    backup_path = filepath.with_suffix(filepath.suffix + ".splitting")
    filepath.rename(backup_path)

    try:
        # Write each document to its own file
        written = []
        for doc, fname in zip(docs, filenames):
            kind = doc.get("kind", "unknown")
            outpath = output_dir / fname

            with open(outpath, "w") as f:
                # Inject schema comment for HelmRelease files
                if kind == "HelmRelease" and schema_dir and chart_name:
                    comment = get_schema_comment(outpath, chart_name, schema_dir)
                    if comment:
                        f.write(f"{comment}\n")

                f.write("---\n")
                f.write(doc_to_yaml(doc))

            written.append(fname)

        # All writes succeeded, remove the backup
        backup_path.unlink()

        print(f"  SPLIT {filepath} -> {output_dir}/")
        for w in written:
            print(f"    -> {w}")

    except Exception as e:
        # Restore original file on failure
        print(f"  FAIL  {filepath} - write error: {e}")
        if backup_path.exists():
            backup_path.rename(filepath)
            print(f"        Original restored from backup")
        return False

    return True


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory> [--schema-dir <path>] [--dry-run]")
        sys.exit(1)

    target = Path(sys.argv[1]).resolve()
    dry_run = "--dry-run" in sys.argv

    # Parse --schema-dir
    schema_dir = None
    if "--schema-dir" in sys.argv:
        idx = sys.argv.index("--schema-dir")
        if idx + 1 < len(sys.argv):
            schema_dir = Path(sys.argv[idx + 1]).resolve()

    if dry_run:
        print("=== DRY RUN — no files will be changed ===\n")
    else:
        print("=== Splitting multi-document YAML files ===\n")

    if schema_dir:
        if not schema_dir.exists():
            print(f"  WARN  Schema dir does not exist: {schema_dir}")
            print(f"        Schema comments will not be injected.\n")
        else:
            print(f"  Schema dir: {schema_dir}\n")

    schema_dir_resolved = schema_dir.resolve() if schema_dir else None

    split_count = 0
    for ext in ("*.yaml", "*.yml"):
        for filepath in sorted(target.rglob(ext)):
            # Skip hidden dirs and node_modules
            if any(p.startswith(".") or p == "node_modules" for p in filepath.parts):
                continue
            # Skip files inside the schema output dir
            if schema_dir_resolved and (
                schema_dir_resolved in filepath.resolve().parents
                or filepath.resolve() == schema_dir_resolved
            ):
                continue
            if process_file(filepath, schema_dir, dry_run):
                split_count += 1

    print(f"\n  Split {split_count} files.")
    if not dry_run and split_count > 0:
        print("\n  Don't forget to update your Kustomization files if you use them!")
        print("  And re-run fetch-and-patch-helm-schemas.py to update schema mappings.")


if __name__ == "__main__":
    main()