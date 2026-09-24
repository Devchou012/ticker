# 把富果行情 API key 存成使用者環境變數 FUGLE_API_KEY。輸入時畫面只顯示 *，key 不會出現在畫面或指令紀錄裡。
# 用法：在另一個 PowerShell 視窗執行  powershell -ExecutionPolicy Bypass -File "$HOME\ticker\set_fugle_key.ps1"
$secure = Read-Host "貼上富果 API key（畫面只會顯示 *）" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr).Trim()
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if (-not $key) { Write-Host "沒有輸入，什麼都沒改"; exit 1 }
[Environment]::SetEnvironmentVariable("FUGLE_API_KEY", $key, "User")
Write-Host "已存好（共 $($key.Length) 個字元）。重開面板後生效。"
