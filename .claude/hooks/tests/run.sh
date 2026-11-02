#!/usr/bin/env bash
# Run the hook tests. Zero dependencies: node --test (Node 20+).
cd "$(dirname "${BASH_SOURCE[0]}")" && exec node --test ./*.test.mjs
