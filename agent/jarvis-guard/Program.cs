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

        var loaded = Config.Load();
        if (loaded.Config is null)
        {
            // Only a missing file is "not set up"; access denied or a damaged
            // file is its own error. Either way the reason goes to the console,
            // the log and the state file the tray and the lock surface read.
            var error = loaded.Error ?? $"No usable configuration at {Config.ConfigPath}.";
            Console.Error.WriteLine(error);
            GuardService.Log($"not starting: {error}");
            PublishConfigError(error);
            return loaded.Missing ? 2 : 3;
        }
        var config = loaded.Config;

        Console.WriteLine($"Jarvis Guard agent {config.Version} starting for '{config.Username}' (hub {config.HubUrl}).");
        Directory.CreateDirectory(Config.Root);

        var service = new GuardService(config);
        try
        {
            await service.RunAsync();
        }
        catch (Exception ex)
        {
            // A failure to start guarding (for example another program holding
            // the pipe) ends the process loudly; the scheduled task restarts it.
            GuardService.Log($"stopped: {ex}");
            Console.Error.WriteLine($"Jarvis Guard stopped: {ex.Message}");
            return 4;
        }
        return 0;
    }

    private static void PublishConfigError(string error)
    {
        try
        {
            Config.WriteState(new GuardState
            {
                Source = "config-error",
                Problem = $"Jarvis Guard is not guarding this PC: {error}",
                AsOfUtc = DateTime.UtcNow.ToString("O"),
            });
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Console.Error.WriteLine($"Could not publish the error to {Config.StatePath}: {ex.Message}");
        }
    }
}
