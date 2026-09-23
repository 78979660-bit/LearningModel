param(
    [Parameter(Mandatory = $true)]
    [string]$PromptFile,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Result {
    param(
        [bool]$Success,
        [string]$Code,
        [string]$Message
    )
    [PSCustomObject]@{
        success = $Success
        code = $Code
        message = $Message
    } | ConvertTo-Json -Compress
    exit $(if ($Success) { 0 } else { 1 })
}

function Test-VisuallyEmptyPrompt {
    param(
        [string]$Value,
        [string]$AccessibleName
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    # ChatGPT's Electron editor exposes its visible placeholder through
    # ValuePattern while the actual editor is empty.
    if ($Value -eq $AccessibleName) {
        return $true
    }
    $withoutInvisibleCharacters = $Value -replace "[\u200B-\u200D\u2060\uFEFF]", ""
    return [string]::IsNullOrWhiteSpace($withoutInvisibleCharacters)
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $prompt = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
    $processes = @(Get-Process -Name "ChatGPT" -ErrorAction SilentlyContinue)
    if ($processes.Count -eq 0) {
        Write-Result $false "chatgpt_not_running" "ChatGPT process was not found."
    }

    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $input = $null
    for ($attempt = 0; $attempt -lt 4 -and $null -eq $input; $attempt++) {
        foreach ($process in $processes) {
            $condition = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
                $process.Id
            )
            $elements = $root.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $condition
            )
            foreach ($element in $elements) {
                if (
                    $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and
                    $element.Current.IsEnabled -and
                    -not $element.Current.IsOffscreen -and
                    $element.Current.BoundingRectangle.Width -gt 0 -and
                    $element.Current.BoundingRectangle.Height -gt 0 -and
                    (
                        $element.Current.AutomationId -eq "prompt-textarea" -or
                        $element.Current.Name -eq "与 ChatGPT 聊天"
                    )
                ) {
                    $input = $element
                    break
                }
            }
            if ($null -ne $input) { break }
        }
        if ($null -eq $input) { Start-Sleep -Milliseconds 400 }
    }

    if ($null -eq $input) {
        Write-Result $false "input_not_found" "ChatGPT prompt input was not found."
    }

    $valuePattern = $null
    if (-not $input.TryGetCurrentPattern(
        [System.Windows.Automation.ValuePattern]::Pattern,
        [ref]$valuePattern
    )) {
        Write-Result $false "value_pattern_unavailable" "ValuePattern is unavailable."
    }
    if ($valuePattern.Current.IsReadOnly) {
        Write-Result $false "input_readonly" "ChatGPT prompt input is read-only."
    }

    $currentValue = [string]$valuePattern.Current.Value
    $accessibleName = [string]$input.Current.Name
    if ($currentValue -eq $prompt) {
        Write-Result $true "already_filled" "The same prompt is already present."
    }
    if (-not (Test-VisuallyEmptyPrompt $currentValue $accessibleName)) {
        Write-Result $false "input_not_empty" "ChatGPT prompt input already contains text."
    }
    if ($DryRun) {
        Write-Result $true "ready" "ChatGPT prompt input is ready."
    }

    $valuePattern.SetValue($prompt)
    Start-Sleep -Milliseconds 150
    $writtenValue = [string]$valuePattern.Current.Value
    if ($writtenValue -ne $prompt) {
        Write-Result $false "write_failed" "The prompt could not be verified after writing."
    }
    Write-Result $true "filled" "Prompt filled; waiting for user confirmation."
}
catch {
    Write-Result $false "bridge_error" $_.Exception.Message
}
