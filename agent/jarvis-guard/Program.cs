using JarvisGuard.Agent;
using JarvisGuard.Core;

// Single binary, three modes. No arguments (or --serve) runs the long-lived
// SYSTEM agent: it polls the hub once a minute, publishes the public state
// file the tray and the lock surface read, and signs interactive sessions
// out when the household locks the PC.
// --tray   runs the tray shield every signed-in user gets.
// --overlay runs the lock surface for one PC-wide presentation.
namespace JarvisGuard;

internal static class Program
{
    [STAThread]
    private static async Task<int> Main(string[] args)
    {
        var mode = args is { Length: > 0 } ? args[0] : "--serve";

        if (string.Equals(mode, "--tray", StringComparison.OrdinalIgnoreCase))
        {
            Tray.Program.Run();
            return 0;
        }
        if (string.Equals(mode, "--overlay", StringComparison.OrdinalIgnoreCase))
        {
            Overlay.Program.Run();
            return 0;
        }
        if (string.Equals(mode, "--serve", StringComparison.OrdinalIgnoreCase))
        {
            return await RunServiceAsync();
        }

        Console.Error.WriteLine("Usage: jarvis-guard [--serve | --tray | --overlay]");
        return 2;
    }

    private static async Task<int> RunServiceAsync()
    {
        // Runs as a plain long-lived process under a boot-time scheduled task
        // (see install.ps1) so it needs no service-control plumbing in .NET itself.
        Console.OutputEncoding = System.Text.Encoding.UTF8;

        var config = Config.Load();
        if (config is null)
        {
            Console.Error.WriteLine($"No Jarvis Guard configuration at {Config.ConfigPath}. Run install.ps1 as an administrator.");
            return 2;
        }

        Console.WriteLine($"Jarvis Guard agent {config.Version} starting for '{config.Username}' (hub {config.HubUrl}).");
        Directory.CreateDirectory(Config.Root);

        var service = new GuardService(config);
        await service.RunAsync();
        return 0;
    }
}
