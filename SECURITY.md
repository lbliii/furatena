# Security policy

## Supported versions

Furatena is currently an alpha project. Security fixes are applied to the
latest released version and `main`; older alpha versions are not maintained as
separate security branches.

## Report a vulnerability

Use GitHub's private vulnerability reporting or open a private security
advisory for `lbliii/furatena`. Do not disclose credentials, exploit details,
private content, or affected deployments in a public issue.

Include the affected version or commit, attack prerequisites, impact,
reproduction steps, and any suggested mitigation. Maintainers will acknowledge
the report, assess severity and affected versions, coordinate a fix, and
publish an advisory when users can act safely.

## Release incidents

For a compromised or incorrectly published distribution, follow
[docs/RELEASING.md](docs/RELEASING.md): stop publishing, preserve evidence, yank
affected versions with a reason, verify hashes and attestations, and publish a
new version. Never reuse a compromised version or filename.
