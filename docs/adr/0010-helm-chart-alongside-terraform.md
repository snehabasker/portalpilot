# 0010. A Helm chart alongside the ECS Terraform

* Status: accepted
* Date: 2026-11-06
* Deciders: Sneha Basker

## Context

The ECS Terraform (ADR 0008) fits clients who give us an AWS account. Others run everything on their own Kubernetes
platform (EKS, AKS, OpenShift, on-prem), with their own ingress, secrets, monitoring and admission policies, and
their platform teams install workloads from Helm charts. Forward-deployed work means deploying into what the client
already operates, not asking them to adopt our stack.

## Decision

Ship a Helm chart (`deploy/helm/portalpilot`) for the application tier (api, worker, web, relay, optional mock
portal), designed for **bring-your-own** Postgres, Temporal and S3. Dev-only bundled dependencies (single-replica
Postgres, Temporal auto-setup, S3 mock) exist so the same chart runs on `kind` in CI. Platform integration is
optional and off by default: Ingress, ServiceMonitor, PrometheusRule (the same rule files as the docker compose
stack), NetworkPolicy, HPA, and service-account annotations for IRSA or pod identity.

Choices inside the chart:

* Workers get `terminationGracePeriodSeconds: 75`, a memory-backed `/dev/shm` for Chromium, a PDB of
  `maxUnavailable: 1`, and `maxUnavailable: 0` rolling updates. Session affinity is handled by Temporal (ADR 0004),
  so pods need no stickiness.
* Secrets come from an `existingSecret` in production (External Secrets, Vault, Sealed Secrets). A chart-generated
  operator token persists across upgrades via `lookup`.
* Pods run as non-root (UID 10001) with all capabilities dropped and the RuntimeDefault seccomp profile.
* Migrations run at API startup under an advisory lock (unchanged), not in a Helm hook, so `helm upgrade --wait`
  and GitOps tools behave the same way.

## Consequences

* There are two deployment artifacts to keep in step. CI renders the chart with kubeconform, installs it on `kind`,
  runs the smoke test and the chaos e2e against it, and diffs the chart's copy of the alert rules against the source.
* Bundled dependencies are deliberately minimal and flagged in NOTES.txt. The chart is not an operator for
  Postgres or Temporal and should not become one.
* HPA scales on CPU, which is a coarse proxy. The Temporal task-queue backlog via KEDA is the better signal; it is
  left to clients who run KEDA.

## Alternatives considered

* **Kustomize only.** Good for one client's overlays, weaker as a distributable artifact with options.
* **Terraform's `helm_release` / EKS module.** It ties the Kubernetes path back to AWS and to our Terraform, while
  these clients usually have their own GitOps (Argo CD, Flux) that consumes charts directly.
* **An operator.** Far more than the problem needs.
