[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://localhost:8000',
    [string]$OrganizationId = '5af35f74-aa72-4245-b8b5-100b5d6d6786'
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')

Write-Host "`n========== تسجيل الدخول للتحقق ==========" -ForegroundColor Cyan
$username = Read-Host 'البريد الإلكتروني'
$securePassword = Read-Host 'كلمة المرور (لن تُحفظ)' -AsSecureString
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
foreach ($name in @('access_token','token')) {
    if ($login.PSObject.Properties[$name] -and $login.$name) { $token = [string]$login.$name; break }
}
if (-not $token -and $login.data) {
    foreach ($name in @('access_token','token')) {
        if ($login.data.PSObject.Properties[$name] -and $login.data.$name) { $token = [string]$login.data.$name; break }
    }
}
if (-not $token) { throw 'نجح طلب الدخول أو عاد برد غير متوقع، لكن لم يتم العثور على access_token.' }

Write-Host 'تم تسجيل الدخول.' -ForegroundColor Green
Write-Host "`n========== اختبار البحث وقراءة UTF-8 الخام ==========" -ForegroundColor Cyan

$queryBody = @{
    query = 'منتجع وادي جارف: الملف التعريفي والوحدات والأسعار والحجز وسياسة الدفع'
    top_k = 10
} | ConvertTo-Json -Compress

$headers = @{ Authorization = "Bearer $token"; Accept = 'application/json' }
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($queryBody)
$uri = "$BaseUrl/api/v1/organizations/$OrganizationId/knowledge/search"

$response = Invoke-WebRequest -UseBasicParsing -Method Post `
    -Uri $uri -Headers $headers `
    -ContentType 'application/json; charset=utf-8' `
    -Body $bodyBytes -ErrorAction Stop

$rawBytes = $response.RawContentStream.ToArray()
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

Write-Host "عدد النتائج المعادة: $($items.Count)"
$arabicCount = 0
foreach ($item in $items) {
    $title = [string]$item.title
    $hasArabic = $title -match '[\u0600-\u06FF]'
    if ($hasArabic) { $arabicCount++ }
    $status = if ($hasArabic) { 'عربي سليم' } else { 'تحقق من الترميز' }
    Write-Host ("- [{0}] {1} | {2} | similarity={3}" -f $status, $title, $item.category, $item.similarity)
}

if ($items.Count -gt 0) {
    $firstContent = [string]$items[0].content
    $contentHasArabic = $firstContent -match '[\u0600-\u06FF]'
    Write-Host "`nهل يحتوي نص أول نتيجة على حروف عربية؟ $contentHasArabic"
    Write-Host 'مقتطف من أول نتيجة:'
    if ($firstContent.Length -gt 500) { $firstContent = $firstContent.Substring(0, 500) }
    Write-Host $firstContent
}

if ($items.Count -gt 0 -and $arabicCount -gt 0 -and $contentHasArabic) {
    Write-Host "`nنجح التحقق: استجابة API الخام تحتوي نصاً عربياً سليماً بعد فك UTF-8 صراحةً." -ForegroundColor Green
} else {
    Write-Warning 'لم يثبت الاختبار سلامة العربية في استجابة البحث. لا تعِد رفع العناصر؛ أرسل هذه النتيجة لمراجعة التخزين والترميز.'
}
