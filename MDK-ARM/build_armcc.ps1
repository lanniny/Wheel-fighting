# Manual AC5 (armcc) build for robot firmware (STM32F407VET6)
# Reason: project is EIDE-format (no .uvprojx) and EIDE extension not installed.
# Strategy: cd into MDK-ARM and use RELATIVE ASCII paths so the Chinese project
# path never reaches armcc (AC5 handles non-ASCII abs paths poorly).
$ErrorActionPreference = "Continue"

$BIN     = "C:\Dprogress\Keil\ARM\ARMCC\bin"
$ARMCC   = Join-Path $BIN "armcc.exe"
$ARMASM  = Join-Path $BIN "armasm.exe"
$ARMLINK = Join-Path $BIN "armlink.exe"
$FROMELF = Join-Path $BIN "fromelf.exe"

Set-Location -LiteralPath $PSScriptRoot
$OUT = "build"
if (-not (Test-Path $OUT)) { New-Item -ItemType Directory -Path $OUT | Out-Null }
Remove-Item "$OUT\*.o" -ErrorAction SilentlyContinue

$DEFS = @("-DUSE_HAL_DRIVER","-DSTM32F407xx")
$INCS = @(
  "-I.",
  "-I..\Core\Inc",
  "-I..\Drivers\STM32F4xx_HAL_Driver\Inc",
  "-I..\Drivers\STM32F4xx_HAL_Driver\Inc\Legacy",
  "-I..\Drivers\CMSIS\Device\ST\STM32F4xx\Include",
  "-I..\Drivers\CMSIS\Include",
  "-I..\freertos\include",
  "-I..\freertos\portable\ARM_CM4F",
  "-I.cmsis\include"
)
$CFLAGS = @("--cpu","Cortex-M4.fp","--apcs=interwork","-O3","--c99","-g",
            "--split_sections","--diag_suppress=1,1295")

# ---- source list (relative to MDK-ARM) ----
$srcs = @()
Get-ChildItem "..\Core\Src" -Filter *.c | ForEach-Object { $srcs += "..\Core\Src\$($_.Name)" }
$halDir = "..\Drivers\STM32F4xx_HAL_Driver\Src"
@("stm32f4xx_hal_tim.c","stm32f4xx_hal_tim_ex.c","stm32f4xx_hal_adc.c","stm32f4xx_hal_adc_ex.c",
  "stm32f4xx_ll_adc.c","stm32f4xx_hal_rcc.c","stm32f4xx_hal_rcc_ex.c","stm32f4xx_hal_flash.c",
  "stm32f4xx_hal_flash_ex.c","stm32f4xx_hal_flash_ramfunc.c","stm32f4xx_hal_gpio.c",
  "stm32f4xx_hal_dma_ex.c","stm32f4xx_hal_dma.c","stm32f4xx_hal_pwr.c","stm32f4xx_hal_pwr_ex.c",
  "stm32f4xx_hal_cortex.c","stm32f4xx_hal.c","stm32f4xx_hal_exti.c","stm32f4xx_hal_i2c.c",
  "stm32f4xx_hal_i2c_ex.c","stm32f4xx_hal_uart.c") | ForEach-Object { $srcs += "$halDir\$_" }
$srcs += "..\freertos\src\croutine.c","..\freertos\src\event_groups.c","..\freertos\src\list.c",
         "..\freertos\src\queue.c","..\freertos\src\stream_buffer.c","..\freertos\src\tasks.c",
         "..\freertos\src\timers.c","..\freertos\portable\ARM_CM4F\port.c",
         "..\freertos\portable\MemMang\heap_4.c"

$logFile = "$OUT\build.log"
Set-Content $logFile "==== armcc build $(Get-Date) ===="
$objs = @(); $errFiles = @(); $totalErr = 0; $totalWarn = 0

foreach ($s in $srcs) {
  if (-not (Test-Path $s)) { Write-Host "[MISS] $s" -ForegroundColor Red; $errFiles += $s; continue }
  $base = [IO.Path]::GetFileNameWithoutExtension($s)
  $obj  = "$OUT\$base.o"
  $cargs = $CFLAGS + $DEFS + $INCS + @("-c", $s, "-o", $obj)
  $armccOut = & $ARMCC @cargs 2>&1
  $code = $LASTEXITCODE
  Add-Content $logFile "---- $s ----"
  $armccOut | Add-Content $logFile
  $txt = [string]($armccOut -join "`n")
  $e = ([regex]::Matches($txt, "Error:")).Count
  $w = ([regex]::Matches($txt, "Warning:")).Count
  $totalErr += $e; $totalWarn += $w
  if ($code -ne 0 -or $e -gt 0) {
    $errFiles += $base
    Write-Host "[FAIL] $base  ($e err, $w warn)" -ForegroundColor Red
    $armccOut | Where-Object { $_ -match "Error:" } | Select-Object -First 4 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
  } elseif ($w -gt 0) {
    Write-Host "[warn] $base  ($w warn)" -ForegroundColor Yellow
    $objs += $obj
  } else {
    $objs += $obj
  }
}

# ---- startup (ARM syntax -> armasm) ----
$asmOut = & $ARMASM --cpu Cortex-M4.fp -g "startup_stm32f407xx.s" -o "$OUT\startup.o" 2>&1
Add-Content $logFile "---- startup_stm32f407xx.s ----"; $asmOut | Add-Content $logFile
if ($LASTEXITCODE -eq 0) { $objs += "$OUT\startup.o" }
else { Write-Host "[FAIL] startup.s" -ForegroundColor Red; $asmOut | Write-Host; $errFiles += "startup" }

Write-Host ""
Write-Host "==== COMPILE: $($objs.Count) ok / $($errFiles.Count) fail / $totalErr err / $totalWarn warn ===="
if ($errFiles.Count -gt 0) {
  Write-Host "FAILED: $($errFiles -join ', ')" -ForegroundColor Red
  Write-Host "Full log: MDK-ARM\$logFile"
  exit 1
}

# ---- scatter file (literal here-string; $$ stays literal) ----
$sct = "$OUT\robot.sct"
@'
LR_IROM1 0x08000000 0x00080000  {
  ER_IROM1 0x08000000 0x00080000  {
   *.o (RESET, +First)
   *(InRoot$$Sections)
   .ANY (+RO)
  }
  RW_IRAM1 0x20000000 0x00020000  {
   .ANY (+RW +ZI)
  }
}
'@ | Set-Content -Encoding ASCII $sct

# ---- link ----
$linkArgs = @("--cpu","Cortex-M4.fp","--scatter",$sct,"--info","sizes,totals",
              "--map","--list","$OUT\robot.map","-o","$OUT\robot.axf") + $objs
$linkOut = & $ARMLINK @linkArgs 2>&1
Add-Content $logFile "==== LINK ===="; $linkOut | Add-Content $logFile
$linkOut | Write-Host
if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] link" -ForegroundColor Red; exit 1 }

# ---- hex / bin ----
& $FROMELF --i32 --output "$OUT\robot.hex" "$OUT\robot.axf" | Out-Null
& $FROMELF --bin --output "$OUT\robot.bin" "$OUT\robot.axf" | Out-Null

Write-Host ""
Write-Host "==== ARTIFACTS ====" -ForegroundColor Green
Get-ChildItem "$OUT\robot.axf","$OUT\robot.hex","$OUT\robot.bin" -ErrorAction SilentlyContinue |
  Select-Object Name, @{N="Bytes";E={$_.Length}} | Format-Table -AutoSize
Write-Host "BUILD OK" -ForegroundColor Green
