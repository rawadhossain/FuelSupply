variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "fuel-platform"
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.30"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.60.0.0/16"
}

variable "node_instance_type" {
  description = "Managed node group instance type"
  type        = string
  default     = "t3.medium"
}

variable "node_desired_size" {
  description = "Desired managed node group size"
  type        = number
  default     = 2
}

variable "github_owner" {
  description = "GitHub org/user that owns this repo (for the OIDC-trusted deploy role)"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo name (for the OIDC-trusted deploy role)"
  type        = string
}
