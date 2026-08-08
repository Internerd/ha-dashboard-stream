# Contributing

Thanks for considering a contribution to Dashboard Stream Cam.

## Before you start

- Read [AI_POLICY.md](./AI_POLICY.md) - it applies to this repository's
  own history and to any contribution you submit.
- For anything beyond a small fix, please open an issue first to discuss
  the approach - especially for changes touching authentication, network
  exposure, or the container's privilege model (see
  [SECURITY.md](./SECURITY.md)).
- Found a security vulnerability? Do **not** open a public issue - follow
  [SECURITY.md](./SECURITY.md) instead.

## Development setup

This repository is a standard Home Assistant App/Add-on repository. To
iterate on the app itself:

1. Add this repository (or your fork/branch) to a test Home Assistant
   instance's App/Add-on store (**Settings &rarr; Add-ons &rarr; Add-on
   Store &rarr; Repositories**).
2. While developing, Supervisor builds `dashboard_stream/` locally from
   its `Dockerfile` on every install/rebuild - no registry push needed.
3. Bump `version` in `dashboard_stream/config.yaml` and add an entry to
   `dashboard_stream/CHANGELOG.md` for any user-facing change, following
   [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
   [Semantic Versioning](https://semver.org/).

Useful local checks before opening a PR:

```bash
# YAML sanity
python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" dashboard_stream/config.yaml

# Shell scripts
shellcheck dashboard_stream/rootfs/etc/cont-init.d/*.sh dashboard_stream/rootfs/etc/services.d/*/run dashboard_stream/rootfs/etc/services.d/*/finish

# Python syntax
python3 -m py_compile dashboard_stream/app/*.py
```

The same checks run in CI on pull requests (see `.github/workflows/lint.yml`).

## Pull requests

- Keep PRs focused; unrelated changes make review slower.
- Explain **why**, not just what, in the PR description - you should be
  able to answer follow-up questions about your change in your own words.
- Update `DOCS.md`/`README.md` alongside any behavior change.
- By submitting a contribution, you agree it is licensed under this
  repository's [Apache License 2.0](./LICENSE).

## Code of conduct

Participation in this project is governed by the
[Code of Conduct](./CODE_OF_CONDUCT.md).
