---
name: k8s_manifest1
description: Kubernetes manifest plugin for Apollo. Parses K8s YAML to extract Deployments/Pods/Services as classes, containers as methods, environment variables, and image references as imports.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/k8s_manifest1
author: Fujio Turner
---

# Kubernetes Manifest Plugin

Parses Kubernetes YAML manifests.
Deployments, Pods, and Services become classes.
Containers become methods.
Environment variables and image references are extracted as variables and imports respectively.
