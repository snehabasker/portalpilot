# 0008. ECS Fargate and one Terraform state per client

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

Client data (claims, screenshots with PII, portal credentials) should stay in the client's own AWS account. Each
deployment must be repeatable by an FDE in a day, cheap enough to run as a pilot, and have nothing to patch at the
node level.

## Decision

The reference deployment is **Terraform** for ECS Fargate (api, worker, web, self-hosted Temporal, optional mock
portal), RDS PostgreSQL, S3, KMS, Secrets Manager, ALB, Cloud Map and CloudWatch, with **one state and one
`clients/<name>.tfvars` per client**. `scripts/deploy.sh <client>` builds, pushes and applies, and needs only Docker
(Terraform and the AWS CLI run from their images).

## Consequences

* A client deployment is one file of variables and has been run end to end (deploy, chaos e2e on AWS, destroy).
* There is no cluster to operate, and the demo costs about $4–5 a day while it is up.
* The demo puts tasks in public subnets with no NAT, to save cost. That is documented, and production should use
  private subnets with VPC endpoints.
* Clients who standardize on Kubernetes need a different artifact (ADR 0010).

## Alternatives considered

* **EKS.** The right home for clients who already run it, but too heavy to stand up per client just for us.
* **One shared multi-tenant SaaS deployment.** It would be cheaper to operate, but most claims clients will not send
  portal credentials and PII to a vendor account in a pilot.
* **CDK.** Fine, but Terraform is what client platform teams most often already review and run.
