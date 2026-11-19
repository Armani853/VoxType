[CmdletBinding()]
param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\build\real-world-audio'))

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$references = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'references.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$output = New-Item -ItemType Directory -Path $OutputDirectory -Force
$format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
)
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$voices = @{ RU = 'Microsoft Irina Desktop'; EN = 'Microsoft Zira Desktop' }
try {
    $rows = foreach ($scenario in $references.PSObject.Properties) {
        $lang, $seconds = $scenario.Name -split '-'
        $synth.SelectVoice($voices[$lang])
        $path = Join-Path $output.FullName ($scenario.Name + '.wav')
        $bestError = [double]::PositiveInfinity
        $temp = [IO.Path]::GetTempFileName()
        try {
            foreach ($rate in @(0, -1, 1, -2, 2, -3, 3)) {
                $synth.Rate = $rate
                try {
                    $synth.SetOutputToWaveFile($temp, $format)
                    $synth.Speak($scenario.Value)
                } finally {
                    $synth.SetOutputToNull()
                }
                # SAPI emits an uncompressed RIFF wave. Read the data chunk,
                # excluding header and any non-audio metadata.
                $reader = [IO.BinaryReader]::new([IO.File]::OpenRead($temp))
                try {
                    $reader.BaseStream.Position = 12
                    do {
                        $chunk = [Text.Encoding]::ASCII.GetString($reader.ReadBytes(4))
                        $size = $reader.ReadUInt32()
                        if ($chunk -ne 'data') {
                            $reader.BaseStream.Position += $size + ($size % 2)
                        }
                    } until ($chunk -eq 'data')
                } finally {
                    $reader.Dispose()
                }
                $duration = $size / 32000.0
                $errorSeconds = [Math]::Abs($duration - [int]$seconds)
                if ($errorSeconds -lt $bestError) {
                    Copy-Item -LiteralPath $temp -Destination $path -Force
                    $bestError = $errorSeconds
                    $bestDuration = $duration
                    $bestRate = $rate
                }
                if ($errorSeconds -le ([int]$seconds * 0.1)) { break }
            }
        } finally {
            Remove-Item -LiteralPath $temp
        }
        [pscustomobject]@{
            id = $scenario.Name
            target_seconds = [int]$seconds
            wav_seconds = [Math]::Round($bestDuration, 3)
            source = 'Offline Windows SAPI TTS'
            voice = $synth.Voice.Name
            rate = $bestRate
            within_tolerance = $bestError -le ([int]$seconds * 0.1)
            wav = Split-Path -Leaf $path
        }
    }
    $rows | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output.FullName 'manifest.json') -Encoding UTF8
    $rows | Format-Table -AutoSize
    if ($rows.Where({ -not $_.within_tolerance }).Count) {
        throw 'One or more speech fixtures are outside the 10 percent duration tolerance.'
    }
} finally {
    $synth.Dispose()
}
