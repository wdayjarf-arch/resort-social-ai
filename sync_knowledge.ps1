[CmdletBinding()]
param(
    [string]$ApiBaseUrl = "http://localhost:8000",
    [string]$OrganizationId = "5af35f74-aa72-4245-b8b5-100b5d6d6786",
    [string]$AdminEmail = "wdayjarf@gmail.com",
    [string]$AdminPassword = "Password776666862!",
    [string]$KnowledgeFile = "knowledge.json",
    [int]$HealthTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"

# 1) الانتظار حتى يصبح الـ API جاهزاً
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$ready = $false
Write-Host "Checking API readiness: $ApiBaseUrl/health/ready" -ForegroundColor Cyan

while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "$ApiBaseUrl/health/ready" -Method Get -TimeoutSec 3
        if ($health) { $ready = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $ready) {
    throw "API did not become ready within $HealthTimeoutSeconds seconds."
}
Write-Host "API is ready." -ForegroundColor Green

if (-not (Test-Path -LiteralPath $KnowledgeFile -PathType Leaf)) {
    throw "Knowledge file not found: $KnowledgeFile"
}

$rawJson = Get-Content -LiteralPath $KnowledgeFile -Raw -Encoding UTF8
$knowledgeItems = ConvertFrom-Json $rawJson
if (-not $knowledgeItems -or $knowledgeItems.Count -lt 1) {
    throw "Knowledge file contains no items."
}

# 2) تسجيل الدخول والحصول على التوكن
Write-Host "Logging in as $AdminEmail ..." -ForegroundColor Cyan
$loginBody = "username=$([System.Uri]::EscapeDataString($AdminEmail))&password=$([System.Uri]::EscapeDataString($AdminPassword))"

$login = Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/auth/login" -Method Post -Body $loginBody -ContentType "application/x-www-form-urlencoded"
$accessToken = [string]$login.access_token

if ([string]::IsNullOrWhiteSpace($accessToken)) {
    throw "Login failed: token is missing."
}

$headers = @{
    "Authorization" = "Bearer $accessToken"
    "Accept"        = "application/json"
}

# 3) رفع البيانات مع تعطيل embedding المباشر لتفادي فحص مفتاح OpenAI
$endpoint = "$ApiBaseUrl/api/v1/organizations/$OrganizationId/knowledge?embed_now=false"
$successCount = 0
$failures = [System.Collections.Generic.List[string]]::new()

foreach ($item in $knowledgeItems) {
    $payload = [ordered]@{
        category   = [string]$item.category
        title      = [string]$item.title
        content    = [string]$item.content
        source_url = "https://resort.local/info"
        metadata   = @{
            language = "ar"
            source   = "admin"
        }
    }

    $jsonBody = [System.Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress))

    try {
        $resp = Invoke-RestMethod -Uri $endpoint -Method Post -Headers $headers -Body $jsonBody -ContentType "application/json; charset=utf-8"
        Write-Host "Synced successfully: $($item.title)" -ForegroundColor Green
        $successCount++
    } catch {
        $msg = $_.Exception.Message
        if ($_.ErrorDetails.Message) { $msg = $_.ErrorDetails.Message }
        Write-Warning "Failed: $($item.title)"
        $failures.Add($msg)
    }
}

Write-Host "Synced $successCount of $($knowledgeItems.Count) items." -ForegroundColor Cyan

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    throw "Finished with errors."
}

Write-Host "All knowledge items synced successfully!" -ForegroundColor Green