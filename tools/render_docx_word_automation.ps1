Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Remove-OwnershipFile {
    $ownershipPath = $env:BUILD_A_SPEC_WORD_OWNERSHIP
    if (-not [string]::IsNullOrWhiteSpace($ownershipPath)) {
        Remove-Item -LiteralPath $ownershipPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ($ownershipPath + ".pending") -Force -ErrorAction SilentlyContinue
    }
}

function Get-OwnedWordProcess {
    $ownershipPath = $env:BUILD_A_SPEC_WORD_OWNERSHIP
    if ([string]::IsNullOrWhiteSpace($ownershipPath) -or
        -not (Test-Path -LiteralPath $ownershipPath -PathType Leaf)) {
        return $null
    }

    try {
        $record = [IO.File]::ReadAllText($ownershipPath) | ConvertFrom-Json
        if ($null -eq $record -or
            -not [string]::Equals(
                [string]$record.token,
                $env:BUILD_A_SPEC_WORD_TOKEN,
                [StringComparison]::Ordinal
            )) {
            return $null
        }
        $ownedId = [int]$record.pid
        $ownedStart = [long]$record.creation_time
        $recordedExecutable = [IO.Path]::GetFullPath(
            [string]$record.executable
        )
        $process = Get-Process -Id $ownedId -ErrorAction Stop
        if ($process.ProcessName -ine "WINWORD") {
            return $null
        }
        $actualExecutable = [IO.Path]::GetFullPath([string]$process.Path)
        $expectedExecutable = [IO.Path]::GetFullPath(
            $env:BUILD_A_SPEC_WORD_EXECUTABLE
        )
        if (-not [string]::Equals(
            $actualExecutable,
            $expectedExecutable,
            [StringComparison]::OrdinalIgnoreCase
        ) -or -not [string]::Equals(
            $recordedExecutable,
            $expectedExecutable,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            return $null
        }
        $actualStart = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
        if ($actualStart -ne $ownedStart) {
            return $null
        }
        return $process
    }
    catch {
        return $null
    }
}

function Stop-OwnedWordProcess {
    param([int]$WaitSeconds = 0)

    $ownedProcess = Get-OwnedWordProcess
    if ($null -ne $ownedProcess -and $WaitSeconds -gt 0) {
        $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
        while ($null -ne $ownedProcess -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
            $ownedProcess = Get-OwnedWordProcess
        }
    }

    if ($null -ne $ownedProcess) {
        Stop-Process -Id $ownedProcess.Id -Force -ErrorAction Stop
        $deadline = [DateTime]::UtcNow.AddSeconds(5)
        while ($null -ne (Get-OwnedWordProcess) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
        if ($null -ne (Get-OwnedWordProcess)) {
            throw "Owned WINWORD process $($ownedProcess.Id) did not exit."
        }
    }
    Remove-OwnershipFile
}

if ($env:BUILD_A_SPEC_WORD_CLEANUP_ONLY -eq "1") {
    Stop-OwnedWordProcess -WaitSeconds 1
    exit 0
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class BuildASpecWordNativeMethods
{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(
        IntPtr windowHandle,
        out uint processId
    );
}
"@

$inputPath = [IO.Path]::GetFullPath($env:BUILD_A_SPEC_WORD_INPUT)
$pdfPath = [IO.Path]::GetFullPath($env:BUILD_A_SPEC_WORD_PDF)
$expectedWordPath = [IO.Path]::GetFullPath($env:BUILD_A_SPEC_WORD_EXECUTABLE)
$ownershipPath = [IO.Path]::GetFullPath($env:BUILD_A_SPEC_WORD_OWNERSHIP)
$beforeWordIdentities = @{}
Get-Process -Name WINWORD -ErrorAction SilentlyContinue | ForEach-Object {
    $beforeWordIdentities[[int]$_.Id] = `
        $_.StartTime.ToUniversalTime().ToFileTimeUtc()
}
$activationStarted = [DateTime]::UtcNow.AddSeconds(-2)

$word = $null
$documents = $null
$document = $null
$documentWindow = $null
$createdWordByCom = $false
$ownsWord = $false
$hasOriginalAutomationSecurity = $false
$originalAutomationSecurity = 0
$primaryError = $null
$cleanupErrors = @()

try {
    $word = New-Object -ComObject Word.Application
    # Word.Application is a SingleUse COM server: this object is the exact
    # instance created by New-Object.  Keep that object-level ownership even
    # if PID discovery fails, so normal pre-handshake errors still call Quit.
    $createdWordByCom = $true
    $newWordProcesses = @()
    $identityDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $newWordProcesses = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue |
            Where-Object {
                $candidateId = [int]$_.Id
                $candidateStart = $_.StartTime.ToUniversalTime().ToFileTimeUtc()
                (-not $beforeWordIdentities.ContainsKey($candidateId) -or
                    $beforeWordIdentities[$candidateId] -ne $candidateStart) -and
                    $_.StartTime.ToUniversalTime() -ge $activationStarted
            })
        if ($newWordProcesses.Count -eq 1) {
            break
        }
        if ($newWordProcesses.Count -gt 1) {
            throw "More than one new WINWORD process appeared during activation."
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $identityDeadline)
    if ($newWordProcesses.Count -ne 1) {
        throw "Could not identify the dedicated WINWORD process."
    }
    [uint32]$wordProcessId = [uint32]$newWordProcesses[0].Id

    $wordStart = $newWordProcesses[0].StartTime.ToUniversalTime().ToFileTimeUtc()
    if ($beforeWordIdentities.ContainsKey([int]$wordProcessId) -and
        $beforeWordIdentities[[int]$wordProcessId] -eq $wordStart) {
        throw "Word automation attached to a pre-existing WINWORD process; refusing to close it."
    }

    $wordProcess = Get-Process -Id ([int]$wordProcessId) -ErrorAction Stop
    if ($wordProcess.ProcessName -ine "WINWORD" -or
        $wordProcess.StartTime.ToUniversalTime() -lt $activationStarted) {
        throw "The Word automation process failed the ownership check."
    }

    $actualWordPath = [IO.Path]::GetFullPath([string]$wordProcess.Path)
    if (-not [string]::Equals(
        $actualWordPath,
        $expectedWordPath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Registered Word executable '$actualWordPath' does not match '$expectedWordPath'."
    }

    $ownsWord = $true
    $ownershipRecord = [ordered]@{
        token = $env:BUILD_A_SPEC_WORD_TOKEN
        pid = [int]$wordProcessId
        creation_time = [long]$wordStart
        executable = $actualWordPath
    } | ConvertTo-Json -Compress
    $pendingOwnershipPath = $ownershipPath + ".pending"
    [IO.File]::WriteAllText(
        $pendingOwnershipPath,
        $ownershipRecord,
        [Text.Encoding]::UTF8
    )
    Move-Item -LiteralPath $pendingOwnershipPath -Destination $ownershipPath -Force

    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.ScreenUpdating = $false
    $originalAutomationSecurity = [int]$word.AutomationSecurity
    $hasOriginalAutomationSecurity = $true
    $word.AutomationSecurity = 3

    $documents = $word.Documents
    $openPath = $inputPath
    $confirmConversions = $false
    $openReadOnly = $true
    $addToRecentFiles = $false
    $document = $documents.Open(
        [ref]$openPath,
        [ref]$confirmConversions,
        [ref]$openReadOnly,
        [ref]$addToRecentFiles
    )
    $documentWindow = $document.ActiveWindow
    [uint32]$documentWindowProcessId = 0
    [void][BuildASpecWordNativeMethods]::GetWindowThreadProcessId(
        [IntPtr]([int64]$documentWindow.Hwnd),
        [ref]$documentWindowProcessId
    )
    if ($documentWindowProcessId -ne $wordProcessId) {
        throw "The opened document window does not belong to the owned WINWORD process."
    }
    $document.ExportAsFixedFormat(
        $pdfPath,
        17,
        $false,
        0,
        0,
        1,
        1,
        0,
        $true,
        $false,
        1,
        $true,
        $true,
        $false
    )
    if (-not (Test-Path -LiteralPath $pdfPath -PathType Leaf) -or
        (Get-Item -LiteralPath $pdfPath).Length -le 0) {
        throw "Word did not produce a non-empty PDF."
    }
    Write-Output ("Rendered with Word {0}." -f $word.Version)
}
catch {
    $primaryError = $_
}
finally {
    if ($null -ne $documentWindow) {
        try {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($documentWindow)
        }
        catch {
            $cleanupErrors += "Window COM release failed: $($_.Exception.Message)"
        }
        $documentWindow = $null
    }

    if ($null -ne $document) {
        try {
            $closeSaveChanges = 0
            $document.Close([ref]$closeSaveChanges)
        }
        catch {
            $cleanupErrors += "Document.Close failed: $($_.Exception.Message)"
        }
        finally {
            try {
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
            }
            catch {
                $cleanupErrors += "Document COM release failed: $($_.Exception.Message)"
            }
            $document = $null
        }
    }

    if ($null -ne $documents) {
        try {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($documents)
        }
        catch {
            $cleanupErrors += "Documents COM release failed: $($_.Exception.Message)"
        }
        $documents = $null
    }

    if ($null -ne $word) {
        if ($createdWordByCom) {
            if ($hasOriginalAutomationSecurity) {
                try {
                    $word.AutomationSecurity = $originalAutomationSecurity
                }
                catch {
                    $cleanupErrors += "AutomationSecurity restore failed: $($_.Exception.Message)"
                }
            }
            try {
                $quitSaveChanges = 0
                $word.Quit([ref]$quitSaveChanges)
            }
            catch {
                $cleanupErrors += "Word.Quit failed: $($_.Exception.Message)"
            }
        }
        try {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
        }
        catch {
            $cleanupErrors += "Word COM release failed: $($_.Exception.Message)"
        }
        $word = $null
    }

    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()

    if ($ownsWord) {
        try {
            Stop-OwnedWordProcess -WaitSeconds 10
        }
        catch {
            $cleanupErrors += "Owned Word cleanup failed: $($_.Exception.Message)"
        }
    }
}

if ($null -ne $primaryError) {
    $message = $primaryError.Exception.Message
    if ($cleanupErrors.Count -gt 0) {
        $message += " Cleanup: " + ($cleanupErrors -join "; ")
    }
    throw $message
}
if ($cleanupErrors.Count -gt 0) {
    throw ($cleanupErrors -join "; ")
}
