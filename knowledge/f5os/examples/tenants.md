# F5OS Tenants API Examples (F5OS-A 1.8.3 / F5OS-C 1.8.1)

These examples show how to manage tenants via the F5OS REST API using the swagger spec in `f5os/api/f5-tenants.json`. Paths and payloads are aligned with the clouddocs source of truth for F5OS-A 1.8.3 and F5OS-C 1.8.1.

Assumptions:

- Base URL: `https://<f5os-host>`
- API root: `/restconf`
- Auth: HTTP Basic to the F5OS controller or chassis (adjust for your environment).
- Content type: `application/yang-data+json`

## List all tenants

**Spec reference:** `f5os/api/f5-tenants.json`, path `/data/f5-tenants:tenants` `GET`

Request:

```http
GET /restconf/data/f5-tenants:tenants HTTP/1.1
Host: <f5os-host>
Authorization: Basic <base64 user:pass>
Accept: application/yang-data+json
```

## Create a new tenant

**Spec reference:** `f5os/api/f5-tenants.json`, path `/data/f5-tenants:tenants` `POST` (`data_f5-tenants_tenants-post`)

Minimal example creating a BIG-IP tenant with management networking:

```http
POST /restconf/data/f5-tenants:tenants HTTP/1.1
Host: <f5os-host>
Authorization: Basic <base64 user:pass>
Content-Type: application/yang-data+json
Accept: application/yang-data+json

{
  "f5-tenants:tenant": [
    {
      "config": {
        "f5-tenants:name": "tenant1",
        "f5-tenants:type": "BIG-IP",
        "f5-tenants:mgmt-ip": "192.0.2.10",
        "f5-tenants:prefix-length": 24,
        "f5-tenants:gateway": "192.0.2.1",
        "f5-tenants:vlans": [
          "vlan-10"
        ],
        "f5-tenant-mgmt-vlan:mgmt-vlan": "mgmt-vlan"
      }
    }
  ]
}
```

Adjust `vlans` and `mgmt-vlan` values to match existing VLAN objects referenced by the platform.

## Show a specific tenant

**Spec reference:** `f5os/api/f5-tenants.json`, path `/data/f5-tenants:tenants/tenant={tenant-name}` `GET`

Request for `tenant1`:

```http
GET /restconf/data/f5-tenants:tenants/tenant=tenant1 HTTP/1.1
Host: <f5os-host>
Authorization: Basic <base64 user:pass>
Accept: application/yang-data+json
```

## Update tenant management IP

**Spec reference:** `f5os/api/f5-tenants.json`, path `/data/f5-tenants:tenants/tenant={tenant-name}/config` `PATCH`

Patch the management IP for `tenant1`:

```http
PATCH /restconf/data/f5-tenants:tenants/tenant=tenant1/config HTTP/1.1
Host: <f5os-host>
Authorization: Basic <base64 user:pass>
Content-Type: application/yang-data+json
Accept: application/yang-data+json

{
  "f5-tenants:mgmt-ip": "192.0.2.11",
  "f5-tenants:prefix-length": 24,
  "f5-tenants:gateway": "192.0.2.1"
}
```

## Delete a tenant

**Spec reference:** `f5os/api/f5-tenants.json`, path `/data/f5-tenants:tenants/tenant={tenant-name}` `DELETE`

Request to delete `tenant1`:

```http
DELETE /restconf/data/f5-tenants:tenants/tenant=tenant1 HTTP/1.1
Host: <f5os-host>
Authorization: Basic <base64 user:pass>
Accept: application/yang-data+json
```

## Safety and validation

- Before destructive changes, capture current state:
  - `GET /restconf/data/f5-tenants:tenants/tenant=<name>` and save the response.
- After create/patch:
  - Re-run a `GET` for the specific tenant and check `config` and `state` sections match expectations.
- Monitor tenant lifecycle:
  - Use `state` paths (e.g., `.../state/running-state`, `.../state/status`) from the swagger definitions to verify deployment state.

Always cross-check fields and allowable values against the corresponding definitions in `f5os/api/f5-tenants.json` when building more advanced payloads.

