---
name: clinical-ai-procurement
description: Work with a local healthcare AI buyer evidence register and its clinical evaluation handoff, without uploading documents or implying clinical certification.
---

# Local Clinical AI Procurement

Use `clinical-ai-eval buyer contract` to discover the supported JSON CLI.
Read `docs/LOCAL_FIRST_BUYER_WORKFLOW.md` for the supported lifecycle.

## Operating rules

- Prefer existing local buyer workspaces; do not overwrite or recreate them.
- Invoke `clinical-ai-eval buyer ...` through the shell, parse its `ok/result/error`
  JSON, and treat nonzero exit codes as failures. Never infer success.
- To register a candidate: `buyer vendor-add PATH --vendor-id ID --name NAME`.
- To record a document: `buyer evidence-add PATH --vendor-id ID --kind KIND --file FILE`.
  The file is copied locally and content-addressed. Never claim its contents
  are clinically validated because it is present.
- Read `buyer status PATH` for missing information and changed documents.
  Treat `decision_grade: false` as binding.
- Use existing `procurement init/add-vendor/ingest-package/compare/export` for
  evaluation evidence, not editable ad hoc result files.
- Freeze requirements, clinical case-pack hash, hazards, thresholds, and judge
  settings **before** running candidates. Do not re-freeze after results appear.
- Only ingest independently verified packages. No combined clinical safety
  score, vendor winner, procurement verdict, or regulatory certification.
- Do not call remote AI APIs or upload buyer documents without explicit user
  authorisation. Never include identifiable patient content in cloud agent prompts.
  An agent may itself be cloud-hosted even though the CLI runs locally.
- Never expose the current unauthenticated loopback console on a network.
- State what was actually checked versus missing, pending review, or experimental.

## Human-facing local dashboard

Use `clinical-ai-eval buyer serve ./hospital-assessment` to launch the loopback-only
browser interface, including first-run setup, vendor management and private
document upload. This command is long-running and prints a session-bearing local
URL. Do **not** copy that token into a cloud chat or log. Default to CLI commands
for automation; use `serve --no-browser` only when the user wants a local UI.

The browser is single-user. Never proxy it to a hospital intranet or expose it
through a tunnel; shared use requires proper multi-user authentication and TLS.

## Useful commands

```bash
clinical-ai-eval buyer contract
clinical-ai-eval buyer status ./hospital-assessment
clinical-ai-eval buyer serve ./hospital-assessment
clinical-ai-eval buyer dossier ./hospital-assessment --out ./dossier-new.md
clinical-ai-eval capabilities --json
```

If a feature is not supported, do not invent a command or bypass integrity gates.
