# Sync Resort Social AI Knowledge Base (PowerShell)

## 1. Configure credentials and organization
Open PowerShell in the folder containing `sync_knowledge.ps1` and set:

```powershell
$env:RESORT_ADMIN_EMAIL = 'admin@example.com'
```

Set the password with an interactive prompt that avoids echoing it:

```powershell
$secure = Read-Host 'Admin password' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:RESORT_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$env:RESORT_ORGANIZATION_ID = '00000000-0000-0000-0000-000000000000' # replace with real organization UUID
```

The email must be a registered user with an active `owner`, `admin`, or `manager` membership in that organization. Replace the example organization UUID.

## 2. Edit the knowledge payload
Edit `knowledge.json`. It must be a JSON array. Every item needs `category`, `title`, and `content`; optional fields are `property_id`, `source_url`, and `metadata`. The script sets `embed_now=true`.

## 3. Run

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\sync_knowledge.ps1
```

Or provide parameters explicitly:

```powershell
.\sync_knowledge.ps1 -ApiBaseUrl 'http://localhost:8000' -OrganizationId '<ORG-UUID>' -AdminEmail 'admin@example.com' -AdminPassword $env:RESORT_ADMIN_PASSWORD -KnowledgeFile '.\knowledge.json'
```

The script waits for `GET /health/ready`, logs in using the app's OAuth2 form login, and sends each knowledge item to `POST /api/v1/organizations/{organization_id}/knowledge` with a Bearer token. The API currently has no bulk-create route, so the script posts each JSON item in sequence as one automated run.

## Important notes
- CORS is a browser-enforced restriction; PowerShell HTTP requests are not blocked by browser CORS policy. This script does not change CORS settings.
- `embed_now=true` uses the embedding provider configured in the backend. Configure its API key/provider first. This repository uses the application's database/vector embedding support, not a dedicated Qdrant write endpoint.
- Do not commit credentials to source control. The password is kept in the current PowerShell process environment only when using the secure prompt above.
