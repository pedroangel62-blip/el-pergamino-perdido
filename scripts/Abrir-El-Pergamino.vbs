Option Explicit

Dim shell, fso, scriptDir, ps1Path, commandLine, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path = fso.BuildPath(scriptDir, "abrir-pergamino.ps1")

commandLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & ps1Path & Chr(34)
exitCode = shell.Run(commandLine, 0, True)

If exitCode <> 0 Then
    MsgBox "No se pudo abrir El Pergamino Perdido. Codigo: " & exitCode, 16, "El Pergamino Perdido"
End If
