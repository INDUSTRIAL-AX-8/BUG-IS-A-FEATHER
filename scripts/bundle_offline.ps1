# 인터넷 되는 PC 에서 실행: 폐쇄망 서버로 옮길 설치 묶음을 만든다.
#   powershell -ExecutionPolicy Bypass -File scripts\bundle_offline.ps1 [-TargetLinux] [-Model qwen2.5:7b-instruct]
# 결과: offline_bundle\ (app 소스, frontend\dist, wheels, ollama 모델 폴더 안내) + offline_bundle.zip
param(
    [switch]$TargetLinux,          # 대상 서버가 Linux x86_64 이면 지정
    [string]$PythonVersion = "3.11",
    [string]$Model = ""            # 함께 반입할 Ollama 모델 (선택)
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "offline_bundle"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force "$Out\wheels" | Out-Null

Write-Host "[1/4] 소스 복사 (git 추적 파일만)"
Push-Location $Root
git archive --format=zip -o "$Out\app.zip" HEAD
Pop-Location

Write-Host "[2/4] 프론트엔드 빌드"
Push-Location "$Root\frontend"
npm ci
npm run build
Pop-Location
Copy-Item -Recurse "$Root\frontend\dist" "$Out\frontend_dist"

Write-Host "[3/4] Python wheel 다운로드"
$lock = "$Root\backend\requirements.lock"
if ($TargetLinux) {
    python -m pip download -r $lock -d "$Out\wheels" --only-binary=:all: --platform manylinux2014_x86_64 --python-version $PythonVersion
} else {
    python -m pip download -r $lock -d "$Out\wheels"
}

Write-Host "[4/4] Ollama 모델"
if ($Model) {
    ollama pull $Model
    $models = Join-Path $env:USERPROFILE ".ollama\models"
    Copy-Item -Recurse $models "$Out\ollama_models"
    Write-Host "  모델 폴더를 복사했습니다: $Out\ollama_models (서버의 ~/.ollama/models 로 옮기세요)"
} else {
    Write-Host "  -Model 미지정: 모델은 docs\OFFLINE_INSTALL.md 의 '모델 반입' 절차로 따로 옮기세요."
}
Write-Host "  Ollama 설치 파일은 https://ollama.com/download 에서 대상 OS 용으로 받아 함께 넣으세요."

Compress-Archive -Path "$Out\*" -DestinationPath "$Root\offline_bundle.zip" -Force
Write-Host "완료: $Root\offline_bundle.zip"
