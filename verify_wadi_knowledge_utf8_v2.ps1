[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://localhost:8000',
    [string]$OrganizationId = '5af35f74-aa72-4245-b8b5-100b5d6d6786'
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')

Write-Host ''
Write-Host '========== READ-ONLY UTF-8 VERIFICATION ==========' -ForegroundColor Cyan
Write-Host 'This script does not create, update, or delete knowledge items.'

$username = Read-Host 'Login email'
$securePassword = Read-Host 'Password (hidden)' -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential($username, $securePassword)
$password = $credential.GetNetworkCredential().Password

try {
    $login = Invoke-RestMethod -Method Post `
        -Uri "$BaseUrl/api/v1/auth/login" `
        -ContentType 'application/x-www-form-urlencoded' `
        -Body @{ username = $username; password = $password } `
        -ErrorAction Stop
} finally {
    $password = $null
    $credential = $null
    $securePassword = $null
}

$token = $null
foreach ($name in @('access_token', 'token')) {
    $prop = $login.PSObject.Properties[$name]
    if ($prop -and $prop.Value) { $token = [string]$prop.Value; break }
}
if (-not $token -and $login.data) {
    foreach ($name in @('access_token', 'token')) {
        $prop = $login.data.PSObject.Properties[$name]
        if ($prop -and $prop.Value) { $token = [string]$prop.Value; break }
    }
}
if (-not $token) { throw 'Login response did not contain an access token.' }
Write-Host 'Login succeeded.' -ForegroundColor Green

# Build Arabic query from JSON Unicode escapes; the script remains ASCII-only.
$query = ConvertFrom-Json '"\u0645\u0646\u062a\u062c\u0639 \u0648\u0627\u062f\u064a \u062c\u0627\u0631\u0641 \u0627\u0644\u0623\u0633\u0639\u0627\u0631 \u0648\u0627\u0644\u0648\u062d\u062f\u0627\u062a \u0648\u0627\u0644\u062d\u062c\u0632"'
$queryBody = @{ query = $query; top_k = 10 } | ConvertTo-Json -Compress
$headers = @{ Authorization = "Bearer $token"; Accept = 'application/json' }
$uri = "$BaseUrl/api/v1/organizations/$OrganizationId/knowledge/search"
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($queryBody)

$response = Invoke-WebRequest -UseBasicParsing -Method Post `
    -Uri $uri -Headers $headers `
    -ContentType 'application/json; charset=utf-8' `
    -Body $bodyBytes -ErrorAction Stop

$contentType = [string]$response.Headers['Content-Type']
Write-Host "Response Content-Type: $contentType"

$stream = $response.RawContentStream
if ($null -eq $stream) { throw 'The response did not expose a raw content stream.' }
$rawBytes = $stream.ToArray()
$jsonText = [System.Text.Encoding]::UTF8.GetString($rawBytes)
$result = ConvertFrom-Json -InputObject $jsonText

if ($result -is [System.Array]) {
    $items = @($result)
} elseif ($result.results) {
    $items = @($result.results)
} elseif ($result.items) {
    $items = @($result.items)
} else {
    $items = @($result)
}

Write-Host "Results returned: $($items.Count)"
$arabicTitles = 0
$mojibakeTitles = 0
foreach ($item in $items) {
    $title = [string]$item.title
    if ($title -match '[\u0600-\u06FF]') { $arabicTitles++ }
    if ($title -match '[\u00D8\u00D9][\u0080-\u00FF]') { $mojibakeTitles++ }
    Write-Host ("Title: {0} | Category: {1} | Similarity: {2}" -f $title, $item.category, $item.similarity)
}

if ($items.Count -gt 0) {
    $content = [string]$items[0].content
    $containsArabic = $content -match '[\u0600-\u06FF]'
    $containsMojibake = $content -match '[\u00D8\u00D9][\u0080-\u00FF]'
    Write-Host "First result content contains Arabic characters: $containsArabic"
    Write-Host "First result content has common mojibake pattern: $containsMojibake"
    Write-Host 'First result content preview:'
    if ($content.Length -gt 700) { $content = $content.Substring(0, 700) }
    Write-Host $content
}

Write-Host ''
if ($items.Count -gt 0 -and $arabicTitles -gt 0 -and $containsArabic -and -not $containsMojibake) {
    Write-Host 'PASS: Raw UTF-8 response contains readable Arabic. The earlier display issue is likely PowerShell response decoding.' -ForegroundColor Green
} elseif ($items.Count -gt 0 -and $containsMojibake) {
    Write-Host 'WARNING: Raw UTF-8 response itself contains mojibake patterns. Do not re-import; database content needs a controlled repair.' -ForegroundColor Yellow
} else {
    Write-Host 'INCONCLUSIVE: Inspect the returned titles and preview before making changes.' -ForegroundColor Yellow
}
