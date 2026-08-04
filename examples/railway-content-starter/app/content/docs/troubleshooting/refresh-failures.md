---
title: Content refresh failures
description: Recover when a documentation generation cannot be activated.
weight: 10
---

# Content refresh failures

Check `/_fura/content/status` for the active generation and remediation. A bad
repository URL, missing ref, invalid subdirectory, or malformed page prevents a
new generation from becoming active. The last-known-good generation continues
serving when one exists.

Correct the repository content or configuration, then retry the authenticated
refresh. If a newly activated generation is wrong, use content rollback and
verify that the application image digest did not change.
