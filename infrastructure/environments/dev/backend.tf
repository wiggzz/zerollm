terraform {
  backend "s3" {
    bucket       = "zerollm-terraform-state-265978616089-us-east-2"
    key          = "infrastructure/dev/terraform.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
}
