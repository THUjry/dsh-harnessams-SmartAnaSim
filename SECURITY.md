# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x (pre-release) | yes |

## Boundaries you should know about

- The plugin spawns plain Python scripts (`scripts/`) via the system `python`; the scripts
  write only into workspace run directories and the trash folder. No credentials are read,
  stored, or transmitted anywhere.
- Custom web routes registered by DSH plugins are **not token-gated** by the DSH web server.
  This plugin registers no web routes of its own; the HTML reports it generates are static
  files intended to be served from the top-level console plugin's report route (management
  UI / static assets only). Do not expose generated reports on an untrusted network without
  adding an auth layer.
- Destructive operations move paths into the workspace trash (`trash.py`); true deletion
  requires an explicit `--purge` of a named trash entry.

## Reporting a vulnerability

Until the public repository goes live, report privately to the maintainer. Once published,
use the GitHub security advisory flow of the repository (link to be added at publication).
