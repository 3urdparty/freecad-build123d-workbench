# Security Policy

## Supported Versions

The current public alpha release is supported. Please update to the latest
release before reporting an issue.

## Reporting a Vulnerability

Report vulnerabilities privately to [security@jobi.is](mailto:security@jobi.is).
Do not disclose the issue publicly until we have coordinated a fix and release.
We aim to acknowledge reports within five business days and will provide status
updates while investigating.

## Security Model

Code Workbench intentionally executes user-selected Python scripts. Only open
scripts and FreeCAD documents from sources you trust. Its kernel RPC server
binds to loopback and requires a per-process random token. On first setup, the
workbench may download Python and pinned packages after explicit user consent;
review the package settings before approving setup.
