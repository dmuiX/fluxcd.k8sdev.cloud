# helper scripts

Maintenance scripts for this Flux repository. All scripts are **repo-root-aware** and accept **zero required arguments** — run them from any working directory.

---

## Prerequisites

| Tool | Required by | Install |
| --- | --- | --- |
| Python 3 + PyYAML | all scripts | `pip install pyyaml` |
| helm CLI | `fetch-and-patch-helm-schemas.py` | `brew install helm` |
| fswatch | VS Code auto-validation watcher | `brew install fswatch` |
| jsonschema | `validate-helm-schemas.py` | `pip install jsonschema` |

### VS Code auto-validation

`validate-helm-schemas.py` runs automatically on every YAML save via a background task in `.vscode/tasks.json`. It uses **fswatch** to watch for file changes — no Claude Code or manual task trigger needed.

Errors appear directly in the VS Code **Problems** panel with file + line references.

The watcher starts automatically when you open the workspace folder (`runOn: folderOpen`). If it doesn't start, run it manually: `Terminal → Run Task → Watch & Validate HelmReleases`.

---

## Scripts

### `fetch-and-patch-helm-schemas.py`

Fetches `values.schema.json` for every pinned `HelmRelease` in the repo, generates schemas from `values.yaml` when none exist, inlines sub-chart schemas, patches all schemas with `additionalProperties: false`, and emits ready-to-use `.vscode/settings.json` fragments.

```sh
python3 "helper scripts/fetch-and-patch-helm-schemas.py"
python3 "helper scripts/fetch-and-patch-helm-schemas.py" --debug
python3 "helper scripts/fetch-and-patch-helm-schemas.py" --schema-dir path/to/schemas
```

Output lands in `<repo>/values-schemas/` by default.

---

### `split-flux-yamls.py`

Splits every multi-document YAML file in the repo into one file per Kubernetes object. Single-document files are left untouched (idempotent).

```sh
python3 "helper scripts/split-flux-yamls.py" --dry-run
python3 "helper scripts/split-flux-yamls.py"
```

Folder naming: HelmRelease files produce a folder named after the chart; other files use the original filename stem. Within each folder, a Kind that appears once becomes `Kind.yml`; duplicates become `Kind-<name>.yml`.

---

### `organize-flux-yamls.py`

Moves flat single-document YAML files into topic subdirectories according to mapping files named `organize-flux-yamls.yml`. The script searches the whole repo for mapping files automatically.

```sh
python3 "helper scripts/organize-flux-yamls.py" --dry-run
python3 "helper scripts/organize-flux-yamls.py"
```

**Mapping file format** (`organize-flux-yamls.yml` anywhere in the repo):

```yaml
moves:
  source-file.yml: subfolder/TargetName.yml
```

Paths are bare filenames or short relative paths. If a source is not found in the mapping file's directory the script searches the entire repo for the file. The target is resolved relative to wherever the source was found. The script is fully **idempotent**: already-moved files are detected and silently skipped even if they ended up at a different base path.

Uses `git mv` when possible to preserve history; falls back to a plain filesystem move for untracked files.

---

### `inject-schema-comments.py`

Injects `# yaml-language-server: $schema=...` comments into HelmRelease files so VS Code validates `spec.values` against the matching schema in `values-schemas/`.

```sh
python3 "helper scripts/inject-schema-comments.py" --dry-run
python3 "helper scripts/inject-schema-comments.py"
```

Run after `fetch-and-patch-helm-schemas.py` and `split-flux-yamls.py`.

---

### `validate-helm-schemas.py`

Validates all HelmRelease `spec.values` blocks against their schemas and reports violations as VS Code-style problems.

```sh
python3 "helper scripts/validate-helm-schemas.py"
```

---

## Typical workflow

```sh
# 1. Fetch/regenerate schemas (needs helm + internet)
python3 "helper scripts/fetch-and-patch-helm-schemas.py"

# 2. Split any new multi-doc YAML files
python3 "helper scripts/split-flux-yamls.py"

# 3. Organise flat config files into topic folders
python3 "helper scripts/organize-flux-yamls.py"

# 4. Inject schema comments into HelmRelease files
python3 "helper scripts/inject-schema-comments.py"

# 5. Validate
python3 "helper scripts/validate-helm-schemas.py"
```
