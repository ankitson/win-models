param(
  [string] $BaseUrl = "http://127.0.0.1:8080/v1",
  [Parameter(Mandatory)] [string] $Model,
  [int] $Runs = 3,
  [int] $MaxTokens = 256
)

$prompt = @"
Think briefly, then answer in exactly one sentence: for local coding use, name one advantage and one drawback of quantized models.
"@

$rows = @()
for ($i = 1; $i -le $Runs; $i++) {
  $body = @{
    model = $Model
    messages = @(@{ role = "user"; content = $prompt })
    max_tokens = $MaxTokens
    temperature = 0
  } | ConvertTo-Json -Depth 8

  $start = Get-Date
  $response = Invoke-RestMethod -Uri "$BaseUrl/chat/completions" -Method Post -ContentType "application/json" -Body $body
  $elapsedMs = ((Get-Date) - $start).TotalMilliseconds
  $usage = $response.usage
  $timings = $response.timings
  $choice = $response.choices[0]
  $content = $choice.message.content
  $reasoning = $choice.message.reasoning_content

  $rows += [PSCustomObject]@{
    run = $i
    model = $Model
    elapsed_ms = [math]::Round($elapsedMs, 1)
    prompt_tokens = $usage.prompt_tokens
    completion_tokens = $usage.completion_tokens
    prompt_tps = if ($timings) { [math]::Round($timings.prompt_per_second, 2) } else { $null }
    generation_tps = if ($timings) { [math]::Round($timings.predicted_per_second, 2) } else { $null }
    finish = $choice.finish_reason
    content_chars = if ($content) { $content.Length } else { 0 }
    reasoning_chars = if ($reasoning) { $reasoning.Length } else { 0 }
  }
}

$rows | Format-Table -AutoSize
