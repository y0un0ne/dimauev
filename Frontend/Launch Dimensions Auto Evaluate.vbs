Option Explicit

Dim shell, fso, folder, script, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(folder, "start_frontend.ps1")

If Not fso.FileExists(script) Then
    MsgBox "Frontend launcher is missing:" & vbCrLf & script, vbCritical, "Dimensions Auto Evaluate"
    WScript.Quit 1
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """ -HiddenLaunch"
shell.Run command, 0, False
