param(
    [Parameter(Mandatory = $true)]
    [string]$InputDirectory,
    [Parameter(Mandatory = $true)]
    [string]$OutputJson
)

# This is intentionally a thin Windows-only bridge. The project Python runtime
# has no WinRT OCR binding, and using Windows.Media.Ocr avoids downloading an
# OCR package or model. Frame extraction, cleanup, merging, and SRT writing stay
# in Python.

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

$engines = @{}
foreach ($tag in @('zh-Hans-CN', 'en-US')) {
    $language = [Windows.Globalization.Language]::new($tag)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    if ($null -eq $engine) {
        throw "Windows OCR language is unavailable: $tag"
    }
    $engines[$tag] = $engine
}

$results = @()
foreach ($image in Get-ChildItem -LiteralPath $InputDirectory -Filter '*.png' -File | Sort-Object Name) {
    $file = Wait-WinRtOperation (
        [Windows.Storage.StorageFile]::GetFileFromPathAsync($image.FullName)
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
            $row = [ordered]@{ id = [int]$image.BaseName; languages = @{} }
            foreach ($tag in @('zh-Hans-CN', 'en-US')) {
                $ocr = Wait-WinRtOperation (
                    $engines[$tag].RecognizeAsync($bitmap)
                ) ([Windows.Media.Ocr.OcrResult])
                $row.languages[$tag] = @(
                    $ocr.Lines | ForEach-Object { $_.Text }
                )
            }
            $results += [pscustomobject]$row
        }
        finally {
            $bitmap.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

$json = $results | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText(
    [System.IO.Path]::GetFullPath($OutputJson),
    $json,
    [System.Text.UTF8Encoding]::new($false)
)
