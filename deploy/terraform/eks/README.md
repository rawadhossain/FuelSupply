# EKS (write-only — never applied)

This Terraform is written for review and CI validation only (`terraform validate` /
`terraform fmt -check` with `init -backend=false`, no AWS credentials). No one has
run `terraform apply`. There is no AWS account wired to this repo.

## What it creates

- A VPC (`terraform-aws-modules/vpc`) with 2 AZs, public+private subnets, single NAT.
- An EKS cluster (`terraform-aws-modules/eks`), public endpoint enabled, one managed
  node group of `t3.medium` (default 2 nodes).
- A GitHub Actions OIDC provider and a deploy role trusted only for
  `repo:<github_owner>/<github_repo>:*`, scoped to describing/accessing this cluster
  (no static AWS keys anywhere).

## Variables to set

`github_owner`, `github_repo` are required (no default — they gate the OIDC trust
policy). Everything else has a sane default; override via `terraform.tfvars` or `-var`.

## Before a real apply

1. `terraform init` (with a real backend — none is configured here on purpose).
2. `terraform plan` and read it end to end.
3. Install the AWS Load Balancer Controller (needed for the `alb` Ingress class
   referenced by `values-eks.yaml`):
   ```
   helm repo add eks https://aws.github.io/eks-charts
   helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
     -n kube-system --set clusterName=<cluster_name> --set serviceAccount.create=true
   ```
   It also needs its own IRSA role (`AWSLoadBalancerControllerIAMPolicy`) — not created
   by this Terraform; add it before relying on the Ingress in `values-eks.yaml`.
4. Point `.github/workflows/deploy-eks.yml`'s `role-to-assume` at
   `github_actions_deploy_role_arn` (Terraform output) and run it via `workflow_dispatch`.
