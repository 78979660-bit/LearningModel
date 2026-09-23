param(
    [Parameter(Mandatory = $true)][string]$DownloadDirectory,
    [Parameter(Mandatory = $true)][string]$StagingDirectory,
    [Parameter(Mandatory = $true)][string]$DownloadKeywordFile,
    [string]$JobId = "",
    [string]$StartedAtUtc = "",
    [string]$TraceFile = "",
    [int]$TimeoutSeconds = 120
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class CapturePdfWin32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);
}
"@
function Trace([string]$event,[hashtable]$fields=@{}) {
    if([string]::IsNullOrWhiteSpace($TraceFile)){return}
    try {
        $payload=@{ts=(Get-Date).ToString("s");event=$event}
        foreach($key in $fields.Keys){$payload[$key]=$fields[$key]}
        Add-Content -LiteralPath $TraceFile -Value ($payload|ConvertTo-Json -Compress) -Encoding UTF8
    } catch {}
}
function Result([bool]$ok,[string]$code,[string]$message,[string]$path="") {
    Trace "ps_capture_result" @{job_id=$JobId;success=$ok;code=$code;message=$message;path=$path}
    [pscustomobject]@{success=$ok;code=$code;message=$message;pdf_path=$path}|ConvertTo-Json -Compress
    exit $(if($ok){0}else{1})
}
function IsPdf([string]$path) {
    try {$s=[IO.File]::Open($path,"Open","Read","ReadWrite");$b=New-Object byte[] 5;$n=$s.Read($b,0,5);$s.Dispose();return $n -eq 5 -and [Text.Encoding]::ASCII.GetString($b) -eq "%PDF-"} catch {return $false}
}
function ClickElement($element) {
    $ip=$null;if($element.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$ip)){$ip.Invoke();return}
    if($element.Current.IsOffscreen) {
        $sp=$null;if($element.TryGetCurrentPattern([Windows.Automation.ScrollItemPattern]::Pattern,[ref]$sp)){$sp.ScrollIntoView();Start-Sleep -Milliseconds 450}
        try{$element.SetFocus();Start-Sleep -Milliseconds 250}catch{}
    }
    try {$pt=$element.GetClickablePoint();$x=[int]$pt.X;$y=[int]$pt.Y} catch {$r=$element.Current.BoundingRectangle;if($r.Width -le 0 -or $r.Height -le 0){throw};$x=[int]($r.X+$r.Width/2);$y=[int]($r.Y+$r.Height/2)}
    [CapturePdfWin32]::SetCursorPos($x,$y)|Out-Null
    [CapturePdfWin32]::mouse_event(2,0,0,0,[UIntPtr]::Zero);Start-Sleep -Milliseconds 100;[CapturePdfWin32]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
}
function ConfirmSaveDialog {
    $root=[Windows.Automation.AutomationElement]::RootElement
    $wc=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::Window)
    foreach($dialog in @($root.FindAll([Windows.Automation.TreeScope]::Children,$wc))) {
        if([string]$dialog.Current.ClassName -ne "#32770"){continue}
        $fc=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty,"1001")
        $sc=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty,"1")
        $field=$dialog.FindFirst([Windows.Automation.TreeScope]::Descendants,$fc);$save=$dialog.FindFirst([Windows.Automation.TreeScope]::Descendants,$sc)
        if(!$field -or !$save){continue}
        New-Item -ItemType Directory -Path $StagingDirectory -Force|Out-Null
        $target=Join-Path $StagingDirectory ("chatgpt_captured_"+(Get-Date -Format "yyyyMMdd_HHmmss_fff")+".pdf")
        $vp=$null;if(-not $field.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$vp)){continue}
        $vp.SetValue($target);Start-Sleep -Milliseconds 250;ClickElement $save;return $target
    }
    return ""
}
function TriggerBrowserSave([IntPtr]$chatHandle) {
    Start-Sleep -Milliseconds 900
    $root=[Windows.Automation.AutomationElement]::RootElement
    foreach($process in @(Get-Process -Name chrome -ErrorAction SilentlyContinue|Where-Object{$_.MainWindowHandle -ne 0})) {
        $pc=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty,$process.Id)
        $sc=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty,"save")
        foreach($candidate in @($root.FindAll([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.AndCondition($pc,$sc))))) {
            $name=[string]$candidate.Current.Name;$match=$false
            foreach($keyword in $keywords){if($keyword -and $name.IndexOf($keyword,[StringComparison]::OrdinalIgnoreCase) -ge 0){$match=$true}}
            if($match -and -not $candidate.Current.IsOffscreen) {
                [CapturePdfWin32]::SetForegroundWindow([IntPtr]$process.MainWindowHandle)|Out-Null
                Start-Sleep -Milliseconds 350
                ClickElement $candidate
                Start-Sleep -Milliseconds 800
                return
            }
        }
    }
}
try {
    Trace "ps_capture_start" @{job_id=$JobId;timeout=$TimeoutSeconds}
    $keywords=@(Get-Content -LiteralPath $DownloadKeywordFile -Encoding UTF8)
    $startedAt = [DateTime]::UtcNow.AddMinutes(-1)
    if (-not [string]::IsNullOrWhiteSpace($StartedAtUtc)) {
        try {
            $startedAt = [DateTime]::Parse(
                $StartedAtUtc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
            )
        }
        catch {
            $startedAt = [DateTime]::UtcNow
        }
    }
    $generationErrorPhrases=@(
        -join ([char[]]@(22788,29702,20320,30340,35831,27714,26102,20986,38169)),
        -join ([char[]]@(22788,29702,35831,27714,26102,20986,38169)),
        "Something went wrong",
        "There was an error"
    )
    $retryLabel=-join ([char[]]@(37325,35797))
    $visibleChat = Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if($null -ne $visibleChat) {
        Trace "ps_capture_visible_chatgpt" @{job_id=$JobId;pid=$visibleChat.Id}
        [CapturePdfWin32]::SetForegroundWindow([IntPtr]$visibleChat.MainWindowHandle)|Out-Null
        Start-Sleep -Milliseconds 400
        [System.Windows.Forms.SendKeys]::SendWait("^{END}")
        Start-Sleep -Milliseconds 700
    }
    $root=[Windows.Automation.AutomationElement]::RootElement
    $button=$null
    $buttonDeadline=(Get-Date).AddSeconds($TimeoutSeconds)
    while($null -eq $button -and (Get-Date) -lt $buttonDeadline) {
        $bestScore=-1
        $jobMarkerFound=[string]::IsNullOrWhiteSpace($JobId)
        foreach($p in @(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue)) {
            $pc=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty,$p.Id)
            $elements=@($root.FindAll([Windows.Automation.TreeScope]::Descendants,$pc))
            $markerIndex=-1
            $nextMarkerIndex=$elements.Count
            if(-not [string]::IsNullOrWhiteSpace($JobId)) {
                for($i=0;$i -lt $elements.Count;$i++) {
                    $markerName=[string]$elements[$i].Current.Name
                    if($markerName.IndexOf($JobId,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
                        $markerIndex=$i
                        $jobMarkerFound=$true
                    }
                }
                if($markerIndex -lt 0){continue}
                for($i=$markerIndex+1;$i -lt $elements.Count;$i++) {
                    $laterName=[string]$elements[$i].Current.Name
                    if(
                        $laterName.IndexOf("PLOS_JOB_",[StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                        $laterName.IndexOf($JobId,[StringComparison]::OrdinalIgnoreCase) -lt 0
                    ) {
                        $nextMarkerIndex=$i
                        break
                    }
                }
            }
            for($i=[Math]::Max(0,$markerIndex+1);$i -lt $nextMarkerIndex;$i++) {
                $e=$elements[$i]
                $name=[string]$e.Current.Name
                $isGenerationError=$name -eq $retryLabel
                foreach($errorPhrase in $generationErrorPhrases) {
                    if($name.IndexOf($errorPhrase,[StringComparison]::OrdinalIgnoreCase) -ge 0){$isGenerationError=$true}
                }
                if($isGenerationError) {
                    Trace "ps_capture_generation_error" @{job_id=$JobId;name=$name}
                    Result $false "generation_failed" "ChatGPT reported an error after the current request."
                }
                if($e.Current.ControlType -ne [Windows.Automation.ControlType]::Button -and $e.Current.ControlType -ne [Windows.Automation.ControlType]::Hyperlink){continue}
                if(-not $e.Current.IsEnabled){continue}
                $score=0
                foreach($keyword in $keywords){if($keyword -and $name.IndexOf($keyword,[StringComparison]::OrdinalIgnoreCase) -ge 0){$score++}}
                if($name.IndexOf("PDF",[StringComparison]::OrdinalIgnoreCase) -ge 0){$score+=10}
                # Never click an arbitrary ChatGPT button while the PDF link
                # has not appeared. Only positive keyword matches are valid.
                # UIA returns conversation controls in document order. For
                # equally scored PDF links, keep the last one (newest reply)
                # instead of repeatedly selecting an older exercise.
                if($score -gt 0 -and $score -ge $bestScore){$bestScore=$score;$button=$e}
            }
        }
        if($null -eq $button){
            Trace "ps_capture_waiting_for_pdf_link" @{job_id=$JobId;marker_found=$jobMarkerFound;best_score=$bestScore}
            if(-not $jobMarkerFound){Start-Sleep -Seconds 2}else{Start-Sleep -Seconds 3}
        }
    }
    if($null -eq $button){
        if(-not [string]::IsNullOrWhiteSpace($JobId) -and -not $jobMarkerFound) {
            Result $false "job_marker_not_found" "The current request marker is not visible in ChatGPT."
        }
        Result $false "pdf_link_not_found" "No PDF download button exists after the current request."
    }
    Trace "ps_capture_pdf_link_found" @{job_id=$JobId;name=[string]$button.Current.Name}
    $known=@{};Get-ChildItem -LiteralPath $DownloadDirectory -File -ErrorAction SilentlyContinue|ForEach-Object{$known[$_.FullName]=$_.LastWriteTimeUtc.Ticks}
    $walker=[Windows.Automation.TreeWalker]::ControlViewWalker;$window=$button
    while($window -and $window.Current.NativeWindowHandle -eq 0){$window=$walker.GetParent($window)}
    $chatHandle=[IntPtr]$window.Current.NativeWindowHandle
    [CapturePdfWin32]::SetForegroundWindow($chatHandle)|Out-Null
    Start-Sleep -Milliseconds 700
    ClickElement $button
    Trace "ps_capture_pdf_link_clicked" @{job_id=$JobId}
    TriggerBrowserSave $chatHandle
    $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
    while((Get-Date) -lt $deadline) {
        $saved=ConfirmSaveDialog
        if($saved) {
            Trace "ps_capture_save_dialog" @{job_id=$JobId;path=$saved}
            for($wait=0;$wait -lt 20;$wait++){Start-Sleep -Milliseconds 500;if((Test-Path -LiteralPath $saved) -and (IsPdf $saved)){Result $true "pdf_downloaded" "PDF saved through Save As dialog." $saved}}
        }
        foreach($f in @(Get-ChildItem -LiteralPath $DownloadDirectory -File -Force -ErrorAction SilentlyContinue)) {
            if((-not $known.ContainsKey($f.FullName) -and $f.LastWriteTimeUtc -ge $startedAt.AddSeconds(-5)) -and (IsPdf $f.FullName)) {
                Start-Sleep -Seconds 1;$fresh=Get-Item -LiteralPath $f.FullName -ErrorAction SilentlyContinue
                if($fresh -and (IsPdf $fresh.FullName)){New-Item -ItemType Directory -Path $StagingDirectory -Force|Out-Null;$target=Join-Path $StagingDirectory ([IO.Path]::GetFileNameWithoutExtension($fresh.Name)+".pdf");Copy-Item -LiteralPath $fresh.FullName -Destination $target -Force;Trace "ps_capture_download_file_found" @{job_id=$JobId;source=$fresh.FullName;target=$target};Result $true "pdf_downloaded" "PDF downloaded." $target}
            }
        }
        Start-Sleep -Seconds 1
    }
    Result $false "pdf_download_failed" "Timed out waiting for the downloaded PDF."
} catch {Trace "ps_capture_exception" @{job_id=$JobId;error=$_.Exception.Message}; Result $false "bridge_error" $_.Exception.Message}
