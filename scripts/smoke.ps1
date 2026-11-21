param(
  [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

Write-Host "Checking service health..."
$health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health"
$health | ConvertTo-Json -Depth 4

$session = "smoke-$([guid]::NewGuid().ToString('N'))"
$body = @{
  session_id = $session
  message = "Namaste, can I book a product demo this week?"
} | ConvertTo-Json -Compress

Write-Host "Checking the chat contract..."
$chat = Invoke-RestMethod -Method Post -Uri "$BaseUrl/chat" -ContentType "application/json" -Body $body
$chat | ConvertTo-Json -Depth 5

Write-Host "Checking persisted session history..."
Invoke-RestMethod -Method Get -Uri "$BaseUrl/sessions/$session/messages" | ConvertTo-Json -Depth 6

