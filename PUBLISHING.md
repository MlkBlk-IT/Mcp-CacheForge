# Publishing `icycache`

Two ways to publish. Pick one — both produce the same artifacts.

The package name, the repository name and the GitHub URL are all `icycache`.

---

## A. GitHub Actions (recommended — nothing secret on your machine)

**PyPI — trusted publishing (OIDC). No API token is stored anywhere.**

One-time setup on pypi.org → *MlkBlk-IT* → *Publishing* → *Add a new pending publisher*:

| Field | Value |
|---|---|
| PyPI project name | `icycache` |
| Owner | `MlkBlk-IT` |
| Repository | `MlkBlk-IT/icycache` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Then create the GitHub environment: repo → *Settings* → *Environments* → new environment `pypi`.

**npm — classic automation token.**

1. npmjs.com → *Access Tokens* → *Generate new token* (scope: **publish**).
2. Repo → *Settings* → *Secrets and variables* → *Actions* → new repository secret `NPM_TOKEN`.

Then bump the version in `pyproject.toml` and `package.json`, commit, tag, and run
*Actions* → *Publish* → *Run workflow*.

---

## B. From your machine

```bash
# build
python -m build
python -m twine check dist/*

# PyPI — needs TWINE_USERNAME=__token__ and TWINE_PASSWORD=pypi-XXXX
python -m twine upload dist/*

# npm — needs `npm login` first
npm publish --access public
```

`python -m build` produces `dist/icycache-<version>-py3-none-any.whl` and
`dist/icycache-<version>.tar.gz`.

---

## Notes

- The PyPI distribution is `icycache` (`pip install icycache`); the npm package is `icycache`
  too (`npx -y icycache`). The npm package exposes two binaries: `icycache` (the CLI:
  `serve` / `mcp` / `doctor`) and `icycache-mcp` (the MCP server on stdio), so
  `npx -y -p icycache icycache-mcp` starts the MCP server.
- The import package is `icycache`.
- The wheel ships the runtime data (`config/`, `static/`) next to the code.
