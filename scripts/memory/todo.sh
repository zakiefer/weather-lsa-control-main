#!/usr/bin/env bash
set -euo pipefail
mkdir -p ops
md="ops/backlog.md"; ids="ops/.todo_index"
touch "$md" "$ids"
new_id(){ n=1; [ -s "$ids" ] && n=$(( $(tail -n1 "$ids") + 1 )); echo "$n"; }
case "${1:-}" in
  add) text="${2:-}"; [ -z "$text" ] && { echo "usage: todo.sh add \"text\""; exit 2; }
       id=$(new_id); echo "$id" >> "$ids"
       echo "- [ ] (#$id) $text" >> "$md"; echo "added #$id";;
  done) id="${2:-}"; [ -z "$id" ] && { echo "usage: todo.sh done <id>"; exit 2; }
        awk -v id="$id" '{
          if ($0 ~ "\\(\\#" id "\\)") sub("- \\[ \\]", "- [x]");
          print
        }' "$md" > "$md.tmp" && mv "$md.tmp" "$md"
        echo "done #$id";;
  list) grep -n "^- \\[[ x\\]\\] " "$md" || true;;
  *) echo "usage: todo.sh {add|done|list}"; exit 2;;
esac
