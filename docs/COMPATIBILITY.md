# Compatibility and support policy

This document defines the Furatena surfaces external adopters may rely on. The
machine-readable mirror is [`support-policy.json`](support-policy.json); CI
checks it against package metadata, public module exports, and the actual
runtime used by release gates.

## Versioning

Furatena uses [Semantic Versioning 2.0.0](https://semver.org/):

- Patch releases fix defects without intentionally changing a public contract.
- Minor releases add compatible behavior. Before 1.0, a minor release is also
  the only boundary where an announced breaking change may ship.
- At and after 1.0, breaking changes require a major release.

A change is breaking when a supported consumer must change code or configuration
to retain behavior. This includes removing or renaming a public Python symbol,
command, option, config key, DCP field, theme/source-adapter contract member,
MCP resource or tool, changing a stable type, exit code, URL/URI, or narrowing
accepted input.

Additive optional fields, commands, tools, enum values documented as open, and
new diagnostics are normally compatible. Consumers must ignore unknown optional
fields unless a contract explicitly describes a closed set.

## Deprecation and communication

Normal deprecations remain available for at least two consecutive minor
releases and 90 days. A deprecation must include:

1. a runtime warning or structured diagnostic where practical;
2. release-note and migration guidance naming the replacement;
3. a `deprecated` directive or equivalent marker in the owning contract doc;
4. a removal release no earlier than the published support window.

Before 1.0, removal happens only at an eligible minor boundary. At and after
1.0, removal happens only at a major boundary. An actively exploited security
issue or credible data-loss/corruption risk may shorten the window; the release
notes must identify the exception, impact, remediation, and rollback path.

Breaking changes are called out under a **Breaking changes** release-note
heading. CLI, DCP, and MCP fixture diffs require an explicit compatibility or
migration decision in CI before an accepted break can merge.

## Supported Python runtime

The supported 0.1 release runtime is exactly:

- CPython 3.14 (`>=3.14,<3.15`);
- the free-threaded (`3.14t`) build;
- the GIL disabled, with `PYTHON_GIL=0`.

Every Make/CI lane, deterministic evaluation, browser test, benchmark, wheel
build, sdist build, and isolated installed-artifact smoke uses this runtime.
Thread safety is therefore a release contract, not an optional performance
mode.

GIL-enabled CPython, CPython 3.15+, PyPy, and other implementations are not in
the 0.1 support matrix. Packaging may not prevent every unsupported
combination from installing, but a defect is release-blocking only when it
reproduces on the supported free-threaded runtime. A new runtime variant becomes
supported only after it has an explicit CI lane and packaged-artifact smoke.

## Public surfaces

### Python

The public Python API is the union of `__all__` from these modules:

- `furatena`
- `furatena.catalog`
- `furatena.catalog.sources`
- `furatena.cli`
- `furatena.themes`

The exact symbol lists are recorded in `support-policy.json` and checked in CI.
Other modules, attributes prefixed with `_`, and imports not re-exported by one
of those modules are internal. Documentation may describe an internal module
for contributors without making it public.

`furatena.catalog.sources.ContentAdapter`, `register_adapter`, and their exported
input/output records are the supported source-adapter extension boundary.
Adapters must be safe when called concurrently under CPython free-threading.

`furatena.themes.ThemePack` and the `furatena.themes` package entry-point group
are the supported installable-theme boundary. The pack fields and asset paths
documented in [THEMING.md](THEMING.md) are stable; private template context or
undocumented CSS implementation details are not.

### CLI

Command names, options, defaults, exit codes, JSON envelopes, diagnostics, and
machine-readable result fields documented in [CLI_CONTRACT.md](CLI_CONTRACT.md)
and the [generated CLI/config reference](../content/furatena/docs/reference/generated-cli-config.md)
are public. Human-readable prose, whitespace, progress output, and ordering not
declared by the JSON contract are not stable automation interfaces.

### DCP and exported sidecars

The DCP schema and its version policy are defined in [DCP.md](DCP.md). Required
field removal, type changes, identity/URL semantic changes, or stricter
validation that rejects supported fixtures require a new DCP major schema.
Versioned fixtures are the compatibility oracle. Search, structure, inventory,
channel, deployment, and agent sidecars are public only to the extent their
schemas and version fields are documented there or in the CLI contract.

### Configuration

Documented `docs.yaml`, `mounts.yaml`, autodoc, theme, environment, and CLI
configuration keys in the generated reference are public. Defaults are part of
the contract. Undocumented keys, incidental parser acceptance, and internal
dataclass layout are not. Removing a key, narrowing an accepted value, or
changing a default with behavior impact follows the deprecation policy.

### MCP

Documented MCP resource URIs, tool names, input schemas, structured result
fields, error codes, and access behavior are public. Additive optional tools or
fields are compatible. A removal, rename, required-input addition, stable type
change, URI change, or authorization weakening is breaking. The versioned agent
fixtures and `fura agent-diff` enforce semantic compatibility; transport and
policy details are documented in [AGENT_WORKFLOWS.md](AGENT_WORKFLOWS.md).

## Reporting compatibility defects

Reports should include the Furatena version, DCP or agent schema version when
relevant, the command or import path, and `python -VV`. Runtime defects must be
reproduced on CPython 3.14t with `PYTHON_GIL=0`; if they only occur on an
unsupported runtime, record that distinction rather than weakening the
free-threading gates.
