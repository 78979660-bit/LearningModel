param(
    [Parameter(Mandatory = $true)]
    [string]$PromptFile,
    [Parameter(Mandatory = $true)]
    [string]$DownloadDirectory,
    [Parameter(Mandatory = $true)]
    [string]$StagingDirectory,
    [int]$TimeoutSeconds = 900,
    [int]$SendDelaySeconds = 5,
    [int]$RetryDelaySeconds = 5,
    [string]$JobId = "",
    [string]$PlaceholderFile = "",
    [string]$DownloadKeywordFile = "",
    [string]$TraceFile = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class StudyChatGPTWin32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
    public struct POINT { public int X; public int Y; }
}
"@

function Write-Trace {
    param([string]$Event, [hashtable]$Fields = @{})
    if ([string]::IsNullOrWhiteSpace($TraceFile)) { return }
    try {
        $payload = @{ ts = (Get-Date).ToString("s"); event = $Event }
        foreach ($key in $Fields.Keys) { $payload[$key] = $Fields[$key] }
        $line = $payload | ConvertTo-Json -Compress
        Add-Content -LiteralPath $TraceFile -Value $line -Encoding UTF8
    }
    catch {}
}

function Write-Result {
    param([bool]$Success, [string]$Code, [string]$Message, [string]$PdfPath = "")
    [PSCustomObject]@{
        success = $Success
        code = $Code
        message = $Message
        pdf_path = $PdfPath
    } | ConvertTo-Json -Compress
    exit $(if ($Success) { 0 } else { 1 })
}

function Get-ChatGPTProcesses {
    return @(
        Get-Process -Name "ChatGPT" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
    )
}

function Get-ChatWindow {
    param([System.Windows.Automation.AutomationElement]$Element)
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $current = $Element
    while ($null -ne $current) {
        if ($current.Current.NativeWindowHandle -ne 0) {
            return $current
        }
        $current = $walker.GetParent($current)
    }
    return $null
}

function Assert-ChatGPTForeground {
    param(
        [IntPtr]$WindowHandle,
        [int]$WaitMilliseconds = 700
    )
    if ($WindowHandle -eq [IntPtr]::Zero -or [StudyChatGPTWin32]::IsIconic($WindowHandle)) {
        Write-Result $false "chatgpt_background" "ChatGPT is minimized or has no usable foreground window."
    }
    [StudyChatGPTWin32]::SetForegroundWindow($WindowHandle) | Out-Null
    Start-Sleep -Milliseconds $WaitMilliseconds
    if ([StudyChatGPTWin32]::GetForegroundWindow() -ne $WindowHandle) {
        Write-Result $false "chatgpt_background" "Windows did not keep ChatGPT in the foreground."
    }
}

function Normalize-ComposerText {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    return (($Text.Normalize([Text.NormalizationForm]::FormKC) -replace "`r`n", "`n" -replace "`r", "`n") -replace "[\u200B-\u200D\u2060\uFEFF]", "").TrimEnd()
}

function Get-ComposerCompactText {
    param([string]$Text)
    return ((Normalize-ComposerText $Text) -replace "\s", "")
}

function Get-ComposerSemanticText {
    param([string]$Text)
    return ((Normalize-ComposerText $Text) -replace "[\p{P}\p{S}\p{C}\p{Z}]", "")
}

function Test-PromptPresent {
    param(
        [string]$Observed,
        [string]$Expected,
        [string]$Marker
    )
    $observedCompact = Get-ComposerCompactText $Observed
    $expectedCompact = Get-ComposerCompactText $Expected
    if ($observedCompact -eq $expectedCompact) {
        return $true
    }
    if ([string]::IsNullOrWhiteSpace($observedCompact) -or [string]::IsNullOrWhiteSpace($expectedCompact)) {
        return $false
    }

    # Electron may normalize whitespace and punctuation exposed by ValuePattern.
    # The unique marker proves that the end of the prompt arrived; the prefix
    # and length ratio protect against accepting a truncated paste.
    $observedSemantic = Get-ComposerSemanticText $Observed
    $expectedSemantic = Get-ComposerSemanticText $Expected
    $prefixLength = [Math]::Min(24, $expectedSemantic.Length)
    $prefix = $expectedSemantic.Substring(0, $prefixLength)
    $hasPrefix = $observedSemantic.Contains($prefix)
    $hasMarker = -not [string]::IsNullOrWhiteSpace($Marker) -and $observedCompact.Contains($Marker)
    $lengthRatio = $observedSemantic.Length / [double]$expectedSemantic.Length
    return $hasPrefix -and $hasMarker -and $lengthRatio -ge 0.90
}

function Get-PdfLinkElements {
    param([System.Windows.Automation.AutomationElement]$ChatWindow)
    $all = @()
    $linkCondition = New-Object System.Windows.Automation.OrCondition(
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Hyperlink
        )),
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        ))
    )
    if ($null -ne $ChatWindow) {
        $all += @($ChatWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants, $linkCondition))
    }
    return $all
}

function Test-JobVisible {
    param(
        [System.Windows.Automation.AutomationElement]$ChatWindow,
        [string]$Marker
    )
    if ([string]::IsNullOrWhiteSpace($Marker)) { return $false }
    if ($null -eq $ChatWindow) { return $false }
    $textCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Text
    )
    foreach ($element in @($ChatWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCondition))) {
        if ([string]$element.Current.Name -like "*$Marker*") {
            return $true
        }
    }
    return $false
}

function Get-SendButton {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    foreach ($automationId in @("send-button", "composer-submit-button")) {
        $idCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            $automationId
        )
        foreach ($process in @(Get-ChatGPTProcesses)) {
            $processCondition = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
                $process.Id
            )
            $condition = New-Object System.Windows.Automation.AndCondition($processCondition, $idCondition)
            $button = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
            if ($null -ne $button -and $button.Current.IsEnabled -and -not $button.Current.IsOffscreen) {
                return $button
            }
        }
    }
    return $null
}

function Get-PromptInput {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $idCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
        "prompt-textarea"
    )
    foreach ($process in @(Get-ChatGPTProcesses)) {
        $processCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
            $process.Id
        )
        $condition = New-Object System.Windows.Automation.AndCondition($processCondition, $idCondition)
        $element = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
        if ($null -ne $element -and $element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
            return $element
        }
    }
    return $null
}

function Test-GenerationStarted {
    param(
        [System.Windows.Automation.AutomationElement]$ChatWindow,
        [string]$ExpectedPrompt,
        [string]$Marker
    )
    for ($sample = 0; $sample -lt 4; $sample++) {
        $button = Get-SendButton
        if (
            $null -ne $button -and
            -not [string]::IsNullOrWhiteSpace($script:OriginalSendButtonName) -and
            [string]$button.Current.Name -ne $script:OriginalSendButtonName
        ) {
            return $true
        }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Click-Element {
    param([System.Windows.Automation.AutomationElement]$Element)
    $invokePattern = $null
    if ($Element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invokePattern)) {
        $invokePattern.Invoke()
        return
    }
    if ($Element.Current.IsOffscreen) {
        $scrollPattern = $null
        if ($Element.TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern, [ref]$scrollPattern)) {
            $scrollPattern.ScrollIntoView()
            Start-Sleep -Milliseconds 450
        }
        try {
            $Element.SetFocus()
            Start-Sleep -Milliseconds 250
        }
        catch {}
    }
    try {
        $point = $Element.GetClickablePoint()
        $x = [int]$point.X
        $y = [int]$point.Y
    }
    catch {
        $rect = $Element.Current.BoundingRectangle
        if ($rect.Width -le 0 -or $rect.Height -le 0) { throw }
        $x = [int]($rect.X + ($rect.Width / 2))
        $y = [int]($rect.Y + ($rect.Height / 2))
    }
    [StudyChatGPTWin32]::SetCursorPos($x, $y) | Out-Null
    [StudyChatGPTWin32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [StudyChatGPTWin32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

function Test-DownloadName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    if ($DownloadKeywordFile -and (Test-Path -LiteralPath $DownloadKeywordFile)) {
        foreach ($keyword in @(Get-Content -LiteralPath $DownloadKeywordFile -Encoding UTF8)) {
            if ($keyword -and $Name.IndexOf($keyword, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $true
            }
        }
    }
    return $false
}

function Test-PdfFile {
    param([string]$Path)
    try {
        $stream = [System.IO.File]::Open($Path, "Open", "Read", "ReadWrite")
        try {
            $buffer = New-Object byte[] 5
            if ($stream.Read($buffer, 0, 5) -ne 5) { return $false }
            return [System.Text.Encoding]::ASCII.GetString($buffer) -eq "%PDF-"
        }
        finally {
            $stream.Dispose()
        }
    }
    catch {
        return $false
    }
}

function Confirm-SaveDialog {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windowCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Window
    )
    foreach ($dialog in @($root.FindAll([System.Windows.Automation.TreeScope]::Children, $windowCondition))) {
        if ([string]$dialog.Current.ClassName -ne "#32770") { continue }
        $fileNameCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            "1001"
        )
        $saveCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            "1"
        )
        $fileName = $dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $fileNameCondition)
        $saveButton = $dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $saveCondition)
        if ($null -eq $fileName -or $null -eq $saveButton) { continue }
        New-Item -ItemType Directory -Path $StagingDirectory -Force | Out-Null
        $target = Join-Path $StagingDirectory ("chatgpt_generated_" + (Get-Date -Format "yyyyMMdd_HHmmss_fff") + ".pdf")
        $valuePattern = $null
        if (-not $fileName.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
            continue
        }
        $valuePattern.SetValue($target)
        Start-Sleep -Milliseconds 250
        Click-Element $saveButton
        return $target
    }
    return ""
}

function Trigger-BrowserSave {
    param([IntPtr]$ChatWindowHandle)
    Start-Sleep -Milliseconds 900
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    foreach ($process in @(Get-Process -Name "chrome" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })) {
        $processCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
            $process.Id
        )
        $saveCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            "save"
        )
        $condition = New-Object System.Windows.Automation.AndCondition($processCondition, $saveCondition)
        foreach ($button in @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition))) {
            if (-not $button.Current.IsOffscreen -and (Test-DownloadName ([string]$button.Current.Name))) {
                [StudyChatGPTWin32]::SetForegroundWindow([IntPtr]$process.MainWindowHandle) | Out-Null
                Start-Sleep -Milliseconds 350
                Click-Element $button
                Start-Sleep -Milliseconds 800
                return
            }
        }
    }
}

try {
    Write-Trace "ps_workflow_start" @{ job_id = $JobId; timeout = $TimeoutSeconds }
    $prompt = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
    if (@(Get-ChatGPTProcesses).Count -eq 0) {
        Write-Trace "ps_no_chatgpt_process" @{ job_id = $JobId }
        Write-Result $false "chatgpt_not_running" "ChatGPT process was not found."
    }
    Write-Trace "ps_chatgpt_process_found" @{ job_id = $JobId }
    $input = Get-PromptInput
    if ($null -eq $input) {
        Write-Trace "ps_prompt_input_missing" @{ job_id = $JobId }
        Write-Result $false "chatgpt_window_not_visible" "ChatGPT has no visible prompt window."
    }
    Write-Trace "ps_prompt_input_found" @{ job_id = $JobId }
    $chatWindow = Get-ChatWindow $input
    if ($null -eq $chatWindow -or $chatWindow.Current.NativeWindowHandle -eq 0) {
        Write-Result $false "send_failed" "ChatGPT top-level window could not be identified."
    }
    $chatWindowHandle = [IntPtr]$chatWindow.Current.NativeWindowHandle

    $valuePattern = $null
    $currentValue = ""
    if ($input.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
        $currentValue = [string]$valuePattern.Current.Value
    }
    $bridgeOwned = (
        $currentValue.Contains("[PERSONAL_LEARNING_OS_PDF_WORKFLOW]") -or
        ($currentValue.Contains("TEMPLATE_ID") -and $currentValue.Contains("PDF"))
    )
    $knownPlaceholder = $false
    if ($PlaceholderFile -and (Test-Path -LiteralPath $PlaceholderFile)) {
        $knownPlaceholder = @(
            Get-Content -LiteralPath $PlaceholderFile -Encoding UTF8 |
            Where-Object { $_ -eq $currentValue }
        ).Count -gt 0
    }
    if (
        -not [string]::IsNullOrWhiteSpace($currentValue) -and
        $currentValue -ne [string]$input.Current.Name -and
        -not $knownPlaceholder -and
        -not (Test-PromptPresent $currentValue $prompt $JobId) -and
        -not $bridgeOwned
    ) {
        Write-Result $false "input_not_empty" "ChatGPT prompt input contains another draft."
    }
    if ($DryRun) {
        Write-Trace "ps_dry_run_ready" @{ job_id = $JobId }
        Write-Result $true "ready" "ChatGPT PDF workflow is ready."
    }

    $startedAt = Get-Date
    $knownPdfs = @{}
    Get-ChildItem -LiteralPath $DownloadDirectory -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -ieq ".pdf" } |
        ForEach-Object { $knownPdfs[$_.FullName] = $_.LastWriteTimeUtc.Ticks }
    $existingLinks = @{}
    foreach ($element in @(Get-PdfLinkElements $chatWindow)) {
        $name = [string]$element.Current.Name
        if (Test-DownloadName $name) {
            $fingerprint = "$name|$($element.Current.BoundingRectangle.ToString())"
            $existingLinks[$fingerprint] = $true
        }
    }

    $oldForeground = [StudyChatGPTWin32]::GetForegroundWindow()
    $oldCursor = New-Object StudyChatGPTWin32+POINT
    [StudyChatGPTWin32]::GetCursorPos([ref]$oldCursor) | Out-Null
    $clipboardText = ""
    Write-Trace "ps_clipboard_read_start" @{ job_id = $JobId }
    try {
        if ([System.Windows.Forms.Clipboard]::ContainsText()) {
            $clipboardText = [System.Windows.Forms.Clipboard]::GetText()
        }
        Write-Trace "ps_clipboard_read_ok" @{ job_id = $JobId; chars = $clipboardText.Length }
    }
    catch {
        Write-Trace "ps_clipboard_read_failed" @{ job_id = $JobId; error = $_.Exception.Message }
        $clipboardText = ""
    }
    Assert-ChatGPTForeground $chatWindowHandle 700
    Write-Trace "ps_foreground_asserted" @{ job_id = $JobId }
    $submitInput = $input
    $script:OriginalSendButtonName = ""
    $input.SetFocus()
    Write-Trace "ps_clipboard_write_start" @{ job_id = $JobId; chars = $prompt.Length }
    try {
        [System.Windows.Forms.Clipboard]::SetText($prompt)
        Write-Trace "ps_clipboard_write_ok" @{ job_id = $JobId }
    }
    catch {
        Write-Trace "ps_clipboard_write_failed" @{ job_id = $JobId; error = $_.Exception.Message }
        Write-Result $false "write_failed" ("Clipboard write failed: " + $_.Exception.Message)
    }
    try {
        [System.Windows.Forms.SendKeys]::SendWait("^a")
        Write-Trace "ps_select_all_sent" @{ job_id = $JobId }
        [System.Windows.Forms.SendKeys]::SendWait("^v")
        Write-Trace "ps_paste_sent" @{ job_id = $JobId }
    }
    catch {
        Write-Trace "ps_paste_failed" @{ job_id = $JobId; error = $_.Exception.Message }
        Write-Result $false "write_failed" ("Paste failed: " + $_.Exception.Message)
    }
    Write-Trace "ps_prompt_pasted" @{ job_id = $JobId; chars = $prompt.Length }
    # The ChatGPT desktop editor exposes unreliable ValuePattern text after
    # paste. Do not validate the copied text; allow React to settle, then send.
    Start-Sleep -Seconds $SendDelaySeconds
    Assert-ChatGPTForeground $chatWindowHandle 300
    $input.SetFocus()
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Write-Trace "ps_enter_sent" @{ job_id = $JobId }
    Start-Sleep -Seconds 2
    Write-Trace "ps_send_assumed_after_enter" @{ job_id = $JobId }
    if ($clipboardText) {
        [System.Windows.Forms.Clipboard]::SetText($clipboardText)
    }
    [StudyChatGPTWin32]::SetForegroundWindow($oldForeground) | Out-Null
    [StudyChatGPTWin32]::SetCursorPos($oldCursor.X, $oldCursor.Y) | Out-Null

    # PDF-link discovery uses a separate process with a hard timeout. UIA
    # descendant scans in the ChatGPT desktop app can otherwise block forever.
    Write-Result $true "generation_started" "ChatGPT entered generation state."

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $downloadClicked = $false
    $generationFinishedSamples = 0
    while ((Get-Date) -lt $deadline) {
        try {
            if (
                [StudyChatGPTWin32]::IsIconic($chatWindowHandle) -or
                $chatWindow.Current.IsOffscreen
            ) {
                Write-Result $false "chatgpt_background" "ChatGPT became minimized or hidden while generating."
            }
        }
        catch {
            Write-Result $false "chatgpt_background" "The ChatGPT window became unavailable while generating."
        }
        $savedPath = Confirm-SaveDialog
        if ($savedPath) {
            for ($wait = 0; $wait -lt 20; $wait++) {
                Start-Sleep -Milliseconds 500
                if ((Test-Path -LiteralPath $savedPath) -and (Test-PdfFile $savedPath)) {
                    Write-Result $true "pdf_downloaded" "PDF saved through Save As dialog." $savedPath
                }
            }
        }
        $stateButton = Get-SendButton
        if (
            $null -ne $stateButton -and
            -not [string]::IsNullOrWhiteSpace($script:OriginalSendButtonName) -and
            [string]$stateButton.Current.Name -eq $script:OriginalSendButtonName
        ) {
            $generationFinishedSamples++
        }
        else {
            $generationFinishedSamples = 0
        }
        if ($generationFinishedSamples -ge 2) {
            $activeChatWindow = $chatWindow
            $activeInput = Get-PromptInput
            if ($null -ne $activeInput) {
                $refreshedWindow = Get-ChatWindow $activeInput
                if ($null -ne $refreshedWindow) {
                    $activeChatWindow = $refreshedWindow
                }
            }
            foreach ($element in @(Get-PdfLinkElements $activeChatWindow)) {
                $name = [string]$element.Current.Name
                $fingerprint = "$name|$($element.Current.BoundingRectangle.ToString())"
                if (
                    $element.Current.IsEnabled -and
                    ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Hyperlink -or
                     $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) -and
                    (Test-DownloadName $name) -and
                    -not $existingLinks.ContainsKey($fingerprint)
                ) {
                    [StudyChatGPTWin32]::SetForegroundWindow($chatWindowHandle) | Out-Null
                    Start-Sleep -Milliseconds 250
                    Click-Element $element
                    Trigger-BrowserSave $chatWindowHandle
                    [StudyChatGPTWin32]::SetCursorPos($oldCursor.X, $oldCursor.Y) | Out-Null
                    $downloadClicked = $true
                    break
                }
            }
        }

        $savedPath = Confirm-SaveDialog
        if ($savedPath) {
            for ($wait = 0; $wait -lt 20; $wait++) {
                Start-Sleep -Milliseconds 500
                if ((Test-Path -LiteralPath $savedPath) -and (Test-PdfFile $savedPath)) {
                    Write-Result $true "pdf_downloaded" "PDF saved through Save As dialog." $savedPath
                }
            }
        }

        foreach ($file in @(Get-ChildItem -LiteralPath $DownloadDirectory -File -ErrorAction SilentlyContinue)) {
            $isNew = -not $knownPdfs.ContainsKey($file.FullName) -or $file.LastWriteTime -ge $startedAt
            if ($isNew -and $file.Length -gt 0 -and (Test-PdfFile $file.FullName)) {
                Start-Sleep -Seconds 1
                $refreshed = Get-Item -LiteralPath $file.FullName -ErrorAction SilentlyContinue
                if ($null -ne $refreshed -and $refreshed.Length -eq $file.Length -and (Test-PdfFile $refreshed.FullName)) {
                    New-Item -ItemType Directory -Path $StagingDirectory -Force | Out-Null
                    $targetName = [System.IO.Path]::GetFileNameWithoutExtension($refreshed.Name) + ".pdf"
                    $target = Join-Path $StagingDirectory $targetName
                    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
                    Write-Result $true "pdf_downloaded" "PDF downloaded." $target
                }
            }
        }
        Start-Sleep -Seconds $(if ($downloadClicked) { 2 } else { 4 })
    }
    Write-Result $false $(if ($downloadClicked) { "pdf_download_failed" } else { "pdf_link_not_found" }) "Timed out waiting for PDF."
}
catch {
    Write-Trace "ps_workflow_exception" @{ job_id = $JobId; error = $_.Exception.Message }
    Write-Result $false "bridge_error" $_.Exception.Message
}
