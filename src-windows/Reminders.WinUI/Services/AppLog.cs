namespace Reminders.Windows.Services;

internal static class AppLog
{
    private static readonly object Gate = new();
    private static readonly string DirectoryPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RemindersSync", "logs");
    private static readonly string FilePath = Path.Combine(DirectoryPath, "app.log");

    public static void Info(string message) => Write("INFO", message);
    public static void Error(string message, Exception exception) => Write("ERROR", $"{message}: {exception}");

    private static void Write(string level, string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(DirectoryPath);
                RotateIfNeeded();
                File.AppendAllText(FilePath, $"{DateTimeOffset.Now:O} [{level}] {message}{Environment.NewLine}");
            }
        }
        catch { }
    }

    private static void RotateIfNeeded()
    {
        if (!File.Exists(FilePath) || new FileInfo(FilePath).Length < 2 * 1024 * 1024) return;
        var previous = Path.Combine(DirectoryPath, "app.previous.log");
        File.Move(FilePath, previous, true);
    }
}
