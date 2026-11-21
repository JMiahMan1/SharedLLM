using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

namespace JarvisGuard.Core;

/// <summary>
/// Who may be the Jarvis Guard pipe, and who may use it.
///
/// The server side (<see cref="ServerSecurity"/>): owned by SYSTEM; SYSTEM and
/// Administrators have full control; signed-in users may read and write data
/// on it, and nothing else: not create another instance of it (so they cannot
/// stand up a fake agent beside the real one), not change its permissions,
/// not take ownership.
///
/// The client side (<see cref="CheckServer"/>): before the tray or the lock
/// surface sends anything (a parent's offline code, a parent's sign-in), it
/// checks that the pipe it reached is the agent's. A standard user cannot set
/// an object's owner to SYSTEM, so a pipe owned by SYSTEM was made by a SYSTEM
/// process; on top of that, when Windows lets this account read the serving
/// process's token, that token must be SYSTEM's too. The owner is the check
/// that always runs; the token is read where Windows allows it (a standard
/// user usually may not open a SYSTEM process's token, so making it required
/// would refuse the real agent too).
/// </summary>
public static class PipeTrust
{
    public const string SystemSid = "S-1-5-18";
    public const string AdministratorsSid = "S-1-5-32-544";
    public const string AuthenticatedUsersSid = "S-1-5-11";

    /// <summary>The rights users get: data in and out, attributes, read the ACL. Not CreateNewInstance, ChangePermissions or TakeOwnership.</summary>
    public const PipeAccessRights UserRights = PipeAccessRights.ReadWrite;

    /// <summary>
    /// The decision, from what could be read. <paramref name="ownerSid"/> is
    /// the pipe's owner, <paramref name="processSid"/> the serving process's
    /// user when its token could be read (null when Windows would not say).
    /// Null means trusted; otherwise the reason, in words for the screen.
    /// </summary>
    public static string? Decide(string? ownerSid, string? processSid, uint? serverPid)
    {
        var who = serverPid is { } pid ? $" (process {pid})" : "";
        if (string.IsNullOrEmpty(ownerSid))
            return $"Could not check who is serving the Jarvis Guard pipe{who}, so nothing was sent. Tell a parent.";
        if (ownerSid != SystemSid)
            return $"The Jarvis Guard pipe is held by another program{who}, owned by {ownerSid} rather than the system. Nothing was sent. Tell a parent: the Jarvis Guard service needs a restart.";
        if (processSid is not null && processSid != SystemSid)
            return $"The Jarvis Guard pipe is served by a program{who} running as {processSid}, not the system. Nothing was sent. Tell a parent.";
        return null;
    }

    /// <summary>The pipe's ACL, for the agent to create every instance with.</summary>
    public static PipeSecurity ServerSecurity()
    {
        var security = new PipeSecurity();
        var system = new SecurityIdentifier(SystemSid);
        security.SetOwner(system);
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.AddAccessRule(new PipeAccessRule(system, PipeAccessRights.FullControl, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(AdministratorsSid), PipeAccessRights.FullControl, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(AuthenticatedUsersSid), UserRights, AccessControlType.Allow));
        return security;
    }

    /// <summary>Null when <paramref name="pipe"/> (connected) is served by the agent; else why not.</summary>
    public static string? CheckServer(NamedPipeClientStream pipe)
    {
        if (!OperatingSystem.IsWindows()) return "The Jarvis Guard pipe is Windows only.";
        string? owner;
        try { owner = pipe.GetAccessControl().GetOwner(typeof(SecurityIdentifier))?.Value; }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException or InvalidOperationException)
        {
            owner = null;
        }
        uint? pid = GetNamedPipeServerProcessId(pipe.SafePipeHandle, out var p) ? p : null;
        var processSid = pid is { } id ? ProcessUserSid(id) : null;
        return Decide(owner, processSid, pid);
    }

    /// <summary>The session of the client on the other end of a server pipe (which tray is talking).</summary>
    public static uint? ClientSessionId(NamedPipeServerStream server) =>
        OperatingSystem.IsWindows() && GetNamedPipeClientSessionId(server.SafePipeHandle, out var id) ? id : null;

    /// <summary>The user SID of process <paramref name="pid"/>, or null when this account may not read its token.</summary>
    private static string? ProcessUserSid(uint pid)
    {
        var process = OpenProcess(ProcessQueryLimitedInformation, false, pid);
        if (process == IntPtr.Zero) return null;
        try
        {
            if (!OpenProcessToken(process, TokenQuery, out var token)) return null;
            try
            {
                GetTokenInformation(token, TokenUserClass, IntPtr.Zero, 0, out var needed);
                if (needed <= 0) return null;
                var buffer = Marshal.AllocHGlobal(needed);
                try
                {
                    if (!GetTokenInformation(token, TokenUserClass, buffer, needed, out _)) return null;
                    // TOKEN_USER: SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes; }
                    return new SecurityIdentifier(Marshal.ReadIntPtr(buffer)).Value;
                }
                finally { Marshal.FreeHGlobal(buffer); }
            }
            finally { CloseHandle(token); }
        }
        finally { CloseHandle(process); }
    }

    private const uint ProcessQueryLimitedInformation = 0x1000;
    private const uint TokenQuery = 0x0008;
    private const int TokenUserClass = 1;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint serverProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetNamedPipeClientSessionId(SafePipeHandle pipe, out uint clientSessionId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(IntPtr token, int infoClass, IntPtr info, int length, out int returned);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);
}
