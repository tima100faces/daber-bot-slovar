#!/bin/bash
# RETIRED. The server no longer auto-pushes to GitHub.
#
# Single source of truth is GitHub `main`. Development happens locally
# (branch -> PR -> merge), and the server DEPLOYS via pull:
#   scripts/daber-dict-deploy.sh
#
# This stub stays so any lingering scheduler call is a harmless no-op
# instead of racing the `main` branch with the local writer.

echo "daber-dict-push.sh is retired — server must not push to main. Use daber-dict-deploy.sh." >&2
exit 0
