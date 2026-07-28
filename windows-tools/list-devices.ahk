#Requires AutoHotkey v2.0
#Include Lib\AutoHotInterception.ahk

; Enumerate every device the Interception driver can see, without the
; MsgBox-on-miss lookups. Uses the low-level Instance API directly.
out := "=== AHI device list " A_Now "`r`n"
try {
    AHI := AutoHotInterception()
    inst := AHI.GetInstance()
    for d in inst.GetDeviceList()
        out .= Format("id={} mouse={} vid=0x{:04X} pid=0x{:04X} handle={}`r`n",
            d.Id, d.IsMouse, d.Vid, d.Pid, d.Handle)
} catch as e {
    out .= "ERROR: " e.Message "`r`n"
}
FileAppend(out, A_ScriptDir "\devices.log")
ExitApp
