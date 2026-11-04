#!/usr/bin/env bash
# The AWS CLI without installing it: ./scripts/aws.sh sts get-caller-identity
export MSYS_NO_PATHCONV=1
exec docker run --rm -i -e AWS_PROFILE -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e AWS_REGION -e AWS_DEFAULT_REGION -v "$HOME/.aws:/root/.aws:ro" amazon/aws-cli:2.27.50 "$@"
