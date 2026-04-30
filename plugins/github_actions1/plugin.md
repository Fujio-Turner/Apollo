---
name: github_actions1
description: GitHub Actions plugin for Apollo. Parses .github/workflows/*.yml files to extract jobs as functions, uses declarations as imports, and environment variables.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/github_actions1
author: Fujio Turner
---

# GitHub Actions Plugin

Parses GitHub Actions workflow files (`.github/workflows/*.yml`).
Jobs become functions, action references (`uses:`) become imports, and environment variables become variables.
