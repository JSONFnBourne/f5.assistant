# F5OS-A and F5OS-C Content

This directory contains content related to F5OS platforms (F5OS-A and F5OS-C), including:

- System controllers and chassis partitions
- Tenants and BIG-IP VE instances
- Underlay and overlay networking
- Platform lifecycle operations (upgrades, backups, diagnostics)

Current subdirectories:

- `api/` – Swagger (OpenAPI 2.0) specifications for F5OS REST APIs, mirrored from clouddocs for F5OS-A 1.8.3 and F5OS-C 1.8.1. Treat these as the canonical reference for endpoints and schemas.
- `examples/` – Concrete REST workflows (e.g., tenant lifecycle) that are derived from the swagger specs and suitable for direct use with tools like VS Code REST Client.

Additional subdirectories can be added here as the F5KSI environment evolves (e.g., `tenants/`, `networking/`, `operations/`).

