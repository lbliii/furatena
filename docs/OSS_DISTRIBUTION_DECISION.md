# Open-source distribution decision

Decision date: **2026-08-05**  
Status: **accepted**  
Supersedes: proprietary-runtime framing in #471 and the “reject PyPI”
alternative in [RAILWAY_TEMPLATE_ARCHITECTURE.md](RAILWAY_TEMPLATE_ARCHITECTURE.md)

## Decision

Furatena is an **open-source** documentation compiler and runtime under the
existing **MIT** license. The installable Python package is published to
**PyPI** as `furatena`. **Railway templates and deployments** remain the
recommended path for a hosted live platform and are expected to earn template
marketplace kickbacks without requiring a proprietary image or closed source.

## Consequences

| Concern | Outcome |
| --- | --- |
| License | Keep root `LICENSE` (MIT) and `pyproject.toml` `license = "MIT"` |
| Source | Make the GitHub repository **public** before the first PyPI release |
| Install | `pip install furatena` / `uv tool install furatena` after first publish |
| Live hosting | Railway template installs from PyPI (or builds from public source) |
| Private GHCR image | Optional convenience/ops path; not the IP or license boundary |
| #471 | Resolved as “confirm MIT + public repo + PyPI,” not a new proprietary license |

## Operator checklist

See [PYPI.md](PYPI.md) for Trusted Publishing registration and the first
release sequence. See [RELEASING.md](RELEASING.md) for package release steps
alongside any remaining image operations.
