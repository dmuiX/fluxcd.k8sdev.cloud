#!/usr/bin/env python3
"""
fetch-crds.py — Fetch CRD manifests from upstream GitHub sources.

For each operator defined in CRD_SOURCES, reads the chart version from
the matching HelmRelease in the repo, fetches the CRD YAML from GitHub,
and writes one file per CustomResourceDefinition to infra/crds/<operator>/.

Usage:
    fetch-crds.py [--dry-run] [--crds-dir PATH]

All arguments are optional. Defaults:
    --crds-dir  <repo-root>/infra/crds

Relative paths are resolved against the repo root, not the current working
directory, so the script behaves identically regardless of where it's run from.

Set GITHUB_TOKEN env var to avoid API rate limits (60 req/h unauthenticated).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# PyYAML SafeLoader doesn't handle the YAML 1.1 "value" tag (= used as a
# literal scalar, e.g. `- =` in Alertmanager CRDs).  Teach it to treat it as
# the plain string "=".
yaml.SafeLoader.add_constructor(
    "tag:yaml.org,2002:value",
    lambda loader, node: loader.construct_scalar(node),
)


# ---------------------------------------------------------------------------
# CRD source definitions
#
# strategy "raw"        — fetch a single (possibly multi-doc) YAML from a URL.
# strategy "github-dir" — list a GitHub directory via API, fetch each .yaml.
#
# {version} is substituted with the chart version read from the HelmRelease.
# ---------------------------------------------------------------------------

CRD_SOURCES = {
    "cilium": {
        "strategy": "github-dir",
        "repo": "cilium/cilium",
        # Cilium ≥ 1.14 moved from v1/ to v2/ + v2alpha1/; use multiple paths.
        "paths": [
            "pkg/k8s/apis/cilium.io/client/crds/v2",
            "pkg/k8s/apis/cilium.io/client/crds/v2alpha1",
        ],
        "ref": "v{version}",
    },
    "external-secrets": {
        "strategy": "raw",
        "url": "https://raw.githubusercontent.com/external-secrets/external-secrets/v{version}/deploy/crds/bundle.yaml",
    },
    "k8up": {
        "strategy": "raw",
        # Release tag format is "k8up-{version}", not "v{version}"
        "url": "https://github.com/k8up-io/k8up/releases/download/k8up-{version}/k8up-crd.yaml",
    },
    "kube-prometheus-stack": {
        "strategy": "github-dir",
        "repo": "prometheus-community/helm-charts",
        "path": "charts/kube-prometheus-stack/charts/crds/crds",
        "ref": "kube-prometheus-stack-{version}",
    },
}


# ---------------------------------------------------------------------------
# Repo-root-aware path helpers
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(script_dir), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: could not determine git repo root: {e}", file=sys.stderr)
        sys.exit(1)
    return Path(out.stdout.strip())


def resolve_repo_path(arg: str, root: Path) -> Path:
    p = Path(arg)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fetch-crds.py"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers=github_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e


def github_list_dir(repo: str, path: str, ref: str) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    data = fetch_url(url)
    return json.loads(data)


# ---------------------------------------------------------------------------
# HelmRelease discovery
# ---------------------------------------------------------------------------

def find_helm_releases(root: Path) -> dict[str, str]:
    """Return {chart_name: version} for all pinned HelmReleases in the repo."""
    versions: dict[str, str] = {}
    for path in root.rglob("*.yml"):
        rel = path.relative_to(root)
        if any(p.startswith(".") for p in rel.parts):
            continue
        try:
            with open(path) as f:
                for doc in yaml.safe_load_all(f):
                    if not isinstance(doc, dict):
                        continue
                    if doc.get("kind") != "HelmRelease":
                        continue
                    chart = doc.get("spec", {}).get("chart", {}).get("spec", {})
                    name = chart.get("chart")
                    version = chart.get("version")
                    if name and version:
                        versions[name] = version
        except Exception:
            continue
    return versions


# ---------------------------------------------------------------------------
# YAML splitting helpers
# ---------------------------------------------------------------------------

def filename_for(doc: dict) -> str:
    kind = doc.get("kind", "Unknown")
    name = doc.get("metadata", {}).get("name", "")
    if name:
        return f"{kind}-{name}.yml"
    return f"{kind}.yml"


def write_docs(docs: list[dict], output_dir: Path, dry_run: bool) -> int:
    written = 0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        fname = filename_for(doc)
        if dry_run:
            print(f"      {fname}")
        else:
            out = output_dir / fname
            with open(out, "w") as f:
                yaml.dump(doc, f, default_flow_style=False, allow_unicode=True)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Fetch strategies
# ---------------------------------------------------------------------------

def fetch_raw(source: dict, version: str, output_dir: Path, dry_run: bool) -> int:
    url = source["url"].format(version=version)
    print(f"    {url}")
    content = fetch_url(url)
    docs = [d for d in yaml.safe_load_all(content) if d]
    return write_docs(docs, output_dir, dry_run)


def fetch_github_dir(source: dict, version: str, output_dir: Path, dry_run: bool) -> int:
    repo = source["repo"]
    ref = source["ref"].format(version=version)
    paths = source.get("paths") or [source["path"]]
    written = 0
    for path in paths:
        print(f"    {repo}/{path} @ {ref}")
        entries = github_list_dir(repo, path, ref)
        for entry in entries:
            if not entry.get("name", "").endswith(".yaml"):
                continue
            dl_url = entry["download_url"]
            content = fetch_url(dl_url)
            docs = [d for d in yaml.safe_load_all(content) if d]
            n = write_docs(docs, output_dir, dry_run)
            written += n
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument("--crds-dir", help="CRD output directory (default: <repo>/infra/crds)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    crds_dir = resolve_repo_path(args.crds_dir, root) if args.crds_dir else root / "infra" / "crds"

    prefix = "=== DRY RUN — " if args.dry_run else "=== "
    print(f"{prefix}fetch-crds ===\n")
    print(f"  Repo root: {root}")
    print(f"  CRDs dir:  {crds_dir}\n")

    helm_versions = find_helm_releases(root)

    total_written = 0
    total_skipped = 0
    total_failed = 0

    for chart_name, source in CRD_SOURCES.items():
        version = helm_versions.get(chart_name)
        if not version:
            print(f"  SKIP  {chart_name} — no HelmRelease with pinned version found")
            total_skipped += 1
            continue

        print(f"  {chart_name} @ {version}")
        output_dir = crds_dir / chart_name

        if not args.dry_run:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        try:
            strategy = source["strategy"]
            if strategy == "raw":
                n = fetch_raw(source, version, output_dir, args.dry_run)
            elif strategy == "github-dir":
                n = fetch_github_dir(source, version, output_dir, args.dry_run)
            else:
                print(f"    ERROR: unknown strategy {strategy!r}")
                total_failed += 1
                continue
            print(f"    OK — {n} CRD file(s)\n")
            total_written += n
        except Exception as e:
            print(f"    FAIL — {e}\n")
            total_failed += 1

    print(f"  Total: {total_written} written, {total_skipped} skipped, {total_failed} failed")


if __name__ == "__main__":
    main()
