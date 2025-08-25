#!/usr/bin/env sh
# Minimal check for TAVILY_API_KEY presence without printing it
if [ -n "${TAVILY_API_KEY:-}" ]; then
  echo set
else
  echo missing
fi
