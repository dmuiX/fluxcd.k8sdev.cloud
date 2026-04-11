#!/usr/bin/env python3
"""
organize-flux-yamls.py — Move single-document YAMLs into topic folders.

The script auto-discovers `organize-flux-yamls.yml` mapping files anywhere
inside the repo and applies each one to its own parent directory. Drop a
mapping file next to a pile of flat YAMLs and re-run the script — that's
the whole workflow.

Mapping file format (YAML):

    moves:
      source-file.yml: target/subfolder/NewName.yml
      other-file.yml:  other/path/NewName.yml

Paths on both sides are interpreted relative to the directory containing
the mapping file. Missing source files are skipped silently (so it's
idempotent: once a file has been moved, re-running is a no-op), missing
target parents are created automatically.

File moves use `git mv` when possible so history is preserved, falling
back to a plain filesystem move for untracked files.

Usage:
    organize-flux-yamls.py [--path <dir>] [--dry-run]

All arguments are optional. Defaults:
    --path   repo root (discovered via git) — every mapping file under the
             repo root is processed

Relative paths are resolved against the repo root, not the current working
directory, so the script behaves identically regardless of where it's run
from.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


MAPPING_FILENAME = "organize-flux-yamls.yml"


# ---------------------------------------------------------------------------
# Repo-root-aware path helpers
# ---------------------------------------------------------------------------

def repo_root() -> Path:
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
# Git helpers
# ---------------------------------------------------------------------------

def is_tracked(path: Path, repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", str(path)],
        capture_output=True,
    )
    return result.returncode == 0


def git_mv(src: Path, dst: Path, repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "mv", str(src), str(dst)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Mapping file handling
# ---------------------------------------------------------------------------

def load_mapping(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "moves" not in data:
        raise ValueError(f"{path}: missing top-level 'moves' key")
    moves = data["moves"]
    if not isinstance(moves, dict):
        raise ValueError(f"{path}: 'moves' must be a mapping")
    return moves


def find_in_repo(name: str, repo: Path) -> list[Path]:
    """Search for a file by name anywhere under repo, skipping hidden dirs."""
    results = []
    for hit in repo.rglob(name):
        if any(p.startswith(".") or p == "node_modules" for p in hit.relative_to(repo).parts):
            continue
        if hit.is_file():
            results.append(hit)
    return results


def find_dst_in_repo(dst_rel: str, repo: Path) -> list[Path]:
    """Search for a target path suffix anywhere under repo.

    Matches any file whose repo-relative path ends with the directory
    structure of *dst_rel*. E.g. ``cilium/CiliumLoadBalancerIPPool.yml``
    matches ``infra/config/cilium/CiliumLoadBalancerIPPool.yml``.
    """
    target_parts = Path(dst_rel).parts
    results = []
    for hit in repo.rglob(Path(dst_rel).name):
        if any(p.startswith(".") or p == "node_modules" for p in hit.relative_to(repo).parts):
            continue
        if not hit.is_file():
            continue
        rel_parts = hit.relative_to(repo).parts
        if rel_parts[-len(target_parts):] == target_parts:
            results.append(hit)
    return results


def process_mapping_file(
    mapping_path: Path,
    repo: Path,
    dry_run: bool,
) -> tuple[int, int, int, list[str]]:
    """Apply a single mapping file. Returns (moved, skipped, failed, unmapped).

    Source paths in the mapping are resolved as follows:
      1. Relative to the mapping file's directory (normal case).
      2. If not found there, the repo is searched for a file whose name matches
         the basename of the source entry.  If exactly one hit is found the
         target path is resolved relative to that file's parent directory, so
         the mapping stays clean regardless of where files currently live.
    """
    base = mapping_path.parent
    moves = load_mapping(mapping_path)

    rel_base = base.relative_to(repo) if base.is_relative_to(repo) else base
    print(f"  mapping: {mapping_path.relative_to(repo)}  ({len(moves)} entries, base={rel_base}/)")

    moved = skipped = failed = 0

    for src_rel, dst_rel in moves.items():
        src = base / src_rel
        src_base = base  # directory relative to which the target is resolved

        # --- dynamic source discovery ---
        if not src.exists():
            hits = find_in_repo(Path(src_rel).name, repo)
            if len(hits) == 1:
                src = hits[0]
                src_base = src.parent
            elif len(hits) > 1:
                rel_hits = ", ".join(str(h.relative_to(repo)) for h in hits)
                print(f"    AMBIG {src_rel} (found {len(hits)} candidates: {rel_hits})")
                skipped += 1
                continue
            # len == 0: src stays at original (missing) path

        dst = src_base / dst_rel

        if not src.exists():
            if dst.exists():
                # Already moved on a previous run — idempotent success
                skipped += 1
                continue
            # Target may have ended up at a different base — search by path suffix
            if find_dst_in_repo(dst_rel, repo):
                skipped += 1
                continue
            print(f"    MISS  {src_rel} (source missing, target does not exist either)")
            skipped += 1
            continue

        if dst.exists():
            print(f"    SKIP  {src_rel} -> {dst_rel} (target already exists)")
            skipped += 1
            continue

        src_display = str(src.relative_to(repo))
        dst_display = str(dst.relative_to(repo))

        if dry_run:
            print(f"    MOVE  {src_display} -> {dst_display}")
            moved += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)

        ok = False
        if is_tracked(src, repo):
            ok = git_mv(src, dst, repo)
        if not ok:
            try:
                shutil.move(str(src), str(dst))
                ok = True
            except Exception as e:
                print(f"    FAIL  {src_display} -> {dst_display}  ({e})")
                failed += 1

        if ok:
            print(f"    MOVE  {src_display} -> {dst_display}")
            moved += 1

    # Find unmapped YAML files still at the top level of `base`
    mapped_names = {Path(k).name for k in moves}
    unmapped: list[str] = []
    for f in sorted(base.iterdir()):
        if not f.is_file():
            continue
        if f.suffix not in (".yml", ".yaml"):
            continue
        if f.name == MAPPING_FILENAME:
            continue
        if f.name in mapped_names:
            continue
        unmapped.append(f.name)

    return moved, skipped, failed, unmapped


def find_mapping_files(scan_root: Path) -> list[Path]:
    """Walk scan_root and return every `organize-flux-yamls.yml` found."""
    results: list[Path] = []
    for f in sorted(scan_root.rglob(MAPPING_FILENAME)):
        if any(p.startswith(".") or p == "node_modules" for p in f.parts):
            continue
        results.append(f)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument(
        "--path",
        help="Directory to scan for mapping files (default: repo root)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    scan_root = resolve_repo_path(args.path, root) if args.path else root

    if not scan_root.exists() or not scan_root.is_dir():
        print(f"ERROR: scan path does not exist or is not a directory: {scan_root}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=== DRY RUN — organize-flux-yamls ===")
    else:
        print("=== organize-flux-yamls ===")
    print(f"  Repo root: {root}")
    print(f"  Scan path: {scan_root}")
    print()

    mapping_files = find_mapping_files(scan_root)
    if not mapping_files:
        print(f"  No {MAPPING_FILENAME} files found under scan path — nothing to do.")
        return

    total_moved = total_skipped = total_failed = 0
    all_unmapped: list[tuple[Path, list[str]]] = []

    for mapping_file in mapping_files:
        moved, skipped, failed, unmapped = process_mapping_file(
            mapping_file, root, args.dry_run
        )
        total_moved += moved
        total_skipped += skipped
        total_failed += failed
        if unmapped:
            all_unmapped.append((mapping_file.parent, unmapped))
        print()

    print(f"  Totals: {total_moved} moved, {total_skipped} skipped, {total_failed} failed")

    if all_unmapped:
        print()
        print("  Unmapped YAML files still at top level of mapping dirs:")
        for base, files in all_unmapped:
            rel = base.relative_to(root) if base.is_relative_to(root) else base
            print(f"    {rel}/:")
            for f in files:
                print(f"      - {f}")

    if total_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
