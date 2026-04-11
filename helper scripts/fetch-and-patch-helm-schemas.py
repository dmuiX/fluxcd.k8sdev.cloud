#!/usr/bin/env python3
"""
fetch-and-patch-helm-schemas.py

1. Scans repo for HelmRelease + HelmRepository manifests (.yaml + .yml)
2. Pulls charts and extracts values.schema.json
3. If no schema exists, generates one from the chart's values.yaml
4. Inlines sub-chart schemas into the top-level values.schema.json (e.g. grafana, kube-state-metrics inside kube-prometheus-stack)
5. Patches all schemas with additionalProperties: false (recursive)
6. Generates composite helmrelease.schema.json for VS Code validation
7. Outputs ready-to-use .vscode/settings.json config

Usage: python3 fetch-and-patch-helm-schemas.py [--debug] [--path <repo-root>] [--schema-dir <path>]

All arguments are optional. Defaults:
  --path        repo root (discovered via git)
  --schema-dir  <repo-root>/values-schemas

Relative paths are resolved against the repo root, not the current working
directory, so the script behaves identically regardless of where it's run from.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DEBUG = False

try:
    import yaml
except ImportError:
    print("PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


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
# Step 1: Discover HelmReleases and fetch/generate schemas
# ---------------------------------------------------------------------------

def find_yaml_docs(repo_root, schema_dir):
    """Yield (doc, filepath) for all YAML documents, skipping hidden dirs,
    node_modules, and the schema output directory."""
    schema_dir_resolved = schema_dir.resolve()
    for ext in ("*.yaml", "*.yml"):
        for yaml_file in repo_root.rglob(ext):
            # Skip hidden dirs, node_modules, and the schema output dir
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


def find_helm_repos(repo_root, schema_dir):
    repos = {}
    for doc, _ in find_yaml_docs(repo_root, schema_dir):
        if doc.get("kind") == "HelmRepository":
            name = doc.get("metadata", {}).get("name")
            url = doc.get("spec", {}).get("url")
            if name and url:
                repos[name] = url
    return repos


def find_helm_releases(repo_root, schema_dir):
    releases = []
    for doc, filepath in find_yaml_docs(repo_root, schema_dir):
        if doc.get("kind") == "HelmRelease":
            chart_spec = doc.get("spec", {}).get("chart", {}).get("spec", {})
            chart_name = chart_spec.get("chart")
            chart_version = chart_spec.get("version")
            repo_name = chart_spec.get("sourceRef", {}).get("name")
            if chart_name and repo_name:
                releases.append({
                    "chart": chart_name,
                    "version": chart_version,
                    "repo_name": repo_name,
                    "file": filepath,
                })
    return releases


def infer_schema_from_value(value):
    """Infer a JSON Schema node from a YAML value."""
    if value is None:
        return {"type": ["null", "string", "integer", "boolean", "object", "array"]}
    elif isinstance(value, bool):
        return {"type": "boolean"}
    elif isinstance(value, int):
        return {"type": "integer"}
    elif isinstance(value, float):
        return {"type": "number"}
    elif isinstance(value, str):
        return {"type": "string"}
    elif isinstance(value, list):
        if value:
            return {"type": "array", "items": infer_schema_from_value(value[0])}
        return {"type": "array"}
    elif isinstance(value, dict):
        if not value:
            return {"type": "object"}
        props = {}
        for k, v in value.items():
            props[k] = infer_schema_from_value(v)
        return {
            "type": "object",
            "properties": props,
        }
    return {}


def generate_schema_from_values(values_yaml_path):
    """Generate a JSON Schema from a values.yaml file."""
    with open(values_yaml_path) as f:
        values = yaml.safe_load(f)

    if not isinstance(values, dict):
        return None

    schema = infer_schema_from_value(values)
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    return schema


def deep_merge_schemas(official, generated):
    """
    Recursively merge two JSON Schema objects. The official schema wins
    for any key it defines; the generated schema fills in missing keys.
    This ensures incomplete official schemas get supplemented with keys
    found in values.yaml.
    """
    if not isinstance(official, dict) or not isinstance(generated, dict):
        return official

    merged = dict(official)

    # Merge properties recursively
    if "properties" in generated:
        if "properties" not in merged:
            merged["properties"] = {}
        for key, gen_prop in generated["properties"].items():
            if key in merged["properties"]:
                # Both have this key — recurse to fill in nested gaps
                merged["properties"][key] = deep_merge_schemas(
                    merged["properties"][key], gen_prop
                )
            else:
                # Missing in official — add from generated
                merged["properties"][key] = gen_prop

    return merged


def build_schema_from_chart_dir(chart_dir):
    """
    Build a complete schema from a chart directory by merging the official
    values.schema.json (if present) with a schema generated from values.yaml.
    The official schema takes precedence; the generated one fills gaps.
    Returns (status, schema_dict_or_None).
    Status is "ok" if official existed, "generated" if only values.yaml, "failed" if neither.
    """
    schema_file = chart_dir / "values.schema.json"
    values_file = chart_dir / "values.yaml"

    official = None
    if schema_file.exists():
        with open(schema_file) as f:
            official = json.load(f)

    generated = None
    if values_file.exists():
        generated = generate_schema_from_values(values_file)

    if official and generated:
        merged = deep_merge_schemas(official, generated)
        return "ok", merged
    elif official:
        return "ok", official
    elif generated:
        return "generated", generated
    else:
        return "failed", None


def extract_schema_from_chart_dir(chart_dir, output_dir):
    """
    Build a merged schema from an unpacked chart directory and write to disk.
    Returns (status, message) where status is "ok", "generated", or "failed".
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    schema_output = output_dir / "values.schema.json"

    status, schema = build_schema_from_chart_dir(chart_dir)

    if schema:
        with open(schema_output, "w") as f:
            json.dump(schema, f, indent=2)
            f.write("\n")
        return status, str(schema_output)

    return "failed", "no values.schema.json or values.yaml in chart"


def merge_subcharts_into_top_level_schema(output_dir, sub_chart_schemas):
    """
    Load the top-level values.schema.json and merge sub-chart schemas
    into the top-level properties. Uses deep merge so that keys from
    both the top-level values.yaml and the sub-chart are preserved.

    sub_chart_schemas: dict of {sub_name: schema_dict}
    """
    top_schema_path = output_dir / "values.schema.json"
    if not top_schema_path.exists() or not sub_chart_schemas:
        return

    with open(top_schema_path) as f:
        schema = json.load(f)

    if "properties" not in schema:
        schema["properties"] = {}

    for sub_name, sub_schema in sub_chart_schemas.items():
        # Strip top-level $schema key from sub-chart before inlining
        inlined = {k: v for k, v in sub_schema.items() if k != "$schema"}
        if sub_name in schema["properties"]:
            # Merge: keep existing keys from top-level, add missing from sub-chart
            schema["properties"][sub_name] = deep_merge_schemas(
                inlined, schema["properties"][sub_name]
            )
        else:
            schema["properties"][sub_name] = inlined

    with open(top_schema_path, "w") as f:
        json.dump(schema, f, indent=2)
        f.write("\n")


def fetch_or_generate_schema(repo_name, repo_url, chart_name, chart_version, schema_dir):
    """
    Pull chart, extract values.schema.json or generate from values.yaml.
    Sub-chart schemas are extracted to disk temporarily, merged into the
    top-level schema, then the sub-chart directory is removed.
    Returns (status, message, list_of_sub_chart_names).
    """
    output_dir = schema_dir / chart_name
    output_dir.mkdir(parents=True, exist_ok=True)

    add_result = subprocess.run(
        ["helm", "repo", "add", repo_name, repo_url, "--force-update"],
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        return "failed", f"helm repo add failed: {add_result.stderr.strip()}", []

    # Pull and untar directly into the output_dir
    # This puts values.yaml, charts/, etc. right where we need them
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = ["helm", "pull", f"{repo_name}/{chart_name}", "--untar", "--untardir", tmpdir]
        if chart_version:
            cmd.extend(["--version", chart_version])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return "failed", f"helm pull failed: {result.stderr.strip()}", []

        # Find the actual unpacked chart directory
        unpacked_dirs = [d for d in Path(tmpdir).iterdir() if d.is_dir()]
        if not unpacked_dirs:
            return "failed", "helm pull produced no directory", []
        chart_dir = unpacked_dirs[0]

        # Top-level schema
        status, msg = extract_schema_from_chart_dir(chart_dir, output_dir)

        # Sub-charts: extract to disk, build schemas, collect for merge
        sub_chart_schemas = {}
        charts_subdir = chart_dir / "charts"
        if DEBUG:
            print(f"           chart_dir={chart_dir.name} charts_exists={charts_subdir.exists()} contents={[c.name for c in chart_dir.iterdir()] if chart_dir.exists() else 'N/A'}")
        if charts_subdir.exists():
            # Unpack any .tgz sub-chart archives in place
            for tgz in sorted(charts_subdir.glob("*.tgz")):
                try:
                    with tarfile.open(tgz, "r:gz") as tar:
                        tar.extractall(path=charts_subdir)
                except Exception as e:
                    print(f"  WARN  failed to unpack {tgz.name}: {e}")

            for sub_chart_dir in sorted(charts_subdir.iterdir()):
                if not sub_chart_dir.is_dir():
                    continue
                sub_name = sub_chart_dir.name

                # Write sub-chart schema to disk temporarily
                sub_output_dir = output_dir / "charts" / sub_name
                sub_status, sub_msg = extract_schema_from_chart_dir(sub_chart_dir, sub_output_dir)
                if DEBUG:
                    indicator = "OK" if sub_status == "ok" else ("GEN" if sub_status == "generated" else "FAIL")
                    print(f"           sub-chart {sub_name} [{indicator}]")

                # Load the written schema back for merging
                if sub_status in ("ok", "generated"):
                    schema_path = sub_output_dir / "values.schema.json"
                    with open(schema_path) as f:
                        sub_chart_schemas[sub_name] = json.load(f)

        # Merge sub-chart schemas into top-level values.schema.json
        if sub_chart_schemas:
            merge_subcharts_into_top_level_schema(output_dir, sub_chart_schemas)

        # Clean up sub-chart schemas — they're already merged into the top-level
        charts_output_dir = output_dir / "charts"
        if charts_output_dir.exists():
            shutil.rmtree(charts_output_dir)

        return status, msg, list(sub_chart_schemas.keys())


def fetch_all(repo_root, schema_dir):
    print("=== Fetching schemas ===\n")
    helm_repos = find_helm_repos(repo_root, schema_dir)
    helm_releases = find_helm_releases(repo_root, schema_dir)

    if not helm_releases:
        print("No HelmReleases found.", file=sys.stderr)
        return {}

    print(f"Found {len(helm_releases)} HelmReleases, {len(helm_repos)} HelmRepositories\n")
    subprocess.run(["helm", "repo", "update"], capture_output=True)

    results = {"ok": [], "generated": [], "failed": []}
    # chart_files maps (chart_name, version) -> (hr_file, sub_chart_names)
    # to handle same chart name with different versions in separate HelmReleases
    chart_files = {}

    for release in helm_releases:
        chart = release["chart"]
        version = release["version"]
        repo_name = release["repo_name"]
        repo_url = helm_repos.get(repo_name)

        # No pinned version -> skip, schema would be unreliable
        if not version:
            print(f"  SKIP  {chart}@??? - no version pinned in HelmRelease, schema would be unreliable")
            results["failed"].append(f"{chart}@???")
            continue

        label = f"{chart}@{version}"

        if not repo_url:
            print(f"  SKIP  {label} - HelmRepository '{repo_name}' not found")
            results["failed"].append(label)
            continue

        # Deduplicate: same chart+version already processed
        dedup_key = (chart, version)
        if dedup_key in chart_files:
            print(f"  SKIP  {label} - already processed")
            continue

        status, msg, sub_chart_names = fetch_or_generate_schema(
            repo_name, repo_url, chart, version, schema_dir
        )

        if status == "ok":
            indicator = "OK "
            results["ok"].append(label)
            chart_files[dedup_key] = (release["file"], sub_chart_names)
        elif status == "generated":
            indicator = "GEN"
            results["generated"].append(label)
            chart_files[dedup_key] = (release["file"], sub_chart_names)
        else:
            indicator = "FAIL"
            results["failed"].append(label)

        if status == "failed":
            print(f"  {indicator}  {label} - {msg}")
        elif sub_chart_names:
            print(f"  {indicator}  {label} (+ {', '.join(sub_chart_names)})")
        else:
            print(f"  {indicator}  {label}")

    print(f"\n  Extracted: {len(results['ok'])}  |  Generated: {len(results['generated'])}  |  Failed: {len(results['failed'])}")
    if results["generated"]:
        print(f"  Generated from values.yaml: {', '.join(results['generated'])}")
    if results["failed"]:
        print(f"  Failed: {', '.join(results['failed'])}")

    return chart_files


# ---------------------------------------------------------------------------
# Step 2: Patch additionalProperties: false
# ---------------------------------------------------------------------------

def patch_node(node):
    """Recursively patch a schema node in-place."""
    if isinstance(node, dict):
        for key in list(node.keys()):
            patch_node(node[key])

        if "properties" in node and "additionalProperties" not in node:
            node_type = node.get("type")
            is_object = (
                node_type is None
                or node_type == "object"
                or (isinstance(node_type, list) and "object" in node_type)
            )
            if is_object:
                node["additionalProperties"] = False

    elif isinstance(node, list):
        for item in node:
            patch_node(item)

    return node


def patch_all(schema_dir):
    print("\n=== Patching schemas (additionalProperties: false) ===\n")
    for schema_file in schema_dir.rglob("values.schema.json"):
        with open(schema_file) as f:
            schema = json.load(f)

        patch_node(schema)

        with open(schema_file, "w") as f:
            json.dump(schema, f, indent=2)
            f.write("\n")

        print(f"  Patched: {schema_file}")


# ---------------------------------------------------------------------------
# Step 3: Generate composite HelmRelease schemas
# ---------------------------------------------------------------------------

def generate_composite(chart_name):
    """
    Generate a composite HelmRelease schema.
    spec.values points to values.schema.json which already has sub-chart
    schemas inlined.
    """
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "description": f"Composite schema for {chart_name} HelmRelease",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "apiVersion": {"type": "string"},
                    "kind": {"const": "HelmRelease"},
                    "metadata": {"type": "object"},
                    "spec": {
                        "type": "object",
                        "properties": {
                            "values": {"$ref": "./values.schema.json"},
                            "interval": {"type": "string"},
                            "timeout": {"type": "string"},
                            "releaseName": {"type": "string"},
                            "targetNamespace": {"type": "string"},
                            "chart": {"type": "object"},
                            "chartRef": {"type": "object"},
                            "install": {"type": "object"},
                            "upgrade": {"type": "object"},
                            "rollback": {"type": "object"},
                            "uninstall": {"type": "object"},
                            "valuesFrom": {"type": "array"},
                            "dependsOn": {"type": "array"},
                            "suspend": {"type": "boolean"},
                            "maxHistory": {"type": "integer"},
                            "persistentClient": {"type": "boolean"},
                            "driftDetection": {"type": "object"},
                            "postRenderers": {"type": "array"},
                        },
                    },
                },
                "required": ["apiVersion", "kind"],
            },
            {
                "type": "object",
                "properties": {
                    "apiVersion": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "not": {"const": "HelmRelease"},
                    },
                    "metadata": {"type": "object"},
                    "spec": {"type": "object"},
                },
            },
        ],
    }


def generate_all(schema_dir):
    print("\n=== Generating composite schemas ===\n")
    for values_schema in schema_dir.rglob("values.schema.json"):
        chart_dir = values_schema.parent
        chart_name = chart_dir.name
        output = chart_dir / "helmrelease.schema.json"

        composite = generate_composite(chart_name)

        with open(output, "w") as f:
            json.dump(composite, f, indent=2)
            f.write("\n")

        print(f"  Generated: {output}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global DEBUG
    parser = argparse.ArgumentParser(description="Fetch and patch Helm chart schemas for VS Code validation")
    parser.add_argument("--path", help="Repository root (default: auto-discovered via git)")
    parser.add_argument("--schema-dir", help="Schema output directory (default: <repo-root>/values-schemas)")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
    args = parser.parse_args()

    DEBUG = args.debug
    root = repo_root()
    repo_root_path = resolve_repo_path(args.path, root) if args.path else root
    schema_dir = resolve_repo_path(args.schema_dir, root) if args.schema_dir else root / "values-schemas"
    if schema_dir.exists():
        shutil.rmtree(schema_dir)
        print(f"Cleaned {schema_dir}\n")
    schema_dir.mkdir(parents=True, exist_ok=True)

    chart_files = fetch_all(repo_root_path, schema_dir)
    patch_all(schema_dir)
    generate_all(schema_dir)

    # Build settings.json with actual file paths
    yaml_schemas = {}
    for values_schema in schema_dir.rglob("helmrelease.schema.json"):
        chart_name = values_schema.parent.name
        schema_rel = str(values_schema.relative_to(repo_root_path))

        # Find matching entry in chart_files (keyed by (name, version))
        matched_file = None
        for (name, _version), (hr_file, _subs) in chart_files.items():
            if name == chart_name:
                matched_file = hr_file
                break

        if matched_file:
            file_rel = str(matched_file.relative_to(repo_root_path))
            yaml_schemas[schema_rel] = file_rel
        else:
            yaml_schemas[schema_rel] = f"**/{chart_name}/**/*.{{yaml,yml}}"

    print("\n=== Done ===")
    print(f"\nAdd to .vscode/settings.json:")
    print(json.dumps({"yaml.schemas": yaml_schemas}, indent=2))


if __name__ == "__main__":
    main()