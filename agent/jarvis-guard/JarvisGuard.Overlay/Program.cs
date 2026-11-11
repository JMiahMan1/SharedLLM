using System.Windows.Forms;

namespace JarvisGuard.Overlay;

internal static class Program
{
    public static void Run()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new LockForm());
    }
}
