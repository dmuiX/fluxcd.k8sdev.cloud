import json
import sys

lines = []
with open(sys.argv[1]) as f:
    for line in f:
        try:
            lines.append(json.loads(line))
        except Exception:
            pass

for line in lines:
    if "Dependency extraction complete" in line.get("msg", ""):
        stats = line.get("stats", {})
        total = stats.get("total", {})
        managers = stats.get("managers", {})
        print(f'- **Files scanned:** {total.get("fileCount", 0)}')
        print(f'- **Dependencies found:** {total.get("depCount", 0)}')
        for mgr, data in managers.items():
            print(f'  - {mgr}: {data.get("depCount", 0)} deps in {data.get("fileCount", 0)} files')
        print()
        break

prs_created = [l for l in lines if "PR created" in l.get("msg", "")]
prs_updated = [l for l in lines if "PR updated" in l.get("msg", "")]
prs_closed  = [l for l in lines if "PR closed" in l.get("msg", "")]

if prs_created or prs_updated or prs_closed:
    print("### PRs")
    for l in prs_created:
        print(f'- created: {l.get("prTitle", l.get("title", ""))}')
    for l in prs_updated:
        print(f'- updated: {l.get("prTitle", l.get("title", ""))}')
    for l in prs_closed:
        print(f'- closed: {l.get("prTitle", l.get("title", ""))}')
else:
    print("- No PRs created or updated")
