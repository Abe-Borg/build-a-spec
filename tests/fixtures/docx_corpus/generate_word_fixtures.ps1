[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    # Only the LegacyDoc recipe writes an intermediate .doc here.
    [string]$ScratchRoot = "",

    # Which Word fixtures to produce. Every committed fixture is pinned by
    # its SHA-256, so producing one again changes its bytes: name only the
    # recipes you mean to produce.
    [ValidateSet("Rich", "ConsultantTemplate", "LegacyDoc", "TrackedMove")]
    [string[]]$Recipes = @("Rich", "ConsultantTemplate", "LegacyDoc", "TrackedMove")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# TrackedMove: Word's own tracked moves, for Redline on your original
# Phase 2. Word records the Office user name as the author of every change
# it tracks, in word/document.xml -- a part sanitize_external_fixtures.py
# never rewrites -- so the recipe sets this placeholder identity first and
# restores the user's own in a finally block, whatever happens. The moves
# and the bookmark must match tests/docx_corpus.py (TRACKED_MOVES,
# TRACKED_MOVE_BOOKMARK); tests/test_docx_corpus.py pins that they do.
$trackedMoveAuthor = "Build-a-Spec Corpus"
$trackedMoveInitials = "BASC"
$trackedMoveBookmark = "Placeholder_Moved_Anchor"
$trackedMoves = @(
    @{ Move = "Placeholder provision alpha."; After = "Placeholder provision charlie." },
    @{ Move = "Placeholder provision bravo."; After = "Placeholder provision delta." }
)

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

function Find-ParagraphRange {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Document,

        [Parameter(Mandatory = $true)]
        [string]$Needle
    )

    $found = New-Object System.Collections.Generic.List[object]
    $count = $Document.Paragraphs.Count
    for ($index = 1; $index -le $count; $index++) {
        $range = $Document.Paragraphs.Item($index).Range
        if (([string]$range.Text).Contains($Needle)) {
            $found.Add($range)
        }
    }
    if ($found.Count -ne 1) {
        throw "TrackedMove expected one paragraph containing '$Needle'; found $($found.Count)."
    }
    return , $found[0]
}

function Move-TrackedParagraph {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Document,

        [Parameter(Mandatory = $true)]
        [string]$Move,

        [Parameter(Mandatory = $true)]
        [string]$After
    )

    # Find both paragraphs before anything changes: once the cut is
    # tracked, the moved-from copy stays in the document beside its new one.
    $moving = Find-ParagraphRange -Document $Document -Needle $Move
    $anchor = Find-ParagraphRange -Document $Document -Needle $After
    $target = $anchor.Duplicate
    $collapseEnd = 0
    $target.Collapse([ref]$collapseEnd)
    # Cut and paste a whole paragraph, mark included: with Track Changes and
    # Track Moves on, this is what Word records as a move (w:moveFrom and
    # w:moveTo), exactly as it does for Ctrl+X and Ctrl+V.
    $moving.Cut()
    $target.Paste()
}

function Get-TrackedMovePackageProblem {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Identities
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName
            if (-not ($name.EndsWith(".xml") -or $name.EndsWith(".rels"))) {
                continue
            }
            $reader = New-Object System.IO.StreamReader($entry.Open())
            try {
                $text = $reader.ReadToEnd()
            }
            finally {
                $reader.Dispose()
            }
            foreach ($identity in $Identities) {
                # A whole word, so a short Windows user name cannot match
                # inside an attribute name such as w15:userId.
                $pattern = "(?i)(?<![a-z0-9])" + [regex]::Escape($identity) + "(?![a-z0-9])"
                if ([regex]::IsMatch($text, $pattern)) {
                    return "the local Office or Windows identity appears in $name"
                }
            }
            # The same local-path pattern as the corpus privacy scan.
            if ([regex]::IsMatch($text, '(?i)[a-z]:[\\/](users|documents and settings)[\\/]')) {
                return "a local user folder path appears in $name"
            }
            if ($name -eq "word/document.xml" -or $name -eq "word/people.xml") {
                foreach ($match in [regex]::Matches($text, 'w(15)?:author="([^"]*)"')) {
                    if ($match.Groups[2].Value -cne $trackedMoveAuthor) {
                        return "a change in $name is attributed to someone other than the placeholder author"
                    }
                }
            }
            if ($name -eq "word/people.xml") {
                foreach ($match in [regex]::Matches($text, 'w15:providerId="([^"]*)"')) {
                    if ($match.Groups[1].Value -cne "None") {
                        return "$name carries presence information for a signed-in account"
                    }
                }
            }
        }
    }
    finally {
        $archive.Dispose()
    }
    return ""
}

function Invoke-TrackedMoveRecipe {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Word,

        [Parameter(Mandatory = $true)]
        [string]$InputPath,

        [Parameter(Mandatory = $true)]
        [string]$OutputPath
    )

    # The user's own identity: kept only in memory, never written to the
    # console or the fixture, and put back in the finally block below.
    $originalUserName = [string]$Word.UserName
    $originalUserInitials = [string]$Word.UserInitials
    $originalUseLocal = $null
    try {
        $originalUseLocal = [bool]$Word.Options.UseLocalUserInfo
    }
    catch {
        $originalUseLocal = $null
    }
    if ($originalUserName -ceq $trackedMoveAuthor) {
        Write-Warning (
            "Your Office user name is already the TrackedMove placeholder: an " +
            "earlier run was interrupted before it could restore yours. Set " +
            "your name again in Word under File > Options > General."
        )
    }

    $document = $null
    $saved = $false
    $movedFrom = 0
    $movedTo = 0
    $revisionCount = 0
    try {
        # "Always use these values regardless of sign in to Office": without
        # it, a signed-in account's name is recorded instead of UserName.
        if ($null -ne $originalUseLocal) {
            $Word.Options.UseLocalUserInfo = $true
        }
        $Word.UserName = $trackedMoveAuthor
        $Word.UserInitials = $trackedMoveInitials

        $openPath = $InputPath
        $confirmConversions = $false
        $readOnly = $true
        $addToRecentFiles = $false
        $document = $Word.Documents.Open(
            [ref]$openPath,
            [ref]$confirmConversions,
            [ref]$readOnly,
            [ref]$addToRecentFiles
        )
        $document.TrackRevisions = $true
        $document.TrackMoves = $true
        foreach ($move in $trackedMoves) {
            Move-TrackedParagraph -Document $document -Move $move.Move -After $move.After
        }

        # Check what Word recorded BEFORE anything is saved: every change
        # by the placeholder, the moves recorded as moves, the bookmark kept.
        $revisionCount = $document.Revisions.Count
        $otherAuthors = 0
        for ($index = 1; $index -le $revisionCount; $index++) {
            $revision = $document.Revisions.Item($index)
            if ([string]$revision.Author -cne $trackedMoveAuthor) {
                $otherAuthors++
            }
            switch ([int]$revision.Type) {
                14 { $movedFrom++ }
                15 { $movedTo++ }
            }
        }
        if ($revisionCount -lt 1) {
            throw "TrackedMove: Word recorded no tracked changes; nothing was saved."
        }
        if ($otherAuthors -gt 0) {
            throw (
                "TrackedMove: Word attributed $otherAuthors change(s) to someone other " +
                "than the placeholder author; nothing was saved. In Word, check File > " +
                "Options > General > 'Always use these values regardless of sign in " +
                "to Office', then run the recipe again."
            )
        }
        if ($movedFrom -lt $trackedMoves.Count -or $movedTo -lt $trackedMoves.Count) {
            throw (
                "TrackedMove: Word recorded $movedFrom moved-from and $movedTo " +
                "moved-to change(s) for $($trackedMoves.Count) moves -- it tracked the " +
                "cut and paste as a deletion and an insertion. Nothing was saved."
            )
        }
        if (-not $document.Bookmarks.Exists($trackedMoveBookmark)) {
            throw "TrackedMove: the moved paragraph's bookmark is gone; nothing was saved."
        }

        $savePath = $OutputPath
        $saveFormat = 16
        $lockComments = $false
        $password = ""
        $saveAddToRecentFiles = $false
        $document.SaveAs2(
            [ref]$savePath,
            [ref]$saveFormat,
            [ref]$lockComments,
            [ref]$password,
            [ref]$saveAddToRecentFiles
        )
        $saved = $true
    }
    finally {
        if ($null -ne $document) {
            $closeSaveChanges = 0
            $document.Close([ref]$closeSaveChanges)
            [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $document
            ) | Out-Null
        }
        $restored = $true
        try {
            $Word.UserName = $originalUserName
        }
        catch {
            $restored = $false
        }
        try {
            $Word.UserInitials = $originalUserInitials
        }
        catch {
            $restored = $false
        }
        if ($null -ne $originalUseLocal) {
            try {
                $Word.Options.UseLocalUserInfo = $originalUseLocal
            }
            catch {
                $restored = $false
            }
        }
        if (-not $restored) {
            Write-Warning (
                "TrackedMove could not restore your Office user name, initials " +
                "or sign-in setting. Check them in Word under File > Options > General."
            )
        }
    }

    # Then check the package Word wrote, part by part: no local identity, no
    # user folder path, every change the placeholder's, no account presence.
    $identities = @(
        @($originalUserName, $env:USERNAME) |
            Where-Object { $null -ne $_ -and $_.Trim().Length -ge 3 -and $_ -cne $trackedMoveAuthor }
    )
    $problem = Get-TrackedMovePackageProblem -Path $OutputPath -Identities $identities
    if ($problem) {
        if ($saved) {
            Remove-Item -LiteralPath $OutputPath -Force
        }
        throw "TrackedMove: $problem. The file was deleted; nothing was kept."
    }
    Write-Output (
        "TrackedMove: Word $($Word.Version) (build $($Word.Build)) recorded " +
        "$movedFrom moved-from and $movedTo moved-to change(s) among " +
        "$revisionCount, all by the placeholder author. Saved " +
        "$(Split-Path -Leaf $OutputPath)."
    )
}

$sourcePath = (Resolve-Path -LiteralPath $SourceRoot).Path
$outputPath = (New-Item -ItemType Directory -Force -Path $OutputRoot).FullName
$scratchPath = ""
if ($Recipes -contains "LegacyDoc") {
    if ([string]::IsNullOrWhiteSpace($ScratchRoot)) {
        throw "The LegacyDoc recipe needs -ScratchRoot for its intermediate .doc."
    }
    $scratchPath = (New-Item -ItemType Directory -Force -Path $ScratchRoot).FullName
}
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

    if ($Recipes -contains "Rich") {
        Save-WordDocument `
            -Word $word `
            -InputPath (Join-Path $sourcePath "word-like-rich.docx") `
            -OutputPath (Join-Path $outputPath "microsoft-word-16-rich.docx") `
            -Format 16
    }

    if ($Recipes -contains "ConsultantTemplate") {
        Save-WordDocument `
            -Word $word `
            -InputPath (Join-Path $sourcePath "consultant-template.docx") `
            -OutputPath (
                Join-Path $outputPath "microsoft-word-16-consultant-template.docx"
            ) `
            -Format 16
    }

    if ($Recipes -contains "LegacyDoc") {
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

    if ($Recipes -contains "TrackedMove") {
        Invoke-TrackedMoveRecipe `
            -Word $word `
            -InputPath (Join-Path $sourcePath "tracked-move-source.docx") `
            -OutputPath (Join-Path $outputPath "microsoft-word-16-tracked-move.docx")
    }
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
