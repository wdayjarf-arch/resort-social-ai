[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:8000",
    [string]$OrganizationId = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd('/')
$KnowledgeFolder = Join-Path $PSScriptRoot "wadi_jaref_knowledge_draft"

function Write-Section([string]$Text) {
    Write-Host "`n========== $Text ==========" -ForegroundColor Cyan
}

function Get-PropertyValue($Object, [string[]]$Names) {
    if ($null -eq $Object) { return $null }
    foreach ($name in $Names) {
        $prop = $Object.PSObject.Properties[$name]
        if ($null -ne $prop -and $null -ne $prop.Value -and "$($prop.Value)" -ne "") {
            return $prop.Value
        }
    }
    return $null
}

function Get-AccessToken($Response) {
    $token = Get-PropertyValue $Response @('access_token','token')
    if ($token) { return [string]$token }
    foreach ($containerName in @('data','result','user')) {
        $container = Get-PropertyValue $Response @($containerName)
        if ($container) {
            $token = Get-PropertyValue $container @('access_token','token')
            if ($token) { return [string]$token }
        }
    }
    return $null
}

function Resolve-OpenApiSchema($Schema, $Components, [int]$Depth = 0) {
    if ($null -eq $Schema -or $Depth -gt 8) { return $Schema }
    $ref = Get-PropertyValue $Schema @('$ref')
    if ($ref -and $ref -match '#/components/schemas/(.+)$') {
        $name = $Matches[1]
        return $Components.schemas.PSObject.Properties[$name].Value
    }
    $anyOf = Get-PropertyValue $Schema @('anyOf','oneOf','allOf')
    if ($anyOf) {
        foreach ($part in $anyOf) {
            $resolved = Resolve-OpenApiSchema $part $Components ($Depth + 1)
            if ($resolved -and ($resolved.enum -or $resolved.maxLength -or $resolved.type -eq 'string')) {
                return $resolved
            }
        }
    }
    return $Schema
}

function Get-ListFromResponse($Response) {
    if ($null -eq $Response) { return @() }
    if ($Response -is [System.Array]) { return @($Response) }
    foreach ($name in @('items','organizations','results','data')) {
        $value = Get-PropertyValue $Response @($name)
        if ($value) {
            if ($value -is [System.Array]) { return @($value) }
            if ($value -is [System.Collections.IEnumerable] -and $value -isnot [string]) { return @($value) }
            if ((Get-PropertyValue $value @('id','organization_id','uuid'))) { return @($value) }
        }
    }
    if (Get-PropertyValue $Response @('id','organization_id','uuid')) { return @($Response) }
    return @()
}

function Normalize-Title([string]$Value) {
    if ($null -eq $Value) { return '' }
    return (($Value.Trim().ToLowerInvariant() -replace '\s+',' '))
}

function Get-CategoryValue($CategorySchema, [string]$Fallback) {
    $enumValues = @()
    if ($CategorySchema -and $CategorySchema.enum) { $enumValues = @($CategorySchema.enum) }
    if ($enumValues.Count -eq 0) { return $Fallback }

    $hints = @{
        'resort_profile' = @('profile','general','resort','hotel','basic','info')
        'units_and_facilities' = @('unit','facility','amenity','room','general')
        'pricing_and_services' = @('pricing','price','service','general')
        'booking_and_availability' = @('booking','availability','reservation','general')
        'payment_and_cancellation' = @('payment','cancel','refund','policy','general')
        'activities_and_events' = @('activity','event','general')
        'rules_and_regulations' = @('rule','regulation','policy','general')
        'faq' = @('faq','general')
        'complaints_and_escalation' = @('complaint','support','general')
        'tone_and_persona' = @('tone','persona','general')
    }
    foreach ($hint in $hints[$Fallback]) {
        foreach ($allowed in $enumValues) {
            if (([string]$allowed).ToLowerInvariant().Contains($hint)) { return [string]$allowed }
        }
    }
    foreach ($allowed in $enumValues) {
        if (([string]$allowed).ToLowerInvariant() -eq 'general') { return [string]$allowed }
    }
    return [string]$enumValues[0]
}

function Get-OrganizationIdFromApi($Spec, $Headers) {
    $candidates = @($Spec.paths.PSObject.Properties | Where-Object {
        $_.Name -match 'organizations/?$' -and $_.Name -notmatch '\{' -and $_.Value.get
    } | ForEach-Object { $_.Name })
    foreach ($path in $candidates) {
        try {
            $response = Invoke-RestMethod -Method Get -Uri ($BaseUrl + $path) -Headers $Headers -ErrorAction Stop
            $records = @(Get-ListFromResponse $response)
            $valid = @()
            foreach ($record in $records) {
                $id = Get-PropertyValue $record @('id','organization_id','uuid')
                if ($id) { $valid += [pscustomobject]@{ Id = [string]$id; Name = [string](Get-PropertyValue $record @('name','title','display_name')) } }
            }
            if ($valid.Count -eq 1) { return $valid[0].Id }
            if ($valid.Count -gt 1) {
                Write-Section "اختيار المؤسسة"
                for ($i=0; $i -lt $valid.Count; $i++) {
                    Write-Host ("[{0}] {1}  {2}" -f ($i+1), $valid[$i].Name, $valid[$i].Id)
                }
                $choice = Read-Host "أدخل رقم المؤسسة التي تستخدمها في sync_knowledge.ps1"
                $number = 0
                if ([int]::TryParse($choice, [ref]$number) -and $number -ge 1 -and $number -le $valid.Count) {
                    return $valid[$number-1].Id
                }
            }
        } catch {
            # Try another public organization-list route; if none works, prompt for the ID.
        }
    }
    return $null
}

Write-Section "التحقق من ملفات المعرفة"
if (-not (Test-Path $KnowledgeFolder)) {
    throw "لم أجد مجلد المعرفة: $KnowledgeFolder. استخرج الحزمة بحيث يكون المجلد wadi_jaref_knowledge_draft بجانب هذا السكربت."
}

$documents = @(
    @{ File='01_resort_profile.md'; Title='منتجع وادي جارف - الملف التعريفي'; Category='resort_profile' },
    @{ File='02_units_and_facilities.md'; Title='منتجع وادي جارف - الوحدات والمرافق'; Category='units_and_facilities' },
    @{ File='03_pricing_and_services.md'; Title='منتجع وادي جارف - الأسعار والخدمات وقاعدة التسعير'; Category='pricing_and_services' },
    @{ File='04_booking_and_availability.md'; Title='منتجع وادي جارف - الحجز والتوافر'; Category='booking_and_availability' },
    @{ File='05_payment_and_cancellation.md'; Title='منتجع وادي جارف - الدفع والإلغاء'; Category='payment_and_cancellation' },
    @{ File='06_activities_and_events.md'; Title='منتجع وادي جارف - الأنشطة والفعاليات'; Category='activities_and_events' },
    @{ File='07_rules_and_regulations.md'; Title='منتجع وادي جارف - القواعد والتعليمات'; Category='rules_and_regulations' },
    @{ File='08_faq.md'; Title='منتجع وادي جارف - الأسئلة الشائعة'; Category='faq' },
    @{ File='09_complaints_and_escalation.md'; Title='منتجع وادي جارف - الشكاوى والتصعيد'; Category='complaints_and_escalation' },
    @{ File='10_tone_and_persona.md'; Title='منتجع وادي جارف - أسلوب الردود وشخصية المساعد'; Category='tone_and_persona' }
)

foreach ($doc in $documents) {
    $fullPath = Join-Path $KnowledgeFolder $doc.File
    if (-not (Test-Path $fullPath)) { throw "الملف المطلوب غير موجود: $fullPath" }
}
Write-Host "تم العثور على الملفات العشرة. ملف 11_ADMIN_ONLY غير مدرج ولن يُرفع." -ForegroundColor Green

Write-Section "الاتصال وقراءة مخطط API"
$spec = Invoke-RestMethod -Method Get -Uri "$BaseUrl/openapi.json"
$knowledgeSchema = $spec.components.schemas.KnowledgeCreate
if (-not $knowledgeSchema) { throw "لم أستطع قراءة مخطط KnowledgeCreate من OpenAPI." }
$required = @($knowledgeSchema.required)
foreach ($requiredField in @('category','title','content')) {
    if ($required -notcontains $requiredField) { throw "مخطط API لا يطابق المتوقع؛ الحقل الإلزامي $requiredField غير موجود." }
}
$categorySchema = Resolve-OpenApiSchema $knowledgeSchema.properties.category $spec.components 0
$contentSchema = Resolve-OpenApiSchema $knowledgeSchema.properties.content $spec.components 0
$contentMax = 0
if ($contentSchema -and $contentSchema.maxLength) { $contentMax = [int]$contentSchema.maxLength }
if ($contentMax -gt 0) { Write-Host "الحد الأقصى لنص عنصر المعرفة: $contentMax حرفاً." }

Write-Section "تسجيل الدخول"
$username = Read-Host "البريد الإلكتروني المستخدم في sync_knowledge.ps1"
$securePassword = Read-Host "كلمة المرور (لن تُحفظ في الملف)" -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential($username, $securePassword)
$password = $credential.GetNetworkCredential().Password
$loginPath = '/api/v1/auth/login'
$loginOp = $spec.paths.PSObject.Properties[$loginPath].Value.post
$loginContent = $null
if ($loginOp -and $loginOp.requestBody -and $loginOp.requestBody.content) {
    $loginContent = $loginOp.requestBody.content.PSObject.Properties.Name
}
$loginUri = "$BaseUrl$loginPath"
$loginResponse = $null
if ($loginContent -contains 'application/x-www-form-urlencoded' -or $loginContent -contains 'multipart/form-data' -or -not $loginContent) {
    try {
        $loginResponse = Invoke-RestMethod -Method Post -Uri $loginUri -ContentType 'application/x-www-form-urlencoded' -Body @{ username=$username; password=$password } -ErrorAction Stop
    } catch {
        if ($loginContent -contains 'application/json') {
            $jsonLogin = @{ username=$username; password=$password } | ConvertTo-Json -Compress
            $loginResponse = Invoke-RestMethod -Method Post -Uri $loginUri -ContentType 'application/json' -Body $jsonLogin -ErrorAction Stop
        } else { throw }
    }
} elseif ($loginContent -contains 'application/json') {
    $jsonLogin = @{ username=$username; password=$password } | ConvertTo-Json -Compress
    $loginResponse = Invoke-RestMethod -Method Post -Uri $loginUri -ContentType 'application/json' -Body $jsonLogin -ErrorAction Stop
} else {
    throw "نوع تسجيل الدخول في OpenAPI غير معروف: $($loginContent -join ', ')"
}
$password = $null
$credential = $null
$securePassword = $null
$token = Get-AccessToken $loginResponse
if (-not $token) { throw "تم إرسال طلب الدخول لكن لم أجد access_token في الاستجابة." }
$headers = @{ Authorization = "Bearer $token"; Accept='application/json' }
Write-Host "تم تسجيل الدخول بنجاح." -ForegroundColor Green

Write-Section "تحديد المؤسسة"
if (-not $OrganizationId) { $OrganizationId = Get-OrganizationIdFromApi $spec $headers }
if (-not $OrganizationId) {
    $OrganizationId = Read-Host "أدخل UUID المؤسسة نفسه المستخدم في مزامنة المعرفة السابقة"
}
if ($OrganizationId -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw "معرّف المؤسسة ليس UUID صالحاً. أعد التشغيل مع -OrganizationId أو أدخل المعرّف الصحيح عند السؤال."
}
Write-Host "Organization ID: $OrganizationId"

Write-Section "فحص المعرفة الموجودة"
$collectionUri = "$BaseUrl/api/v1/organizations/$OrganizationId/knowledge"
$existingResponse = Invoke-RestMethod -Method Get -Uri $collectionUri -Headers $headers -ErrorAction Stop
$existingItems = @(Get-ListFromResponse $existingResponse)
$existingTitles = @{}
$legacyTitles = @('معلومات المنتجع','سياسة الحجز والإلغاء')
foreach ($item in $existingItems) {
    $title = Get-PropertyValue $item @('title')
    if ($title) { $existingTitles[(Normalize-Title ([string]$title))] = $true }
}
Write-Host "عدد عناصر المعرفة الموجودة: $($existingItems.Count)"
foreach ($legacy in $legacyTitles) {
    if ($existingTitles.ContainsKey((Normalize-Title $legacy))) {
        Write-Warning "يوجد عنصر قديم بعنوان '$legacy'. لن أحذفه أو أعدله تلقائياً؛ راجعه بعد الاستيراد لتجنب التكرار الدلالي."
    }
}

$plan = @()
foreach ($doc in $documents) {
    $titleKey = Normalize-Title $doc.Title
    if ($existingTitles.ContainsKey($titleKey)) {
        Write-Host "تخطي موجود مسبقاً: $($doc.Title)" -ForegroundColor Yellow
        continue
    }
    $filePath = Join-Path $KnowledgeFolder $doc.File
    $content = [System.IO.File]::ReadAllText($filePath, [System.Text.Encoding]::UTF8)
    if ([string]::IsNullOrWhiteSpace($content)) { throw "المحتوى فارغ: $($doc.File)" }
    $hashBytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($content))
    $hash = ([System.BitConverter]::ToString($hashBytes)).Replace('-','').ToLowerInvariant()
    $category = Get-CategoryValue $categorySchema $doc.Category
    if ($contentMax -gt 0 -and $content.Length -gt $contentMax) {
        throw "الملف $($doc.File) طوله $($content.Length) حرفاً ويتجاوز حد API $contentMax. أوقف الاستيراد لتقسيمه بشكل آمن أولاً."
    }
    $metadata = @{
        source_file = $doc.File
        source_bundle = 'wadi-jaref-knowledge-v2-20261031'
        topic = $doc.Category
        content_sha256 = $hash
        imported_by = 'sync_wadi_knowledge.ps1'
    }
    $body = @{
        category = $category
        title = $doc.Title
        content = $content
        metadata = $metadata
        embed_now = $true
    }
    $plan += [pscustomobject]@{ File=$doc.File; Title=$doc.Title; Category=$category; Body=$body; Hash=$hash }
}

Write-Section "ملخص الاستيراد"
Write-Host "عناصر جديدة ستُضاف: $($plan.Count)"
foreach ($item in $plan) { Write-Host ("- {0} [{1}]" -f $item.Title, $item.Category) }
if ($plan.Count -eq 0) {
    Write-Host "لا توجد عناصر جديدة؛ قد تكون الملفات مستوردة مسبقاً." -ForegroundColor Yellow
    exit 0
}
if ($DryRun) {
    Write-Host "وضع DryRun: لم يتم إرسال أي عنصر إلى API." -ForegroundColor Yellow
    exit 0
}

$approval = Read-Host "لرفع العناصر أعلاه وتضمينها فوراً، اكتب YES بالحروف الإنجليزية"
if ($approval -cne 'YES') {
    Write-Host "تم الإلغاء. لم تتم إضافة أي عنصر." -ForegroundColor Yellow
    exit 0
}

Write-Section "رفع المعرفة وتضمينها"
$created = 0
foreach ($item in $plan) {
    $json = $item.Body | ConvertTo-Json -Depth 20 -Compress
    try {
        $createdResponse = Invoke-RestMethod -Method Post -Uri $collectionUri -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($json)) -ErrorAction Stop
        $created++
        $id = Get-PropertyValue $createdResponse @('id','knowledge_id')
        Write-Host "تمت الإضافة: $($item.Title) $id" -ForegroundColor Green
    } catch {
        Write-Error "توقف الاستيراد عند '$($item.Title)'. لا تعاود تشغيله قبل مراجعة GET؛ ربما حُفظ العنصر رغم فشل التضمين. التفاصيل: $($_.Exception.Message)"
        throw
    }
}
Write-Host "تم إنشاء $created عنصر معرفة جديد." -ForegroundColor Green

Write-Section "اختبار استرجاع المعرفة"
try {
    $searchUri = "$BaseUrl/api/v1/organizations/$OrganizationId/knowledge/search"
    $searchBody = @{ query='منتجع وادي جارف: الوحدات والأسعار حسب اليوم والموسم والحجوزات وسياسة الدفع'; top_k=5 } | ConvertTo-Json -Compress
    $searchResult = Invoke-RestMethod -Method Post -Uri $searchUri -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($searchBody)) -ErrorAction Stop
    Write-Host "نجح طلب البحث في قاعدة المعرفة." -ForegroundColor Green
    $searchResult | ConvertTo-Json -Depth 8
} catch {
    Write-Warning "تم إنشاء عناصر المعرفة، لكن اختبار البحث لم يكتمل: $($_.Exception.Message)"
}

Write-Section "انتهى الاستيراد"
Write-Host "لم يتم رفع ملف 11_ADMIN_ONLY_open_items.md، ولم يتم حذف أو تعديل أي عنصر معرفة قديم." -ForegroundColor Green
Write-Host "ملاحظة: نجاح الاستيراد واختبار البحث لا يثبت وحده أن Gemini أجاب بشكل صحيح؛ يلزم اختبار ai-assistant/chat بعد ذلك."
