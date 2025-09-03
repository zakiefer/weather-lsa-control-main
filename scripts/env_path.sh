# This script sets up the environment for the application
#!/usr/bin/env bash
set -euo pipefail
# Add user-level Python scripts (ruff/pyright) to PATH on macOS
export PATH="$HOME/Library/Python/3.13/bin:$HOME/.local/bin:$PATH"
exec "$@"
#!/usr/bin/env bash
set -euo pipefail
# Add user-level Python scripts (ruff/pyright) to PATH on macOS
export PATH="$HOME/Library/Python/3.13/bin:$PATH"
export PATH="$HOME/.local/bin:$PATH"
exec "$@"
