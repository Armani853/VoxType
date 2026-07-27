param(
    [string]$OutputDirectory = "build/private-eval/en-suite-v2"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$resolvedOutput = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::Combine((Get-Location).Path, $OutputDirectory)
)
$audioDirectory = Join-Path $resolvedOutput "audio"
$referenceDirectory = Join-Path $resolvedOutput "references"
[System.IO.Directory]::CreateDirectory($audioDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($referenceDirectory) | Out-Null

$cases = @(
    @{
        Name = "101_en_neutral"
        Text = "Please open the project and review the latest changes before lunch."
        Rate = 0
        Volume = 100
    },
    @{
        Name = "102_en_technical"
        Text = "The Python API connects Django, PostgreSQL, Docker, and GitHub."
        Rate = 0
        Volume = 100
    },
    @{
        Name = "103_en_fast"
        Text = "Build the package, run the tests, check the logs, and publish the local report."
        Rate = 4
        Volume = 100
    },
    @{
        Name = "104_en_quiet"
        Text = "This sentence is intentionally quiet but should remain understandable."
        Rate = 0
        Volume = 28
    },
    @{
        Name = "105_en_punctuation"
        Text = "First, create a new paragraph. Second, verify the API. Finally, stop."
        Rate = -1
        Volume = 100
    }
)

$synthesizer = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $englishVoice = $synthesizer.GetInstalledVoices() |
        Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like "en-*" } |
        Select-Object -First 1
    if ($null -eq $englishVoice) {
        throw "No enabled English Windows SAPI voice is installed."
    }
    $synthesizer.SelectVoice($englishVoice.VoiceInfo.Name)

    foreach ($case in $cases) {
        $wavPath = Join-Path $audioDirectory ($case.Name + ".wav")
        $referencePath = Join-Path $referenceDirectory ($case.Name + ".txt")
        $synthesizer.Rate = $case.Rate
        $synthesizer.Volume = $case.Volume
        $synthesizer.SetOutputToWaveFile($wavPath)
        $synthesizer.Speak($case.Text)
        $synthesizer.SetOutputToNull()
        [System.IO.File]::WriteAllText(
            $referencePath,
            $case.Text + [Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    $manifest = @{
        created_utc = [DateTime]::UtcNow.ToString("O")
        voice = $englishVoice.VoiceInfo.Name
        source = "Windows SAPI synthetic speech generated from original test sentences"
        cases = $cases.Name
    }
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedOutput "manifest.json"),
        ($manifest | ConvertTo-Json -Depth 4) + [Environment]::NewLine,
        $utf8NoBom
    )
    $split = @{
        tuning = @("101", "102", "103", "104")
        held_out = @("105")
        screening = @{
            good = "101"
            medium = "102"
            worst = "104"
            held_observation = "105"
        }
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedOutput "split.json"),
        ($split | ConvertTo-Json -Depth 4) + [Environment]::NewLine,
        $utf8NoBom
    )
}
finally {
    $synthesizer.Dispose()
}

Write-Output $resolvedOutput
