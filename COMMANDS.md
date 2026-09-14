# Publishing

Releases publish to PyPI from GitHub Actions when a `vX.Y.Z` tag is pushed.
Trusted publishing is used — no API token.

```bash
make release              # bump patch in pyproject.toml
make release minor        # bump minor
make release major        # bump major

git add pyproject.toml
git commit -m "Bump version"
git tag v$(uv version --short)
git push origin HEAD --tags
```

The tag must match the version in `pyproject.toml` (`v0.7.18` for `0.7.18`).
Pushing it runs `.github/workflows/publish.yml`.

### One-time PyPI setup

On https://pypi.org/manage/project/ouro-mcp/settings/publishing add (or update)
a GitHub trusted publisher:

- Owner: `ourofoundation`
- Repository: `ouro-mcp`
- Workflow: `publish.yml`
- Environment: `pypi`

If a publisher already exists for `python-publish.yml`, change the workflow
filename to `publish.yml`.
