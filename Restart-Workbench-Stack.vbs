Option Explicit
' Double-click launcher: restarts the workbench stack (portal 8768 + DSH 3080)
' without a console window. Keep ASCII-only: wscript reads this as ANSI.
Dim shell, fso, root, script, result
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(root, "Restart-Workbench-Stack.ps1")
result = shell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """", 0, True)
If result <> 0 Then
    MsgBox "Workbench restart failed. See logs:" & vbCrLf & _
        shell.ExpandEnvironmentStrings("%TEMP%\workbench\launcher.log") & vbCrLf & _
        shell.ExpandEnvironmentStrings("%TEMP%\workbench.stderr.log"), vbExclamation, "Workbench"
End If
