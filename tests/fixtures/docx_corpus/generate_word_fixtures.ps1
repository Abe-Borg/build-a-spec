[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [Parameter(Mandatory = $true)]
    [string]$ScratchRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Save-WordDocument {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Word,

        [Parameter(Mandatory = $true)]
        [string]$InputPath,

        [Parameter(Mandatory = $true)]
        [string]$OutputPath,

        [Parameter(Mandatory = $true)]
        [int]$Format
    )

    $document = $null
    try {
        $openPath = $InputPath
        $document = $Word.Documents.Open([ref]$openPath)
        $savePath = $OutputPath
        $saveFormat = $Format
        $document.SaveAs2([ref]$savePath, [ref]$saveFormat)
    }
    finally {
        if ($null -ne $document) {
            $saveChanges = 0
            $document.Close([ref]$saveChanges)
            [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $document
            ) | Out-Null
        }
    }
}

$sourcePath = (Resolve-Path -LiteralPath $SourceRoot).Path
$outputPath = (New-Item -ItemType Directory -Force -Path $OutputRoot).FullName
$scratchPath = (New-Item -ItemType Directory -Force -Path $ScratchRoot).FullName
$existingWord = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue)
if ($existingWord.Count -ne 0) {
    throw "Close every Word window before generating corpus fixtures."
}
$word = $null

try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3

    Save-WordDocument `
        -Word $word `
        -InputPath (Join-Path $sourcePath "word-like-rich.docx") `
        -OutputPath (Join-Path $outputPath "microsoft-word-16-rich.docx") `
        -Format 16

    Save-WordDocument `
        -Word $word `
        -InputPath (Join-Path $sourcePath "consultant-template.docx") `
        -OutputPath (
            Join-Path $outputPath "microsoft-word-16-consultant-template.docx"
        ) `
        -Format 16

    $legacyPath = Join-Path $scratchPath "sanitized-legacy-input.doc"
    Save-WordDocument `
        -Word $word `
        -InputPath (Join-Path $sourcePath "older-conversion-like.docx") `
        -OutputPath $legacyPath `
        -Format 0

    Save-WordDocument `
        -Word $word `
        -InputPath $legacyPath `
        -OutputPath (
            Join-Path $outputPath "microsoft-word-16-converted-legacy-doc.docx"
        ) `
        -Format 16
}
finally {
    if ($null -ne $word) {
        $saveChanges = 0
        $word.Quit([ref]$saveChanges)
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
            $word
        ) | Out-Null
    }
}
