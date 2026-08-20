param(
    [int]$ClubId = 1,
    [string]$BaseUrl = "http://127.0.0.1:18000"
)

$ErrorActionPreference = "Stop"
$initData = Read-Host "Paste one-time Telegram init_data locally"
if ([string]::IsNullOrWhiteSpace($initData)) { throw "init_data is empty" }

$body = @{ init_data = $initData; club_id = $ClubId } | ConvertTo-Json -Compress
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
try {
    $response = Invoke-WebRequest -Uri "$BaseUrl/auth/telegram/exchange" -Method Post `
        -ContentType "application/json" -Body $body -WebSession $session
    Write-Output "exchange=$($response.StatusCode)"
    Invoke-WebRequest -Uri "$BaseUrl/auth/me" -WebSession $session | Select-Object -ExpandProperty Content
    Write-Output "Session established in this process only. Do not save or share init_data."
} finally {
    $initData = $null
    $body = $null
}
