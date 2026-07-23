using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using System.Windows;

namespace HoudiniIntelligenceLauncher;

public partial class App : Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        try
        {
            var projectRoot = ProjectRootLocator.Find(AppContext.BaseDirectory);
            ValidateProjectLauncher(projectRoot);

            if (Array.Exists(e.Args, argument =>
                    string.Equals(argument, "--smoke-test", StringComparison.OrdinalIgnoreCase)))
            {
                Shutdown(0);
                return;
            }

            using var process = StartPowerShellLauncher(projectRoot, e.Args);
            await process.WaitForExitAsync();
            Shutdown(process.ExitCode);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                $"Big-Chicken Launcher could not start.\n\n{exception.Message}",
                "Big-Chicken Launcher",
                MessageBoxButton.OK,
                MessageBoxImage.Error
            );
            Shutdown(1);
        }
    }

    private static void ValidateProjectLauncher(string projectRoot)
    {
        var systemRoot = Environment.GetEnvironmentVariable("SystemRoot");
        if (string.IsNullOrWhiteSpace(systemRoot))
        {
            throw new InvalidOperationException("Windows SystemRoot is unavailable.");
        }

        var powerShell = Path.Combine(
            systemRoot,
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe"
        );
        if (!File.Exists(powerShell))
        {
            throw new FileNotFoundException("Windows PowerShell 5.1 is unavailable.", powerShell);
        }

        var script = Path.Combine(projectRoot, "scripts", "hia-launcher.ps1");
        if (!File.Exists(script))
        {
            throw new FileNotFoundException("The project launcher script is missing.", script);
        }
    }

    private static Process StartPowerShellLauncher(string projectRoot, string[] forwardedArguments)
    {
        var systemRoot = Environment.GetEnvironmentVariable("SystemRoot")!;
        var powerShell = Path.Combine(
            systemRoot,
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe"
        );
        var script = Path.Combine(projectRoot, "scripts", "hia-launcher.ps1");

        var startInfo = new ProcessStartInfo
        {
            FileName = powerShell,
            WorkingDirectory = projectRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        };
        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-Sta");
        startInfo.ArgumentList.Add("-ExecutionPolicy");
        startInfo.ArgumentList.Add("Bypass");
        startInfo.ArgumentList.Add("-File");
        startInfo.ArgumentList.Add(script);
        foreach (var argument in forwardedArguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        var process = new Process { StartInfo = startInfo };
        if (!process.Start())
        {
            process.Dispose();
            throw new InvalidOperationException("Windows PowerShell did not start.");
        }

        return process;
    }
}
