$ErrorActionPreference = "Stop"

$detectorRoot = Split-Path -Parent $PSScriptRoot
$vendorRoot = Join-Path $detectorRoot "data/vendor"
New-Item -ItemType Directory -Force $vendorRoot | Out-Null

function Get-Repository([string]$Url, [string]$Name) {
    $target = Join-Path $vendorRoot $Name
    if (-not (Test-Path -LiteralPath $target)) {
        git clone --depth 1 $Url $target
        if ($LASTEXITCODE -ne 0) { throw "git clone failed: $Url" }
    }
}

# Downloads into "<target>.part" and renames only after the transfer finishes, so an
# interrupted run never leaves a truncated file that looks complete to the next run.
function Get-DatasetFile([string]$Url, [string]$Target, [long]$ExpectedBytes = 0) {
    if (Test-Path -LiteralPath $Target) {
        $actual = (Get-Item -LiteralPath $Target).Length
        if ($ExpectedBytes -gt 0 -and $actual -ne $ExpectedBytes) {
            Write-Host "re-downloading $Target ($actual bytes, expected $ExpectedBytes)"
            Remove-Item -LiteralPath $Target -Force
        } else {
            return
        }
    }
    New-Item -ItemType Directory -Force (Split-Path -Parent $Target) | Out-Null
    $part = "$Target.part"
    # Zenodo drops long transfers, so resume the .part file until curl reports success.
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        curl.exe -L --fail --retry 3 --continue-at - --output $part $Url
        if ($LASTEXITCODE -eq 0) { break }
        if ($attempt -eq 10) { throw "download failed after $attempt attempts: $Url" }
        Write-Host "resuming $Target (attempt $attempt failed)"
    }
    $size = (Get-Item -LiteralPath $part).Length
    if ($size -eq 0) { throw "empty download: $Url" }
    if ($ExpectedBytes -gt 0 -and $size -ne $ExpectedBytes) {
        throw "size mismatch for ${Target}: got $size bytes, expected $ExpectedBytes"
    }
    Move-Item -LiteralPath $part -Destination $Target -Force
}

Get-Repository "https://github.com/compass-group-tue/prompt_injections_so_back.git" `
    "prompt_injections_so_back"
Get-Repository "https://github.com/microsoft/BIPIA.git" "BIPIA"
Get-Repository "https://github.com/uiuc-kang-lab/InjecAgent.git" "InjecAgent"
Get-Repository "https://github.com/cw-l/email-corpus.git" "email-corpus"

$llmailRoot = Join-Path $vendorRoot "llmail-hf"
$llmailBase = "https://huggingface.co/datasets/microsoft/llmail-inject-challenge/resolve/main/data"
# Sizes taken from the Hugging Face dataset listing; they guard against truncated files.
foreach ($entry in @(
    @{ Name = "raw_submissions_phase1.jsonl"; Bytes = 1666413489 },
    @{ Name = "raw_submissions_phase2.jsonl"; Bytes = 263506099 },
    @{ Name = "emails_for_fp_tests.json"; Bytes = 55486 }
)) {
    Get-DatasetFile "$llmailBase/$($entry.Name)`?download=true" `
        (Join-Path $llmailRoot $entry.Name) $entry.Bytes
}

$phishingRoot = Join-Path $vendorRoot "phishing-curated"
# Sizes come from https://zenodo.org/api/records/8339691 (record 8339691 file listing).
foreach ($entry in @(
    @{ Name = "CEAS_08.csv"; Bytes = 67902056 },
    @{ Name = "Enron.csv"; Bytes = 44837129 },
    @{ Name = "Ling.csv"; Bytes = 9337509 },
    @{ Name = "Nazario.csv"; Bytes = 7811841 },
    @{ Name = "Nazario_5.csv"; Bytes = 11755517 },
    @{ Name = "Nigerian_5.csv"; Bytes = 18253749 },
    @{ Name = "Nigerian_Fraud.csv"; Bytes = 9192393 },
    @{ Name = "SpamAssasin.csv"; Bytes = 14879195 },
    @{ Name = "TREC_05.csv"; Bytes = 123130931 },
    @{ Name = "TREC_06.csv"; Bytes = 41922386 },
    @{ Name = "TREC_07.csv"; Bytes = 100246925 }
)) {
    $url = "https://zenodo.org/api/records/8339691/files/$($entry.Name)/content"
    Get-DatasetFile $url (Join-Path $phishingRoot $entry.Name) $entry.Bytes
}

Write-Host "Training datasets are ready under $vendorRoot"
