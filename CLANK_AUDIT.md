# Clank Reborn audit handoff

The current release candidate is Ollama-only and local-first. The product
provider is Ollama; external model access used during engineering review is not
part of the product or its configuration.

Intentional audit fixes retained in this working tree include explicit UTF-8
Git decoding on Windows, redacted scanner findings, expanded private-artifact
ignore coverage, and final output-size enforcement for fast-exiting commands.

The release validation record is in `RELEASE_VALIDATION.md`. It supersedes
older verbose checkpoint reports. Do not publish credentials, databases,
backups, model files, private exports, or local control artifacts.
