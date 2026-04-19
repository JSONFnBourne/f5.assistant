# F5KSI Workspace

F5KSI is a focused workspace for building and maintaining knowledge, examples, and automation around F5 platforms and modules:

- TMOS-based BIG-IP
- F5OS-A and F5OS-C
- LTM, DNS, APM, ASM/Advanced WAF, SSLO, SWG

This repo is designed to pair with an expert-level F5 assistant to accelerate design, configuration, troubleshooting, and automation work.

## Goals

- Provide a single place to iterate on F5-related:
  - Config snippets (tmsh, REST, AS3/DO, TS, FAST)
  - Runbooks and troubleshooting guides
  - Automation examples (Ansible, Terraform, CI/CD integrations)
- Capture environment-specific assumptions for the F5KSI lab or deployment.

For F5OS-A and F5OS-C, this workspace treats specific clouddocs pages as the source of truth for API and CLI behavior:

- F5OS-A 1.8.3 API: https://clouddocs.f5.com/api/rseries-api/F5OS-A-1.8.3-api.html
- F5OS-C 1.8.1 API: https://clouddocs.f5.com/api/velos-api/F5OS-C-1.8.1-api.html
- F5OS-A 1.8.3 CLI: https://clouddocs.f5.com/api/rseries-api/F5OS-A-1.8.3-cli.html
- F5OS-C 1.8.1 CLI: https://clouddocs.f5.com/api/velos-api/F5OS-C-1.8.1-cli.html

## Getting Started

1. Open this folder (`F5KSI`) in VS Code.
2. Accept the recommended extensions when prompted.
3. Use the snippets under `.antigravity/snippets/f5.code-snippets` (e.g., `f5env`) to quickly describe your environment and tasks to an AI assistant.
4. Use `.antigravity/system_prompt.md` as the base system prompt for any Chat/AI tooling that supports it.

## Next Steps

The initial directory structure now includes:

- `tmos/` – TMOS/BIG-IP-specific content (onboarding, networking, modules).
- `f5os/` – F5OS-A/C platform, tenants, and controller-related material.
- `automation/` – AS3/DO/TS, Ansible, Terraform, and CI/CD examples.
- `irules/` – iRules libraries, examples, and validation approaches.
- `ltm/`, `dns/`, `apm/`, `asm/`, `sslo/`, `swg/` – Module-focused silos.

- `references/` – Local mirrors and index of authoritative F5OS-A/C clouddocs (API and CLI).
- `f5os/api/` – Machine-readable Swagger (OpenAPI 2.0) specs for F5OS-A/C REST APIs, mirrored from clouddocs and treated as canonical.

Next, start populating these folders with concrete examples and runbooks that match your current or planned F5KSI environment.

As we grow this workspace, we can refine structure, add templates, and introduce opinionated best practices tailored to your use cases.
## Application: F5 iRule Assistant

This workspace includes a tailored Next.js application for generating and validating iRules.

### Option 1: Run Locally (Node.js)
Requires Node.js 18+ installed.

1. Install dependencies:
   ```bash
   cd ~/Projects/F5KSI
   npm install
   ```
2. Start the development server:
   ```bash
   npm run dev
   ```
3. Open [http://localhost:3000](http://localhost:3000)

### Option 2: Run with Docker (Portable)
Recommended for sharing or running without setting up a Node environment. Requires Docker Desktop.

1. Build the image:
   ```bash
   docker build -t f5-irule-assistant .
   ```
2. Run the container:
   ```bash
   docker run -p 3000:3000 f5-irule-assistant
   ```
3. Open [http://localhost:3000](http://localhost:3000)

---
