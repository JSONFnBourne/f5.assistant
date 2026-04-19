# F5OS API Specifications

This directory contains machine-readable API specifications for F5OS, sourced from the official clouddocs for the F5OS-A 1.8.3 and F5OS-C 1.8.1 platforms. These files are treated as **gold**: the canonical source for path structure, HTTP methods, parameters, and schema definitions when working with F5OS APIs in this workspace.

## Format

- Each file is a `swagger: \"2.0\"` (OpenAPI 2.0) specification.
- `basePath` is typically `/restconf`.
- Tags and path groupings follow the F5OS/restconf model used in clouddocs.

## Origin (source of truth)

Online documentation:

- F5OS-A 1.8.3 API: https://clouddocs.f5.com/api/rseries-api/F5OS-A-1.8.3-api.html
- F5OS-C 1.8.1 API: https://clouddocs.f5.com/api/velos-api/F5OS-C-1.8.1-api.html

These clouddocs pages (and their subpages) publish or link to the swagger JSON that has been mirrored here.

## Naming and scope

File names roughly map to functional areas or YANG models, for example:

- `f5-tenants.json` – Tenant lifecycle and configuration.
- `f5-tenant-images.json` – Tenant image management.
- `f5-system-*.json` – System services (logging, packages, redundancy, SNMP, telemetry, etc.).
- `f5-platform*.json`, `openconfig-platform.json` – Hardware/platform-related objects.
- `openconfig-interfaces.json`, `openconfig-lacp.json` – Interface and LACP configuration.
- `f5-mgmt-ip.json`, `f5-mgmt-vlan.json` – Management networking.
- `f5-aaa-*.json`, `f5-openconfig-aaa-*.json`, `f5-system-aaa.json` – AAA, authn/authz, TLS, and related services.

Use the file whose name matches the domain you’re working in, and consult its `paths` section for the exact REST endpoints.

## Usage in F5KSI

- When designing F5OS API workflows, copy endpoint paths, verbs, and payload structures directly from these specs.
- Any examples, runbooks, or tooling in `f5os/` and `automation/` should stay consistent with these swagger definitions.
- If you need to verify a field, type, or allowable value, inspect the relevant schema definitions in these files.

If the upstream clouddocs specs change (e.g., newer F5OS versions), update these JSON files and note the change in this README.

