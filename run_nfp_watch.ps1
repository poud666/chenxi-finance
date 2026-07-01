param(
  [string]$TargetRelease = "",
  [double]$ExpectedPayrollsK = 0,
  [double]$ExpectedUnemployment = 0,
  [double]$ExpectedAheMom = 0
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$argsList = @(
  ".\nfp_monitor.py",
  "--watch",
  "--only-new",
  "--interval-seconds", "5",
  "--timeout-seconds", "1200"
)

if ($TargetRelease.Trim().Length -gt 0) {
  $argsList += @("--target-release", $TargetRelease)
}

if ($ExpectedPayrollsK -ne 0) {
  $argsList += @("--expected-payrolls-k", "$ExpectedPayrollsK")
}

if ($ExpectedUnemployment -ne 0) {
  $argsList += @("--expected-unemployment", "$ExpectedUnemployment")
}

if ($ExpectedAheMom -ne 0) {
  $argsList += @("--expected-ahe-mom", "$ExpectedAheMom")
}

py -3 @argsList
