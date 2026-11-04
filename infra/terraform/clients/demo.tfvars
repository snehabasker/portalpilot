# One file per client deployment:  terraform apply -var-file=clients/demo.tfvars
client = "demo"

# the organization policy on this account only allows eu-north-1
region = "eu-north-1"

# the account is on the AWS Free plan (RDS backup limits)
free_tier_account = true

deploy_mock_portal = true
worker_count       = 1

# add qwen / claude once qwen_url / anthropic_api_key are set
grounder_chain = "cache,dom"

# allowed_cidrs = ["203.0.113.0/24"]   # lock the console to the client's network
# anthropic_api_key is passed via TF_VAR_anthropic_api_key, never committed
