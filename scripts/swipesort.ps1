# SwipeSort — common tasks on Windows (PowerShell 5.1+ or 7). macOS / Linux: make <task>
#   powershell -ExecutionPolicy Bypass -File scripts\swipesort.ps1 <task>
param(
  [Parameter(Position = 0)][string]$Task = "help",
  [string]$Model = "qwen2.5:7b-instruct",
  [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function In($dir, [scriptblock]$block) {
  Push-Location (Join-Path $Root $dir)
  try { & $block; if ($LASTEXITCODE) { exit $LASTEXITCODE } } finally { Pop-Location }
}
function Serve([string[]]$extra) { In backend { uv run swipesort serve --port $Port @extra } }

$tasks = [ordered]@{
  help           = "List tasks"
  doctor         = "Check prerequisites"
  setup          = "Install dependencies and build the UI"
  ui             = "Rebuild the UI after frontend changes"
  run            = "Start on this computer only (http://127.0.0.1:8765)"
  lan            = "Start and allow paired phones on the local network"
  watch          = "Start with watched folders (see config: [watch])"
  pair           = "Show a pairing QR for a running 'lan'"
  llm            = "Download the LLM (default qwen2.5:7b-instruct)"
  "llm-eco"      = "Download the small LLM for low-RAM / CPU-only machines"
  agent          = "Start SwipeSort (watch mode) at login"
  "agent-remove" = "Stop starting at login"
  "agent-status" = "Is the login agent installed?"
  test           = "Run all tests (no models needed)"
}

switch ($Task) {
  "help" {
    foreach ($k in $tasks.Keys) { "  {0,-14} {1}" -f $k, $tasks[$k] }
  }
  "doctor" {
    if (-not (Have uv)) { throw "missing: uv  -> winget install --id=astral-sh.uv -e" }
    if (-not (Have node)) { throw "missing: node 20+  -> winget install OpenJS.NodeJS.LTS" }
    $major = [int]((node -v).TrimStart("v").Split(".")[0])
    if ($major -lt 20) { throw "node 20+ required (found $(node -v))" }
    if (Have ollama) { "ok: ollama (LLM features on)" } else { "optional: ollama not found (LLM features off) -> winget install Ollama.Ollama" }
    "ok: uv, node"
  }
  "setup" {
    & $PSCommandPath doctor
    In backend { uv sync }
    In frontend { npm ci; if (-not $LASTEXITCODE) { npm run build } }
  }
  "ui"            { In frontend { npm run build } }
  "run"           { Serve -extra @() }
  "lan"           { Serve -extra @("--lan") }
  "watch"         { Serve -extra @("--watch") }
  "pair"          { In backend { uv run swipesort pair --port $Port } }
  "llm"           { ollama pull $Model }
  "llm-eco"       { ollama pull qwen2.5:3b-instruct }
  "agent"         { In backend { uv run swipesort agent install } }
  "agent-remove"  { In backend { uv run swipesort agent uninstall } }
  "agent-status"  { In backend { uv run swipesort agent status } }
  "test" {
    In backend { uv run pytest -q }
    In frontend { npm test -- --run }
  }
  default { Write-Error "unknown task '$Task' (run: scripts\swipesort.ps1 help)"; exit 2 }
}
