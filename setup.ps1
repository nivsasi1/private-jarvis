# Private Jarvis one-time setup. Run from the project folder:  .\setup.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Write-Host "== Private Jarvis setup ==" -ForegroundColor Cyan

# 1. virtual environment + Python deps
if (-not (Test-Path "$root\.venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv "$root\.venv"
}
$py  = "$root\.venv\Scripts\python.exe"
$pip = "$root\.venv\Scripts\pip.exe"
& $py -m pip install --quiet --upgrade pip
Write-Host "Installing dependencies (this can take a few minutes)..."
& $pip install --quiet -r "$root\requirements.txt"

# 2. NVIDIA GPU runtime for faster-whisper
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "NVIDIA GPU detected - installing CUDA runtime wheels..."
    & $pip install --quiet nvidia-cublas-cu12 nvidia-cudnn-cu12
} else {
    Write-Host "No NVIDIA GPU - STT will run on CPU." -ForegroundColor Yellow
}

# 3. wake-word models
Write-Host "Downloading Hey Jarvis wake-word models..."
& $py -c "import openwakeword.utils as u; u.download_models()"

# 4. Ollama models (local brain + memory embeddings)
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if (Test-Path $ollama) {
    Write-Host "Pulling Ollama models (gemma3:12b, bge-m3)..."
    & $ollama pull gemma3:12b
    & $ollama pull bge-m3
} else {
    Write-Host "Ollama not installed. Install it, then pull gemma3:12b and bge-m3." -ForegroundColor Yellow
}

# 5. secrets (Anthropic key for the Claude brain + tools)
if (-not (Test-Path "$root\secret.yaml")) {
    $q = [char]34
    Set-Content -Encoding utf8 -Path "$root\secret.yaml" -Value ("anthropic_key: " + $q + $q)
    Write-Host "Created secret.yaml - paste your Anthropic API key into it." -ForegroundColor Yellow
}

# 6. desktop shortcut
$ws = New-Object -ComObject WScript.Shell
$dest = "$([Environment]::GetFolderPath('Desktop'))\Jarvis.lnk"
$s = $ws.CreateShortcut($dest)
$s.TargetPath = "$root\.venv\Scripts\pythonw.exe"
$s.Arguments = "-m jarvis"
$s.WorkingDirectory = $root
if (Test-Path "$root\jarvis.ico") { $s.IconLocation = "$root\jarvis.ico" }
$s.Save()

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  1. Put your Anthropic key in secret.yaml"
Write-Host "  2. (optional Gmail) place google_credentials.json here, then run: .venv\Scripts\python -m jarvis --auth-gmail"
Write-Host "  3. Verify: .venv\Scripts\python -m jarvis --check"
Write-Host "  4. Launch with the Jarvis desktop icon."
