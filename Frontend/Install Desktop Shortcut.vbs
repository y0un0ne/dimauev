Option Explicit

Dim shell, fso, folder, script, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(folder, "install_desktop_shortcut.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """"
exitCode = shell.Run(command, 1, True)

If exitCode = 0 Then
    MsgBox "Desktop shortcuts installed.", vbInformation, "Dimensions Auto Evaluate"
Else
    MsgBox "Could not install desktop shortcuts.", vbCritical, "Dimensions Auto Evaluate"
End If
