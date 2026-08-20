param(
    [string]$BaseUrl = "http://127.0.0.1:18000"
)

$ErrorActionPreference = "Stop"

function Assert-Status([string]$Path, [int]$Expected) {
    $actual = [int](& curl.exe -s -o NUL -w '%{http_code}' ($BaseUrl + $Path))
    if ($actual -ne $Expected) {
        throw "$Path returned $actual, expected $Expected"
    }
    Write-Output "$Path=$actual"
}

Assert-Status "/health" 200
Assert-Status "/ready" 200
Assert-Status "/auth/login" 200
Assert-Status "/auth/me" 401

$webPaths = @(
    "/staff", "/staff/overview", "/staff/forecast", "/staff/revenue",
    "/staff/students", "/staff/cash", "/staff/sales", "/staff/audit",
    "/staff/schedule", "/staff/products", "/staff/discounts", "/staff/tariffs",
    "/staff/settings/legal", "/staff/settings/limits", "/staff/settings/branding",
    "/staff/settings/integrations", "/staff/checkin", "/staff/freeze",
    "/client/hub", "/client/cabinet", "/client/history", "/client/freeze",
    "/client/subscriptions", "/client/purchases", "/client/me", "/client/legal",
    "/client/schedule", "/client/products", "/client/discounts", "/client/tariffs",
    "/client/notifications", "/client/club"
)

foreach ($path in $webPaths) {
    Assert-Status $path 401
}

Write-Output "staging smoke passed: $($webPaths.Count) protected web routes"
