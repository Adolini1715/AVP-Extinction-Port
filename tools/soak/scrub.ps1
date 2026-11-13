# scrub.ps1 <session dir>: run by play.bat after the game exits (also after a crash).
# Takes the player's Windows identity out of everything in the session folder before it is
# sent: the profile folder, the user name and the PC name become <home>, <user>, <pc> in the
# text files; in the game memory dump (guest.bin, binary) every copy of the user or PC name,
# ASCII or UTF-16, is overwritten with asterisks of the same length. Then every file is
# checked again and the folder is zipped as <session dir>.zip.
param([Parameter(Mandatory = $true)][string]$Dir)
$ErrorActionPreference = 'Continue'

$names = @()                               # (text, placeholder): the longest first
if ($env:USERPROFILE) { $names += , @($env:USERPROFILE, '<home>'); $names += , @(($env:USERPROFILE -replace '\\', '/'), '<home>') }
if ($env:USERNAME -and $env:USERNAME.Length -ge 3) { $names += , @($env:USERNAME, '<user>') }
if ($env:COMPUTERNAME -and $env:COMPUTERNAME.Length -ge 3) { $names += , @($env:COMPUTERNAME, '<pc>') }

$utf8 = New-Object System.Text.UTF8Encoding $false
$latin1 = [System.Text.Encoding]::GetEncoding(28591)        # one char per byte: a binary file round-trips

$text = Get-ChildItem -LiteralPath $Dir -File -Recurse | Where-Object { $_.Extension -in '.log', '.csv', '.jsonl', '.txt', '.ini', '.json' }
foreach ($f in $text) {
    $s = [System.IO.File]::ReadAllText($f.FullName)
    foreach ($n in $names) { $s = [regex]::Replace($s, [regex]::Escape($n[0]), $n[1], 'IgnoreCase') }
    [System.IO.File]::WriteAllText($f.FullName, $s, $utf8)
}

$bins = Get-ChildItem -LiteralPath $Dir -File -Recurse | Where-Object { $_.Extension -in '.bin', '.dmp' }
foreach ($f in $bins) {
    $s = $latin1.GetString([System.IO.File]::ReadAllBytes($f.FullName))
    foreach ($n in $names) {
        if ($n[1] -eq '<home>') { continue }               # the bare names cover the folder too
        $plain = $n[0]
        $wide = ($plain.ToCharArray() | ForEach-Object { "$_`0" }) -join ''
        foreach ($p in @($plain, $wide)) {
            $s = [regex]::Replace($s, [regex]::Escape($p), { param($m) '*' * $m.Length }, 'IgnoreCase')
        }
    }
    [System.IO.File]::WriteAllBytes($f.FullName, $latin1.GetBytes($s))
}

# the check: nothing left in any file, text or not
$left = 0
foreach ($f in (Get-ChildItem -LiteralPath $Dir -File -Recurse)) {
    if ($f.Extension -in '.png', '.bmp', '.jpg') { continue }  # pictures of the game screen
    $s = $latin1.GetString([System.IO.File]::ReadAllBytes($f.FullName))
    foreach ($n in $names) {
        if ($n[1] -eq '<home>') { continue }
        if ($s.IndexOf($n[0], [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $left++; Write-Host "  still in $($f.Name)" }
    }
}
if ($left -eq 0) { "privacy check: clean (no user or PC name in any file)" | Out-File -LiteralPath (Join-Path $Dir 'privacy.txt') -Encoding ascii }
else { "privacy check: $left file(s) still name this PC or user; do not send" | Out-File -LiteralPath (Join-Path $Dir 'privacy.txt') -Encoding ascii }
Get-Content -LiteralPath (Join-Path $Dir 'privacy.txt')

if ($left -eq 0) {
    Compress-Archive -Path (Join-Path $Dir '*') -DestinationPath "$Dir.zip" -Force
    Write-Host "Ready to send: $(Split-Path -Leaf $Dir).zip (in the sessions folder)"
}
