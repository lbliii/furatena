"""Content migration helpers — MDX to canonical Patitas markdown."""

from furatena.catalog.migrate.mdx import (
    MigrateReport,
    migrate_mdx_file,
    migrate_mdx_paths,
    migrate_mounts,
)
from furatena.catalog.migrate.remediation import (
    SafeRemediationResult,
    remediate_mdx_file_safely,
    remediate_mdx_paths_safely,
    remediation_plan,
)
from furatena.catalog.migrate.report import (
    build_migration_report,
    render_migration_report,
)

__all__ = [
    "MigrateReport",
    "SafeRemediationResult",
    "build_migration_report",
    "migrate_mdx_file",
    "migrate_mdx_paths",
    "migrate_mounts",
    "remediate_mdx_file_safely",
    "remediate_mdx_paths_safely",
    "remediation_plan",
    "render_migration_report",
]
