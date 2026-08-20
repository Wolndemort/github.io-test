param(
    [string]$Email = "omarovadam405@gmail.com",
    [int]$ClubId = 2,
    [string]$BaseUrl = "https://staging.speedycrm.ru:18443"
)

$ErrorActionPreference = "Stop"
$code = Read-Host "Paste the one-time email code locally"
$body = @{ email = $Email; club_id = $ClubId; code = $code } | ConvertTo-Json -Compress
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$response = Invoke-WebRequest -Uri "$BaseUrl/auth/native/verify" -Method Post -ContentType "application/json" -Body $body -WebSession $session
Write-Output "verify=$($response.StatusCode)"
Invoke-WebRequest -Uri "$BaseUrl/auth/me" -WebSession $session | Select-Object -ExpandProperty Content
$code = $null
$body = $null
