# crypto-platform 一键安装脚本（Windows / PowerShell）
#
# 用法（管理员 PowerShell，先装好 Docker Desktop）：
#   irm https://raw.githubusercontent.com/flybace/crypto-platform/main/install.ps1 | iex
#
# 或在已克隆的仓库里：
#   .\install.ps1
#
# 私有仓库：先在 GitHub 生成只读 token（Fine-grained，Contents: Read-only，
# 仅勾选本仓库），然后：
#   $env:GITHUB_TOKEN = "你的token"
#   irm -Headers @{{Authorization="Bearer $env:GITHUB_TOKEN"}} `
#     https://raw.githubusercontent.com/flybace/crypto-platform/main/install.ps1 | iex
#
# 重复运行 = 更新。
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/flybace/crypto-platform.git"
$InstallDir = if ($env:CRYPTO_INSTALL_DIR) { $env:CRYPTO_INSTALL_DIR } else { "$HOME\crypto-platform" }
$AdminUser = if ($env:CRYPTO_ADMIN_USERNAME) { $env:CRYPTO_ADMIN_USERNAME } else { "flybace" }

function Log($msg) { Write-Host "[install] $msg" -ForegroundColor Blue }

# ---------- 1. 自举：拉代码 ----------
$inRepo = (Test-Path ".\compose.yaml") -and (Test-Path ".\.git")
if (-not $inRepo) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "需要先安装 git：https://git-scm.com/downloads" }
  $gitAuth = @()
  if ($env:GITHUB_TOKEN) { $gitAuth = @("-c", "http.extraHeader=Authorization: Bearer $($env:GITHUB_TOKEN)") }
  if (-not (Test-Path "$InstallDir\.git")) {
    Log "拉取代码到 $InstallDir ..."
    & git @gitAuth clone --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "git clone 失败。私有仓库请先 `$env:GITHUB_TOKEN='只读token' 再运行（见脚本头部说明）" }
  } else {
    Log "更新代码 ..."
    & git -C $InstallDir @gitAuth pull --ff-only 2>$null
  }
  $me = Join-Path $InstallDir "install.ps1"
  if ($PSCommandPath -ne $me) { & $me; exit $LASTEXITCODE }
  Set-Location $InstallDir
} else {
  git pull --ff-only 2>$null
}
Set-Location (Split-Path $PSCommandPath -Parent)
Log "安装目录：$(Get-Location)"

# ---------- 2. 检查 Docker ----------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "需要先安装 Docker Desktop：https://docs.docker.com/get-docker/" }
docker compose version | Out-Null

# ---------- 3. 生成 .env ----------
function New-RandomString($len) {
  $chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
  -join (1..$len | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
}
$generatedPass = ""
if (-not (Test-Path ".env")) {
  Log "生成 .env 配置 ..."
  $adminPass = if ($env:CRYPTO_ADMIN_PASSWORD) { $env:CRYPTO_ADMIN_PASSWORD } else { New-RandomString 16; $generatedPass = $adminPass }
  @(
    "# crypto-platform 本地配置（由 install.ps1 生成，请勿提交）",
    "CRYPTO_ADMIN_USERNAME=$AdminUser",
    "CRYPTO_ADMIN_PASSWORD=$adminPass",
    "CRYPTO_SESSION_SECRET=$(New-RandomString 32)",
    "CRYPTO_POSTGRES_PASSWORD=$(New-RandomString 24)",
    "CRYPTO_BIND_ADDRESS=127.0.0.1",
    "CRYPTO_EXECUTION_MODE=DISABLED"
  ) | Out-File -Encoding utf8 .env
} else {
  Log ".env 已存在，保留现有配置"
}

# ---------- 4. 构建并启动 ----------
Log "构建并启动服务（首次需要几分钟） ..."
docker compose up -d --build

# ---------- 5. 等待后端健康 ----------
Log "等待后端就绪 ..."
$backendPort = if ($env:CRYPTO_BACKEND_PORT) { $env:CRYPTO_BACKEND_PORT } else { "8290" }
$ready = $false
for ($i = 1; $i -le 36; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$backendPort/health" -TimeoutSec 5 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch { Start-Sleep 5 }
}
if (-not $ready) { throw "后端 3 分钟内未就绪，请运行 docker compose logs backend 查看日志" }
Log "后端健康检查通过"

# ---------- 6. 打印访问信息 ----------
$frontendPort = if ($env:CRYPTO_FRONTEND_PORT) { $env:CRYPTO_FRONTEND_PORT } else { "4191" }
Write-Host ""
Write-Host "=============================================="
Write-Host "  crypto-platform 安装完成"
Write-Host "  访问地址： http://127.0.0.1:$frontendPort"
Write-Host "  用户名：   $AdminUser"
if ($generatedPass) { Write-Host "  密码：     $generatedPass  ← 请妥善保存，仅显示一次" } else { Write-Host "  密码：     （沿用 .env 中的已有密码）" }
Write-Host "  执行模式： DISABLED（模拟盘安全，真实下单默认关闭）"
Write-Host "=============================================="
