#!/usr/bin/env bash
set -euo pipefail

ROOT="${PWD}"
SETTINGS_PATH="${ROOT}/.vscode/settings.json"

echo "== ENV CHECK =="
if [ -f ".env.local" ]; then
  echo ".env.local present"
  if grep -q '^NOTION_API_KEY=' .env.local; then
    echo "NOTION_API_KEY is set (value hidden)"
  else
    echo "NOTION_API_KEY NOT FOUND in .env.local"
  fi
else
  echo ".env.local missing"
fi
echo

echo "== SETTINGS CHECK =="
if [ -f "${SETTINGS_PATH}" ]; then
  echo "settings.json found at: ${SETTINGS_PATH}"
  AUTOSTART=$(node -e '
    const fs=require("fs");
    const p=process.argv[1];
    try{
      const j=JSON.parse(fs.readFileSync(p,"utf8"));
      const s=j["mcp.servers"]||{};
      const n=s["notion"]||{};
      const a=(n.autoStart===true)?"true":"false";
      console.log(a);
    }catch(e){ console.log("unknown"); }
  ' "${SETTINGS_PATH}" || true)
  echo "notion autoStart: ${AUTOSTART}"
else
  echo "No .vscode/settings.json found"
fi
echo

echo "== PACKAGE CHECK =="
if [ -d "node_modules/@modelcontextprotocol/server-notion" ]; then
  echo "@modelcontextprotocol/server-notion is installed"
else
  echo "@modelcontextprotocol/server-notion NOT installed"
fi

echo
echo "== NEXT ACTIONS =="
echo "Open VS Code → View → Output → MCP Servers"
echo "Confirm there is a line showing 'notion' Running."
echo "Then run tools/list in Copilot Chat."

