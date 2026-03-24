# AGENTS.md

## Commit Style

- Format: `type (scope): description`
- Types: `feat`, `fix`, `docs`, `chore`
- Scope: the component or file that was changed, e.g. `openbao`, `README`, `cert-manager`
- Each logical change gets its own commit — do not batch unrelated changes

Examples:
```
feat (cloudnative-pg): add dependsOn for proper deployment order
fix (cluster-issuer): replace personal email
docs (openbao): add jq gotchas
chore (gitignore): remove token glob
```

## No Co-Author Tags

Do not add `Co-Authored-By` or any AI attribution to commit messages.
