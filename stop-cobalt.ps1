$ErrorActionPreference = 'Stop'
$connections = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue
foreach ($connection in $connections) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
    if ($process -and $process.Name -in @('bun.exe', 'node.exe') -and $process.CommandLine -match 'src[/\\]cobalt') {
        Stop-Process -Id $connection.OwningProcess
        Write-Host "Cobalt durduruldu (PID $($connection.OwningProcess))."
    }
}
