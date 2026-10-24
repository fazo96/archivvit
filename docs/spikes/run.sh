#!/usr/bin/env bash
# usage: run.sh script.py args...
EXPR='with import (builtins.getFlake "nixpkgs") {}'
export PLAYWRIGHT_BROWSERS_PATH=$(nix eval --impure --raw --expr "$EXPR; \"\${playwright-driver.browsers}\"")
export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
exec nix shell --impure --expr "$EXPR; [(python3.withPackages (ps: [ps.playwright ps.requests ps.curl-cffi])) playwright-driver.browsers]" --command python3 -I "$@"
