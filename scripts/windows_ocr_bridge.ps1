param(
    [string]$InputDirectory = "",
    [string]$SourceInputDirectory = "",
    [string]$TargetInputDirectory = "",
    [string]$OutputJson = "",
    [string]$SourceLanguageTag = "en-US",
    [string]$TargetLanguageTag = "zh-Hans-CN",
    [switch]$ListLanguages,
    [switch]$ValidateLanguages
)

# Thin Windows-only bridge. OCR capability selection is explicit: unavailable
# languages fail instead of falling back to the system default engine.

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

function Wait-WinRtOperation($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

$available = @(
    [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
        ForEach-Object { $_.LanguageTag }
)

if ($ListLanguages -and -not $ValidateLanguages) {
    $payload = [ordered]@{ available_ocr_languages = $available }
    $json = $payload | ConvertTo-Json -Depth 4
    if ($OutputJson) {
        [System.IO.File]::WriteAllText(
            [System.IO.Path]::GetFullPath($OutputJson),
            $json,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    else {
        Write-Output $json
    }
    exit 0
}

if (-not $OutputJson) {
    throw 'OutputJson is required.'
}
if (-not $ValidateLanguages) {
    if (-not $SourceInputDirectory) {
        $SourceInputDirectory = $InputDirectory
    }
    if (-not $TargetInputDirectory) {
        $TargetInputDirectory = $InputDirectory
    }
    if (-not $SourceInputDirectory -or -not $TargetInputDirectory) {
        throw 'SourceInputDirectory and TargetInputDirectory are required.'
    }
}

foreach ($tag in @($SourceLanguageTag, $TargetLanguageTag)) {
    if ($available -notcontains $tag) {
        throw "Windows OCR language is unavailable: $tag"
    }
}

$engines = @{}
foreach ($tag in @($SourceLanguageTag, $TargetLanguageTag)) {
    if ($engines.ContainsKey($tag)) {
        continue
    }
    $language = [Windows.Globalization.Language]::new($tag)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    if ($null -eq $engine) {
        throw "Windows OCR engine creation failed: $tag"
    }
    $engines[$tag] = $engine
}

if ($ValidateLanguages) {
    $payload = [ordered]@{
        requested_ocr_languages = @($SourceLanguageTag, $TargetLanguageTag)
        available_ocr_languages = $available
        source_language_tag = $SourceLanguageTag
        target_language_tag = $TargetLanguageTag
        source_engine_created = $engines.ContainsKey($SourceLanguageTag)
        target_engine_created = $engines.ContainsKey($TargetLanguageTag)
    }
    $json = $payload | ConvertTo-Json -Depth 4
    if ($OutputJson) {
        [System.IO.File]::WriteAllText(
            [System.IO.Path]::GetFullPath($OutputJson),
            $json,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    else {
        Write-Output $json
    }
    exit 0
}

function Invoke-OcrImage([string]$Path, [string]$Tag) {
    $file = Wait-WinRtOperation (
        [Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)
    ) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRtOperation (
        $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    ) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Wait-WinRtOperation (
            [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
        ) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-WinRtOperation (
            $decoder.GetSoftwareBitmapAsync()
        ) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $ocr = Wait-WinRtOperation (
                $engines[$Tag].RecognizeAsync($bitmap)
            ) ([Windows.Media.Ocr.OcrResult])
            return [ordered]@{
                lines = @($ocr.Lines | ForEach-Object { $_.Text })
                words = @(
                    $ocr.Lines | ForEach-Object {
                        $_.Words | ForEach-Object {
                            [ordered]@{
                                text = $_.Text
                                left = [double]$_.BoundingRect.X
                                top = [double]$_.BoundingRect.Y
                                width = [double]$_.BoundingRect.Width
                                height = [double]$_.BoundingRect.Height
                            }
                        }
                    }
                )
            }
        }
        finally {
            $bitmap.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

$sourceImages = @{}
Get-ChildItem -LiteralPath $SourceInputDirectory -Filter '*.png' -File |
    ForEach-Object { $sourceImages[$_.BaseName] = $_.FullName }
$targetImages = @{}
Get-ChildItem -LiteralPath $TargetInputDirectory -Filter '*.png' -File |
    ForEach-Object { $targetImages[$_.BaseName] = $_.FullName }
$ids = @($sourceImages.Keys + $targetImages.Keys | Sort-Object -Unique)

$results = @()
foreach ($id in $ids) {
    if (-not $sourceImages.ContainsKey($id) -or -not $targetImages.ContainsKey($id)) {
        throw "OCR ROI frame pair is incomplete: $id"
    }
    $source = Invoke-OcrImage $sourceImages[$id] $SourceLanguageTag
    $target = Invoke-OcrImage $targetImages[$id] $TargetLanguageTag
    $results += [pscustomobject][ordered]@{
        id = [int]$id
        languages = [ordered]@{
            $SourceLanguageTag = $source.lines
            $TargetLanguageTag = $target.lines
        }
        words = [ordered]@{
            $SourceLanguageTag = $source.words
            $TargetLanguageTag = $target.words
        }
    }
}

$json = $results | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    [System.IO.Path]::GetFullPath($OutputJson),
    $json,
    [System.Text.UTF8Encoding]::new($false)
)
