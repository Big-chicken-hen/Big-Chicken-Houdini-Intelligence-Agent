using System;
using System.IO;

namespace HoudiniIntelligenceLauncher
{
    public static class ProjectRootLocator
    {
        public static string Find(string startingDirectory)
        {
            if (string.IsNullOrWhiteSpace(startingDirectory))
            {
                throw new ArgumentException("A launcher directory is required.", "startingDirectory");
            }

            DirectoryInfo current = new DirectoryInfo(Path.GetFullPath(startingDirectory));
            while (true)
            {
                if (HasProjectMarkers(current.FullName))
                {
                    return current.FullName.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                }

                var parent = current.Parent;
                if (parent == null)
                {
                    break;
                }
                current = parent;
            }

            throw new InvalidOperationException(
                "The Big-Chicken Houdini Intelligence Agent project root could not be derived from the launcher location."
            );
        }

        private static bool HasProjectMarkers(string candidate)
        {
            return File.Exists(Path.Combine(candidate, "pyproject.toml"))
                && File.Exists(Path.Combine(candidate, "scripts", "hia-launcher.ps1"))
                && File.Exists(Path.Combine(candidate, "scripts", "launch-houdini.ps1"));
        }
    }
}
