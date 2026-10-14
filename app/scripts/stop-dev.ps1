# Stop Lumina dev processes (electron from this checkout + its engine).
Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'electron.exe' -and $_.CommandLine -match 'lumina') -or ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'lumina serve')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
