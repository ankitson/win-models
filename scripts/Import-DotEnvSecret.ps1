function Import-WinModelsDotEnvSecret {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }

        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $name = $parts[0].Trim()
        if (-not $name -or $name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            continue
        }

        if ([Environment]::GetEnvironmentVariable($name, "Process")) {
            continue
        }

        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }

    if ($env:MCPPROXY_AGENT_TOKEN -and -not $env:MCPPROXY_AGENTS_TOKEN) {
        $env:MCPPROXY_AGENTS_TOKEN = $env:MCPPROXY_AGENT_TOKEN
    }
}
