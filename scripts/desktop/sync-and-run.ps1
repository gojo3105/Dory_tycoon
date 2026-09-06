#Requires -Version 5.1
<#
.SYNOPSIS
  Pulls the latest work from the upstream repo (where Claude Code pushes)
  into this fork and pushes it to origin, which is what makes the
  self-hosted runner registered on the fork actually run the pipeline.

  Replaces typing these by hand every time:
    git fetch upstream
    git merge upstream/<branch>
    git push origin <branch>

.DESCRIPTION
  After pushing, the Game Factory Pipeline needs to actually start. A push
  only auto-triggers it when GameSpecs/** changed (see game-factory.yml),
  so when the incoming commits only touched C#/docs this script needs
  another way to start it. It prefers `gh workflow run` when the GitHub
  CLI is installed and logged in, but that is optional, not required: when
  it is missing (or fails), this instead commits a one-line bump to
  GameSpecs/.ci-trigger and pushes it, which reaches the exact same
  `on: push: paths: GameSpecs/**` trigger game-factory.yml already reacts
  to - no CLI, no token, no extra setup on this machine, ever. Either way,
  exactly one run starts, and never a pointless one when nothing changed.

.PARAMETER Silent
  No popups: write to Logs/auto-sync.log and exit with a status code.
  This is what the scheduled task (register-auto-sync.ps1) uses.

.PARAMETER NoTrigger
  Sync only - never start a pipeline run.

.PARAMETER SkipErrorReport
  Don't collect/commit Unity's compile errors (scripts/dev/collect-errors.ps1).

.NOTES
  The GitHub CLI (`gh`) is optional - see .DESCRIPTION. If you do want it
  for the nicer no-extra-commit path:
    winget install --id GitHub.cli
    gh auth login
  Aborts (without touching anything) if the working tree has uncommitted
  changes, so it can never throw away local work.
#>

[CmdletBinding()]
param(
    [string]$RepoPath = "C:\Dory_tycoon",
    [string]$Branch = "claude/delete-current-content-mgn4xm",
    [string]$UpstreamRemote = "upstream",
    [string]$OriginRemote = "origin",
    [string]$GameId = "game01",
    [string]$Workflow = "game-factory.yml",
    [string]$RepoSlug = "gojo3105/Dory_tycoon",
    [switch]$Silent,
    [switch]$NoTrigger,
    [switch]$SkipErrorReport
)

$ErrorActionPreference = "Stop"

$script:LogPath = Join-Path $RepoPath "Logs\auto-sync.log"
$script:StatusRelativePath = "Reports/sync-status/latest.txt"
$script:StatusPath = Join-Path $RepoPath ($script:StatusRelativePath -replace "/", "\")
# Sync status is committed only when the OUTCOME changes, plus a heartbeat
# past this age. Every run rewriting a timestamp would be 96 commits a day.
$script:StatusHeartbeatHours = 6

function Write-Log([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
    Write-Host $line

    try {
        $logDir = Split-Path $script:LogPath -Parent
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        Add-Content -Path $script:LogPath -Value $line -Encoding UTF8
    }
    catch {
        # Logging must never be the reason a sync fails.
    }
}

# Writes Reports/sync-status/latest.txt and commits + pushes ONLY that file.
# This is the answer to "why has nothing changed for eight hours": the log
# this script keeps is gitignored, so when a sync aborted (a dirty tracked
# file, a merge conflict, no network) neither Claude nor the dashboard could
# see it. Reports/ is the one channel that reaches both. Tolerant of every
# failure - status reporting must never be the reason a sync fails.
function Write-SyncStatus([string]$outcome, [string]$reason) {
    try {
        $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $localHead = ""
        $upstreamHead = ""
        try { $localHead = Invoke-Git @("rev-parse", "--short", "HEAD") } catch { }
        try { $upstreamHead = Invoke-Git @("rev-parse", "--short", "$UpstreamRemote/$Branch") } catch { }

        $previous = ""
        $previousOutcome = ""
        $previousReason = ""
        $previousGenerated = $null
        $lastSuccess = ""
        if (Test-Path $script:StatusPath) {
            $previous = Get-Content $script:StatusPath -Raw -ErrorAction SilentlyContinue
            if ($previous -match '(?m)^Outcome:\s*(.*)$') { $previousOutcome = $Matches[1].Trim() }
            if ($previous -match '(?m)^Reason:\s*(.*)$') { $previousReason = $Matches[1].Trim() }
            if ($previous -match '(?m)^Last-Success:\s*(.*)$') { $lastSuccess = $Matches[1].Trim() }
            if ($previous -match '(?m)^Generated:\s*(\S+ \S+)') {
                try { $previousGenerated = [datetime]::ParseExact($Matches[1], "yyyy-MM-dd HH:mm:ss", $null) } catch { }
            }
        }
        if ($outcome -eq "OK" -or $outcome -eq "UP-TO-DATE") { $lastSuccess = $now }

        $oneLineReason = ($reason -replace "\r?\n", " | ").Trim()
        if ($oneLineReason.Length -gt 600) { $oneLineReason = $oneLineReason.Substring(0, 600) + "..." }

        $text = @(
            "# Auto-sync status",
            "",
            "Generated: $now",
            "Outcome: $outcome",
            "Reason: $oneLineReason",
            "Last-Success: $lastSuccess",
            "Local-Head: $localHead",
            "Upstream-Head: $upstreamHead",
            "Branch: $Branch",
            "",
            "Written by scripts/desktop/sync-and-run.ps1 on every run. Outcome is one of",
            "OK (merged and pushed), UP-TO-DATE (nothing new), BLOCKED (refused to merge",
            "over local edits - see Reason), FAILED (git or network error - see Reason).",
            ""
        ) -join "`r`n"

        $dir = Split-Path $script:StatusPath -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Set-Content -Path $script:StatusPath -Value $text -Encoding UTF8

        $unchanged = ($previousOutcome -eq $outcome) -and ($previousReason -eq $oneLineReason)
        $fresh = $false
        if ($previousGenerated) {
            $fresh = ((Get-Date) - $previousGenerated).TotalHours -lt $script:StatusHeartbeatHours
        }
        $isTracked = $true
        try { Invoke-Git @("ls-files", "--error-unmatch", $script:StatusRelativePath) | Out-Null } catch { $isTracked = $false }

        if ($isTracked -and $unchanged -and $fresh) {
            # Same outcome as last time and the committed copy is recent:
            # restore it so the tree stays clean for the dirty check.
            Invoke-Git @("checkout", "--", $script:StatusRelativePath) | Out-Null
            return
        }

        Invoke-Git @("add", $script:StatusRelativePath) | Out-Null
        Invoke-Git @("commit", "-m", "chore: auto-sync status $outcome") | Out-Null
        Invoke-Git @("push", $OriginRemote, $Branch) -Retries 2 | Out-Null
        Write-Log "Sync status '$outcome' committed to $script:StatusRelativePath"
    }
    catch {
        Write-Log "Could not record sync status (continuing): $_"
    }
}

function Show-Result([string]$message, [string]$icon) {
    Write-Log $message
    if ($Silent) { return }

    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, "Game Factory Sync", "OK", $icon) | Out-Null
}

# git writes progress to stderr, so this deliberately captures both streams
# and judges success by the exit code only. Network-ish operations get the
# 2s/4s/8s/16s retry ladder the project uses everywhere else.
function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [int]$Retries = 0)

    $ErrorActionPreference = "Continue"
    $attempt = 0

    while ($true) {
        $output = & git -C $RepoPath @Arguments 2>&1
        if ($LASTEXITCODE -eq 0) { return ($output | Out-String).Trim() }

        if ($attempt -ge $Retries) {
            throw "git $($Arguments -join ' ') failed (exit $LASTEXITCODE):`n$(($output | Out-String).Trim())"
        }

        $delay = [Math]::Pow(2, $attempt + 1)
        Write-Log "git $($Arguments -join ' ') failed; retrying in ${delay}s"
        Start-Sleep -Seconds ([int]$delay)
        $attempt++
    }
}

try {
    if (-not (Test-Path (Join-Path $RepoPath ".git"))) {
        throw "'$RepoPath' is not a git repository. Pass -RepoPath <path to the clone>."
    }

    $remotes = (Invoke-Git @("remote")) -split "\r?\n"
    foreach ($required in @($UpstreamRemote, $OriginRemote)) {
        if ($remotes -notcontains $required) {
            throw "Remote '$required' is not configured in $RepoPath (found: $($remotes -join ', '))."
        }
    }

    # Collect Unity's errors and check Builds/ for real output into
    # committed reports BEFORE the dirty check below - writing them is
    # itself a tracked-file change, so it has to be committed here rather
    # than left to trip that check. This is the whole point of the loop:
    # Claude Code has no Unity, no GitHub Actions API access, and no other
    # way to see this PC, so both have to reach it through the repo.
    if (-not $SkipErrorReport) {
        $devDir = Join-Path (Split-Path $PSScriptRoot -Parent) "dev"

        try {
            & (Join-Path $devDir "collect-errors.ps1") -RepoPath $RepoPath -Branch $Branch -OriginRemote $OriginRemote -Commit -NoPush
        }
        catch {
            # Never let error reporting be the reason a sync fails.
            Write-Log "Error report step failed (continuing with the sync): $_"
        }

        try {
            & (Join-Path $devDir "report-build-status.ps1") -RepoPath $RepoPath -Branch $Branch -OriginRemote $OriginRemote -Commit -NoPush
        }
        catch {
            Write-Log "Build status report step failed (continuing with the sync): $_"
        }

        # The orchestrator writes one file per run under Reports/runs/ (see
        # AI_GAME_COMPANY/company/orchestrator/runlog.py). Committing them here
        # is what turns "the run failed on the PC console" into something
        # Claude can read from the fork. Only that directory is staged.
        try {
            $runsDir = Join-Path $RepoPath "Reports\runs"
            if (Test-Path $runsDir) {
                Invoke-Git @("add", "--", "Reports/runs") | Out-Null
                $staged = Invoke-Git @("diff", "--cached", "--name-only", "--", "Reports/runs")
                if ($staged) {
                    Invoke-Git @("commit", "-m", "chore: orchestrator run log") | Out-Null
                    Write-Log "Committed orchestrator run log: $(($staged -split "\r?\n").Count) file(s)"
                }
            }
        }
        catch {
            Write-Log "Run log commit step failed (continuing with the sync): $_"
        }
    }

    # Tracked files that TOOLS rewrite, not people. Unity re-resolves packages
    # whenever the Editor is open and rewrites packages-lock.json as a side
    # effect. Left as a hard stop, that one file silently froze every
    # scheduled sync from the moment the Editor was opened - the same file
    # that once made the board runner report correct work as BLOCKED. It is
    # Unity's to write (CLAUDE.md: never hand-edit it), so its churn is
    # committed as housekeeping, the same treatment ProjectVersion.txt gets.
    $toolOwned = @("Packages/packages-lock.json")
    foreach ($toolFile in $toolOwned) {
        $state = Invoke-Git @("status", "--porcelain", "--untracked-files=no", "--", $toolFile)
        if ($state -match '^\s*M\s') {
            Write-Log "$toolFile was rewritten by a tool, not a person - committing it as housekeeping."
            Invoke-Git @("add", "--", $toolFile) | Out-Null
            Invoke-Git @("commit", "-m", "chore: tool-regenerated $toolFile") | Out-Null
        }
    }

    # The shared task board is rewritten by every `team run` (status todo ->
    # in_progress -> review/blocked, plus the run's notes). Those edits are
    # the handoff itself - exactly what Claude needs to see - yet as a dirty
    # tracked file they blocked the next sync, and the one after, until a
    # person committed by hand. The very first live run after the fix did
    # this at 09:51 on 2026-09-05. Committed here as housekeeping, but ONLY
    # when the file still parses: a half-typed hand edit must not be
    # committed mid-keystroke.
    $boardFile = "AI_GAME_COMPANY/config/TASKBOARD.json"
    $boardState = Invoke-Git @("status", "--porcelain", "--untracked-files=no", "--", $boardFile)
    if ($boardState -match '^\s*M\s') {
        $boardPath = Join-Path $RepoPath ($boardFile -replace "/", "\")
        $parses = $true
        try { Get-Content $boardPath -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null } catch { $parses = $false }
        if ($parses) {
            Write-Log "$boardFile changed (a run updated task status) - committing it so the handoff reaches upstream."
            Invoke-Git @("add", "--", $boardFile) | Out-Null
            Invoke-Git @("commit", "-m", "chore: task board updated by a run") | Out-Null
        }
        else {
            Write-Log "$boardFile changed but does not parse as JSON - leaving it for a person (it will block the sync)."
        }
    }

    # Refuse to merge over uncommitted edits to tracked files rather than
    # risk losing them. Untracked files are deliberately tolerated: running
    # the generator locally leaves Assets/GeneratedGames and friends lying
    # around, and treating that as "dirty" would block every scheduled sync
    # from then on. A merge that would actually overwrite an untracked file
    # fails on its own, and the handler below restores the original state.
    #
    # One specific tracked file gets the same tolerance: merely opening the
    # Unity Editor can touch ProjectSettings/ProjectVersion.txt (it can
    # append a revision-hash line) even when the actual Unity version is
    # unchanged. Left as a hard stop, this silently blocked every single
    # scheduled sync overnight - the very first thing that happens after
    # a local Editor session is a sync run, and it kept hitting this wall.
    # Auto-commit it, but only when m_EditorVersion itself is unchanged;
    # if that line actually differs, still stop and ask a human, since
    # CLAUDE.md is explicit that the tracked Unity version must never
    # change without an explicit request.
    $dirty = Invoke-Git @("status", "--porcelain", "--untracked-files=no")
    if ($dirty) {
        $dirtyLines = $dirty -split "\r?\n" | Where-Object { $_ }
        $onlyProjectVersion = ($dirtyLines.Count -eq 1) -and ($dirtyLines[0] -match '^\s*M\s+ProjectSettings/ProjectVersion\.txt\s*$')

        if (-not $onlyProjectVersion) {
            Write-SyncStatus "BLOCKED" "Uncommitted changes to tracked files: $dirty"
            throw "Uncommitted changes to tracked files - stopping. Commit or stash them first:`n`n$dirty"
        }

        $projectVersionPath = Join-Path $RepoPath "ProjectSettings\ProjectVersion.txt"
        $oldVersionLine = (Invoke-Git @("show", "HEAD:ProjectSettings/ProjectVersion.txt")) -split "\r?\n" | Where-Object { $_ -like "m_EditorVersion:*" } | Select-Object -First 1
        $newVersionLine = (Get-Content $projectVersionPath) | Where-Object { $_ -like "m_EditorVersion:*" } | Select-Object -First 1

        if ($oldVersionLine -ne $newVersionLine) {
            Write-SyncStatus "BLOCKED" "ProjectVersion.txt m_EditorVersion changed: '$oldVersionLine' -> '$newVersionLine' - needs a human decision"
            throw "ProjectSettings/ProjectVersion.txt's m_EditorVersion changed ('$oldVersionLine' -> '$newVersionLine') - stopping. This needs a human decision, not an automated commit."
        }

        Write-Log "ProjectVersion.txt changed but m_EditorVersion is still '$newVersionLine' - Editor housekeeping, not a real version change. Committing it."
        Invoke-Git @("add", "ProjectSettings/ProjectVersion.txt") | Out-Null
        Invoke-Git @("commit", "-m", "chore: Unity Editor touched ProjectVersion.txt (version unchanged)") | Out-Null
    }

    $currentBranch = Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")
    if ($currentBranch -ne $Branch) {
        Write-Log "Switching from '$currentBranch' to '$Branch'"
        Invoke-Git @("checkout", $Branch) | Out-Null
    }

    Write-Log "Fetching $UpstreamRemote and $OriginRemote"
    Invoke-Git @("fetch", $UpstreamRemote) -Retries 4 | Out-Null
    Invoke-Git @("fetch", $OriginRemote) -Retries 4 | Out-Null

    # What the fork already has is the baseline that decides whether a
    # pipeline run is warranted - not local HEAD. Local commits that were
    # never pushed (say, assets added by hand) are new to CI too.
    $originRef = "$OriginRemote/$Branch"
    $forkHead = $null
    try { $forkHead = Invoke-Git @("rev-parse", "--verify", "--quiet", $originRef) }
    catch { Write-Log "'$originRef' does not exist yet; treating everything as new." }

    Write-Log "Merging $UpstreamRemote/$Branch"
    try {
        Invoke-Git @("merge", "--no-edit", "$UpstreamRemote/$Branch") | Out-Null
    }
    catch {
        $mergeError = $_

        # THE BOARD IS EXPECTED TO CONFLICT. Claude writes specs and review
        # notes upstream while every `team run` here writes status and notes
        # locally, so git sees one file changed on both sides and stops. That
        # stopped this sync three times in a row on 2026-09-05, and a stopped
        # sync is how a whole day of work once went unnoticed. When the board
        # is the ONLY conflict, merge it properly with the three-way tool and
        # carry on; anything else still stops for a person.
        $resolved = $false
        $boardRel = "AI_GAME_COMPANY/config/TASKBOARD.json"
        try {
            # @(...) is load-bearing. Invoke-Git returns ONE string, and a
            # pipeline that yields a single line comes back as a scalar, not
            # an array - so $conflicts[0] would be the first CHARACTER of the
            # path and the board test below could never match. That is why the
            # board auto-merge, which has a passing test suite behind it, had
            # never once fired on this machine.
            $conflicts = @((Invoke-Git @("diff", "--name-only", "--diff-filter=U")) -split "\r?\n" | Where-Object { $_ })
            Write-Log ("Merge conflicts: " + ($conflicts -join ", "))

            # REPORTS ARE THIS MACHINE'S OWN RECORD, so on a conflict this
            # machine's copy wins. They are regenerated on every run, both
            # sides always differ, and there is nothing in the upstream copy
            # worth keeping - it is a snapshot of what this same PC reported
            # earlier. Leaving them to conflict is what turned "the board
            # conflicts" into "nothing syncs at all": the board tool below
            # only fires when the board is the ONLY conflict, and on
            # 2026-09-06 Reports/runs/latest.txt sat next to it and blocked
            # every attempt for a day.
            $ours = @()
            foreach ($path in $conflicts) {
                if ($path -like "Reports/*") {
                    Invoke-Git @("checkout", "--ours", "--", $path) | Out-Null
                    Invoke-Git @("add", "--", $path) | Out-Null
                    $ours += $path
                }
            }
            if ($ours.Count -gt 0) {
                Write-Log ("Kept this machine's copy of: " + ($ours -join ", "))
                $conflicts = @((Invoke-Git @("diff", "--name-only", "--diff-filter=U")) -split "\r?\n" | Where-Object { $_ })
                Write-Log ("Still conflicting: " + ($conflicts -join ", "))
            }

            if ($conflicts.Count -eq 0) {
                Invoke-Git @("commit", "--no-edit") | Out-Null
                Write-Log "Merge completed; only generated reports had conflicted."
                $resolved = $true
            }
            elseif ($conflicts.Count -eq 1 -and $conflicts[0] -eq $boardRel) {
                $tmp = Join-Path $env:TEMP "taskboard-merge-$PID"
                New-Item -ItemType Directory -Path $tmp -Force | Out-Null
                # :1 base, :2 ours (this clone), :3 theirs (upstream).
                foreach ($stage in @(1, 2, 3)) {
                    $text = Invoke-Git @("show", ":${stage}:$boardRel")
                    Set-Content -Path (Join-Path $tmp "$stage.json") -Value $text -Encoding UTF8
                }
                $merger = Join-Path $RepoPath "AI_GAME_COMPANY\tools\merge-taskboard.py"
                $target = Join-Path $RepoPath ($boardRel -replace "/", "\")
                & python $merger (Join-Path $tmp "1.json") (Join-Path $tmp "2.json") (Join-Path $tmp "3.json") $target
                if ($LASTEXITCODE -eq 0) {
                    Invoke-Git @("add", "--", $boardRel) | Out-Null
                    Invoke-Git @("commit", "--no-edit") | Out-Null
                    Write-Log "Task board conflict merged automatically (both sides kept)."
                    $resolved = $true
                }
                else {
                    Write-Log "merge-taskboard.py refused (exit $LASTEXITCODE) - leaving it for a person."
                }
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        catch {
            Write-Log "Board auto-merge attempt failed: $_"
        }

        if (-not $resolved) {
            # Leave the repo exactly as it was so a human can resolve it calmly.
            # A failed abort must not mask why the merge failed in the first place.
            try { Invoke-Git @("merge", "--abort") | Out-Null } catch { Write-Log "merge --abort also failed: $_" }

            Write-SyncStatus "FAILED" "Merge of $UpstreamRemote/$Branch failed: $mergeError"
            throw "Merge failed - stopping (tried to restore the original state). This needs a human:`n`n$mergeError"
        }
    }

    $after = Invoke-Git @("rev-parse", "HEAD")

    Write-Log "Pushing to $originRef"
    Write-Log (Invoke-Git @("push", $OriginRemote, $Branch) -Retries 4)

    if ($forkHead -eq $after) {
        Write-SyncStatus "UP-TO-DATE" "Nothing new on $UpstreamRemote/$Branch"
        Show-Result "Already up to date. Nothing new reached the fork, so no pipeline run was started." "Information"
        exit 0
    }

    Write-SyncStatus "OK" "Merged $UpstreamRemote/$Branch and pushed to $originRef"

    if ($forkHead) {
        $commitCount = Invoke-Git @("rev-list", "--count", "$forkHead..$after")
        $summary = "Pushed $commitCount new commit(s) to the fork."

        $changedPaths = (Invoke-Git @("diff", "--name-only", $forkHead, $after)) -split "\r?\n" | Where-Object { $_ }
        $specChanges = $changedPaths | Where-Object { $_ -like "GameSpecs/*" }

        # An error-report-only push must not start a build: the report is a
        # diagnostic written by this very script, so triggering on it would
        # queue a pointless pipeline run on every scheduled sync that picks
        # up new Unity errors.
        $buildRelevant = $changedPaths | Where-Object { $_ -notlike "Reports/*" }
        if (-not $buildRelevant) {
            Show-Result "$summary`n`nOnly the error report changed, so no pipeline run was started." "Information"
            exit 0
        }
    }
    else {
        $summary = "Pushed the branch to the fork for the first time."
        $specChanges = $null
    }

    if ($NoTrigger) {
        Show-Result "$summary`n`n(-NoTrigger was set, so no pipeline run was started.)" "Information"
        exit 0
    }

    # A push already starts game-factory.yml when GameSpecs/** changed;
    # triggering again here would just queue a duplicate run.
    if ($specChanges) {
        Show-Result "$summary`n`nGameSpecs changed, so the push starts the pipeline on its own.`n`nProgress: https://github.com/$RepoSlug/actions" "Information"
        exit 0
    }

    if (Get-Command gh -ErrorAction SilentlyContinue) {
        Write-Log "Triggering $Workflow for '$GameId' via gh"
        $ErrorActionPreference = "Continue"
        $ghOutput = & gh workflow run $Workflow --repo $RepoSlug --ref $Branch -f "game_id=$GameId" 2>&1
        $ghExit = $LASTEXITCODE
        $ErrorActionPreference = "Stop"

        if ($ghExit -eq 0) {
            Show-Result "$summary`n`nRequested a pipeline run for '$GameId'.`n`nProgress: https://github.com/$RepoSlug/actions" "Information"
            exit 0
        }

        Write-Log "gh workflow run failed (exit $ghExit): $(($ghOutput | Out-String).Trim()) - falling back to a trigger-file push."
    }
    else {
        Write-Log "GitHub CLI not found - using the trigger-file push instead."
    }

    # No CLI, no token: bumping a file under GameSpecs/ and pushing it hits
    # the same push-triggered path a real GameSpec edit would, which is how
    # game-factory.yml is already wired to start on its own (see the .yml's
    # `on: push: paths: GameSpecs/**`). Currently always targets game01,
    # since that push trigger only ever runs with the workflow's default
    # game_id input - fine for now since it is the only game that exists.
    $triggerPath = Join-Path $RepoPath "GameSpecs\.ci-trigger"
    $triggerContent = "Bumped $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') to start the Game Factory Pipeline for '$GameId' without the GitHub CLI. Not a GameSpec - GameValidator only reads *.json here."
    Set-Content -Path $triggerPath -Value $triggerContent -Encoding UTF8

    Invoke-Git @("add", "GameSpecs/.ci-trigger") | Out-Null
    Invoke-Git @("commit", "-m", "chore: trigger Game Factory Pipeline for $GameId") | Out-Null
    Invoke-Git @("push", $OriginRemote, $Branch) -Retries 4 | Out-Null

    Show-Result "$summary`n`nPushed a trigger-file bump to start the pipeline for '$GameId' (no GitHub CLI needed).`n`nProgress: https://github.com/$RepoSlug/actions" "Information"
    exit 0
}
catch {
    # BLOCKED and merge failures already recorded their own status above; this
    # catches everything else (no remote, no network, git missing) so a sync
    # that dies before reaching the merge is still visible from the fork.
    $already = $false
    try {
        if (Test-Path $script:StatusPath) {
            $recent = Get-Content $script:StatusPath -Raw -ErrorAction SilentlyContinue
            if ($recent -match '(?m)^Generated:\s*(\S+ \S+)') {
                $when = [datetime]::ParseExact($Matches[1], "yyyy-MM-dd HH:mm:ss", $null)
                $already = ((Get-Date) - $when).TotalSeconds -lt 120
            }
        }
    } catch { }
    if (-not $already) { Write-SyncStatus "FAILED" "$_" }

    Show-Result "Sync failed:`n`n$_" "Error"
    exit 1
}
