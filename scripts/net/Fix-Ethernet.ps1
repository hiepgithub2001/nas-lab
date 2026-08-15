<#
.SYNOPSIS
    Diagnose and repair the Realtek 2.5GbE link dropping to 10 Mbps.

.DESCRIPTION
    Checks three layers, in order of how often they are the culprit:
      1. Link speed   - what the NIC actually negotiated
      2. Config drift - the power-saving properties that cause a bad negotiation
      3. Throughput   - real measured download speed

    If the link negotiated below -MinLinkMbps, or any power-saving property has
    drifted back to its default, the script reapplies the known-good config and
    restarts the adapter to force a fresh autonegotiation.

    Background: Ethernet autonegotiation runs once at link-up and then latches.
    If the PHY negotiates while in a low-power state it advertises only 10BASE-T,
    and the link stays there - reporting Connected, full duplex, zero errors.
    A warm reboot does NOT fix it (+5VSB keeps the PHY powered so the link never
    drops); only a real link-down/up re-runs autonegotiation.

.PARAMETER Check
    Read-only. Report status and exit without changing anything or elevating.

.PARAMETER Fix
    Force the repair even if nothing looks wrong.

.PARAMETER SkipSpeedTest
    Skip the throughput measurement (no network egress, much faster).

.PARAMETER MinLinkMbps
    Link speed below this is treated as a fault. Default 1000.

.EXAMPLE
    .\Fix-Ethernet.ps1 -Check
    Read-only health report. No admin rights needed.

.EXAMPLE
    .\Fix-Ethernet.ps1
    Diagnose, and repair automatically if something is wrong. Self-elevates.
#>

[CmdletBinding()]
param(
    [switch] $Check,
    [switch] $Fix,
    [switch] $SkipSpeedTest,
    [int]    $MinLinkMbps = 1000,
    [string] $AdapterName,
    [int]    $SpeedTestSeconds = 8
)

$ErrorActionPreference = 'Stop'

# Desired state for the power-saving properties. All of these can drive the PHY
# into a low-power state and cause it to negotiate 10BASE-T.
#   WolShutdownLinkSpeed = 2 -> "Not Speed Down" (shutdown/S5 path)
#   the other three          -> disabled         (runtime + resume path)
$DesiredConfig = [ordered]@{
    'EnableGreenEthernet'  = 0
    'PowerSavingMode'      = 0
    'GigaLite'             = 0
    'WolShutdownLinkSpeed' = 2
}

$LogFile = Join-Path $env:LOCALAPPDATA 'Fix-Ethernet.log'

function Write-Log {
    param([string] $Message, [string] $Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch { }
}

function Say {
    param([string] $Text, [string] $Color = 'Gray', [string] $Level = 'INFO')
    Write-Host $Text -ForegroundColor $Color
    Write-Log -Message ($Text -replace '\s+', ' ').Trim() -Level $Level
}

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-TargetAdapter {
    if ($AdapterName) {
        return Get-NetAdapter -Name $AdapterName
    }
    # Prefer a physical Realtek/Ethernet adapter; ignore Hyper-V, WSL, Bluetooth.
    $candidates = Get-NetAdapter -Physical |
        Where-Object { $_.InterfaceDescription -notmatch 'Hyper-V|Bluetooth|Virtual' }

    $wired = $candidates | Where-Object {
        $_.InterfaceDescription -match 'Realtek|Gigabit|GbE|Ethernet' -and
        $_.InterfaceDescription -notmatch 'Wi-?Fi|Wireless|802\.11'
    }
    $pick = @($wired | Where-Object Status -eq 'Up' | Select-Object -First 1)
    if (-not $pick) { $pick = @($wired | Select-Object -First 1) }
    if (-not $pick) { throw 'No physical wired adapter found.' }
    $pick[0]
}

function Get-LinkMbps {
    param($Adapter)
    if ($null -ne $Adapter.Speed -and $Adapter.Speed -gt 0) {
        return [math]::Round($Adapter.Speed / 1e6, 1)
    }
    # Fallback: parse the display string, e.g. "2.5 Gbps"
    if ($Adapter.LinkSpeed -match '([\d.]+)\s*(\w)bps') {
        $n = [double] $Matches[1]
        switch ($Matches[2].ToUpper()) {
            'G' { return $n * 1000 }
            'M' { return $n }
            'K' { return $n / 1000 }
        }
    }
    return 0
}

function Get-ConfigDrift {
    param($Adapter)
    $drift = @()
    foreach ($kw in $DesiredConfig.Keys) {
        $want = $DesiredConfig[$kw]
        $prop = Get-NetAdapterAdvancedProperty -Name $Adapter.Name -RegistryKeyword $kw -ErrorAction SilentlyContinue
        if (-not $prop) { continue }   # property not exposed by this driver
        $have = [int] $prop.RegistryValue[0]
        if ($have -ne $want) {
            $drift += [pscustomobject]@{
                Keyword = $kw
                Display = $prop.DisplayName
                Current = "$($prop.DisplayValue) ($have)"
                Want    = $want
            }
        }
    }
    # Unary comma keeps an empty result an empty array instead of collapsing to
    # $null. Callers must assign directly - wrapping this in @() would turn the
    # empty array into a 1-element array containing an empty array.
    , $drift
}

function Measure-Throughput {
    param([int] $Seconds = 8)
    # Time-boxed download: works on both a 10 Mbps and a 2.5 Gbps link without
    # either taking forever or finishing too fast to measure accurately.
    # PowerShell 5.1 defaults to TLS 1.0, which Cloudflare refuses.
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
    [Net.ServicePointManager]::DefaultConnectionLimit = 8

    # Cloudflare 403s any ?bytes= at or above 100 MB, so pull repeated chunks
    # under that cap until the time box expires. Keep-alive makes the per-chunk
    # overhead small, and this measures correctly at 10 Mbps and at 2.5 Gbps.
    $chunk  = 90000000
    $url    = "https://speed.cloudflare.com/__down?bytes=$chunk"
    $buffer = New-Object byte[] 131072
    $total  = 0L
    $sw     = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.Timeout          = 15000
            $req.ReadWriteTimeout = 15000
            $req.KeepAlive        = $true
            $resp   = $req.GetResponse()
            $stream = $resp.GetResponseStream()
            while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
                $read = $stream.Read($buffer, 0, $buffer.Length)
                if ($read -le 0) { break }
                $total += $read
            }
            $stream.Close(); $resp.Close()
        }
        $sw.Stop()

        if ($total -le 0 -or $sw.Elapsed.TotalSeconds -le 0) { return $null }
        # NB: PowerShell hash keys are case-insensitive, so "Mbps"/"MBps" collide.
        [pscustomobject]@{
            Mbps        = [math]::Round(($total * 8) / $sw.Elapsed.TotalSeconds / 1e6, 1)
            MegaBytesSec = [math]::Round($total / $sw.Elapsed.TotalSeconds / 1MB, 1)
            Bytes       = $total
            Seconds     = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        }
    } catch {
        Write-Log "Speed test failed: $($_.Exception.Message)" 'WARN'
        return $null
    }
}

function Repair-Adapter {
    param($Adapter, $Drift)

    Say ''
    Say '=== REPAIRING ===' 'Yellow' 'FIX'

    foreach ($kw in $DesiredConfig.Keys) {
        $want = $DesiredConfig[$kw]
        $prop = Get-NetAdapterAdvancedProperty -Name $Adapter.Name -RegistryKeyword $kw -ErrorAction SilentlyContinue
        if (-not $prop) {
            Say ("  {0,-22} not supported by driver - skipped" -f $kw) 'DarkGray'
            continue
        }
        if ([int] $prop.RegistryValue[0] -eq $want) {
            Say ("  {0,-22} already correct" -f $kw) 'DarkGray'
            continue
        }
        try {
            Set-NetAdapterAdvancedProperty -Name $Adapter.Name -RegistryKeyword $kw `
                -RegistryValue $want -NoRestart -ErrorAction Stop
            Say ("  {0,-22} -> {1}  [set]" -f $kw, $want) 'Green' 'FIX'
        } catch {
            Say ("  {0,-22} FAILED: {1}" -f $kw, $_.Exception.Message) 'Red' 'ERROR'
        }
    }

    # The actual cure: force a link-down/up so autonegotiation runs again with
    # the PHY fully powered. Nothing else recovers a latched 10 Mbps link.
    Say '  Restarting adapter (link will drop for a few seconds)...' 'Yellow' 'FIX'
    try {
        Restart-NetAdapter -Name $Adapter.Name -ErrorAction Stop
    } catch {
        Say "  Restart failed: $($_.Exception.Message)" 'Red' 'ERROR'
        return
    }

    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        $a = Get-NetAdapter -Name $Adapter.Name -ErrorAction SilentlyContinue
        if ($a -and $a.Status -eq 'Up' -and (Get-LinkMbps $a) -gt 0) { break }
    }
}

# ---------------------------------------------------------------- diagnose ---

$adapter = Get-TargetAdapter
$mbps    = Get-LinkMbps $adapter
$drift   = Get-ConfigDrift $adapter
$stats   = Get-NetAdapterStatistics -Name $adapter.Name -ErrorAction SilentlyContinue

Say ''
Say "=== ETHERNET HEALTH CHECK  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" 'Cyan'
Say ''
Say "  Adapter    : $($adapter.Name)  -  $($adapter.InterfaceDescription)"
Say "  Status     : $($adapter.Status)   Duplex: $(if ($adapter.FullDuplex) { 'Full' } else { 'Half' })"

$linkOk    = $mbps -ge $MinLinkMbps
$linkColor = if ($linkOk) { 'Green' } else { 'Red' }
Say ("  Link speed : {0}   (threshold {1} Mbps)" -f $adapter.LinkSpeed, $MinLinkMbps) $linkColor

if ($stats) {
    $errs = $stats.ReceivedPacketErrors + $stats.OutboundPacketErrors
    Say ("  Errors     : {0} rx+tx packet errors, {1} discarded" -f `
        $errs, ($stats.ReceivedDiscardedPackets + $stats.OutboundDiscardedPackets))
}

Say ''
if ($drift.Count -eq 0) {
    Say '  Power-saving config: all 4 properties correct' 'Green'
} else {
    Say "  Power-saving config: $($drift.Count) property/properties DRIFTED" 'Red' 'WARN'
    foreach ($d in $drift) {
        Say ("    {0,-22} is {1}, want {2}" -f $d.Keyword, $d.Current, $d.Want) 'Red' 'WARN'
    }
    Say '    (a Realtek driver update resets these to defaults)' 'DarkGray'
}

if (-not $SkipSpeedTest) {
    Say ''
    Say "  Measuring throughput (~$SpeedTestSeconds s)..." 'Gray'
    $tp = Measure-Throughput -Seconds $SpeedTestSeconds
    if ($tp) {
        $pct = if ($mbps -gt 0) { [math]::Round($tp.Mbps / $mbps * 100) } else { 0 }
        Say ("  Throughput : {0} Mbps ({1} MB/s)  = {2}% of link" -f $tp.Mbps, $tp.MegaBytesSec, $pct) 'Cyan'
        if (-not $linkOk -and $tp.Mbps -gt ($mbps * 0.8)) {
            Say '  -> Traffic is saturating the degraded link. This is the bottleneck.' 'Yellow' 'WARN'
        }
    } else {
        Say '  Throughput : test failed (no internet, or endpoint unreachable)' 'Yellow' 'WARN'
    }
}

# ------------------------------------------------------------------- verdict ---

$needsFix = (-not $linkOk) -or ($drift.Count -gt 0)

Say ''
if ($needsFix) {
    Say '  VERDICT: problem detected.' 'Red' 'WARN'
    if (-not $linkOk) {
        Say "    Link negotiated at $mbps Mbps, below the $MinLinkMbps Mbps threshold." 'Red'
    }
} else {
    Say '  VERDICT: healthy. Nothing to do.' 'Green'
}
Say ''

if ($Check) {
    Say 'Read-only mode (-Check). Re-run without -Check to repair.' 'DarkGray'
    exit ($(if ($needsFix) { 1 } else { 0 }))
}

if (-not $needsFix -and -not $Fix) {
    exit 0
}

# Re-launch elevated if needed; Set-NetAdapterAdvancedProperty and
# Restart-NetAdapter both require administrator rights.
if (-not (Test-Elevated)) {
    Say 'Repair needs administrator rights - requesting elevation (approve the UAC prompt)...' 'Yellow'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Fix', '-SkipSpeedTest')
    if ($AdapterName) { $argList += @('-AdapterName', $AdapterName) }
    try {
        Start-Process powershell -Verb RunAs -Wait -ArgumentList $argList
    } catch {
        Say 'Elevation was declined. Run this script from an admin PowerShell instead.' 'Red' 'ERROR'
        exit 1
    }
    $after = Get-NetAdapter -Name $adapter.Name
    Say ''
    Say "  Link speed now: $($after.LinkSpeed)" 'Cyan'
    exit 0
}

Repair-Adapter -Adapter $adapter -Drift $drift

# ------------------------------------------------------------------- verify ---

$adapter2 = Get-NetAdapter -Name $adapter.Name
$mbps2    = Get-LinkMbps $adapter2
$drift2   = Get-ConfigDrift $adapter2

Say ''
Say '=== AFTER REPAIR ===' 'Cyan'
Say ("  Link speed : {0}  (was {1} Mbps)" -f $adapter2.LinkSpeed, $mbps) `
    $(if ($mbps2 -ge $MinLinkMbps) { 'Green' } else { 'Red' }) 'FIX'
Say ("  Config     : {0}" -f $(if ($drift2.Count -eq 0) { 'all correct' } else { "$($drift2.Count) still drifted" })) `
    $(if ($drift2.Count -eq 0) { 'Green' } else { 'Red' })

Say ''
if ($mbps2 -ge $MinLinkMbps -and $drift2.Count -eq 0) {
    Say '  FIXED.' 'Green' 'FIX'
} elseif ($mbps2 -gt $mbps) {
    Say "  Improved ($mbps -> $mbps2 Mbps) but still below threshold." 'Yellow' 'WARN'
} else {
    Say '  STILL BROKEN. Next steps: try a different cable and a different' 'Red' 'ERROR'
    Say '  switch port, then check the far-end port speed on the router.' 'Red'
}
Say "  Log: $LogFile" 'DarkGray'
Say ''

exit ($(if ($mbps2 -ge $MinLinkMbps) { 0 } else { 1 }))
