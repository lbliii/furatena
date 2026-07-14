# Changelog fragments

Every pull request that changes behavior, documentation, security posture, or
operator expectations must add `ISSUE.TYPE.md` here. Valid types are `added`,
`changed`, `deprecated`, `removed`, `fixed`, and `security`.

Write one concise, user-facing sentence. For example:

```text
365.changed.md
```

Preview the next release notes with `make changelog-draft`. Release preparation
uses Towncrier to assemble these fragments into `CHANGELOG.md`.
