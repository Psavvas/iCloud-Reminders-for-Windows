using System.Text.Json;

namespace Reminders.Windows.Services;

internal static class UiPreferences
{
    private static readonly object Gate = new();
    private static readonly string DirectoryPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RemindersForWindows");
    private static readonly string FilePath = Path.Combine(DirectoryPath, "ui-settings.json");

    public static bool LoadNavigationPaneOpen()
    {
        try
        {
            lock (Gate)
            {
                if (!File.Exists(FilePath)) return true;
                using var document = JsonDocument.Parse(File.ReadAllText(FilePath));
                return !document.RootElement.TryGetProperty("navigation_pane_open", out var value) || value.GetBoolean();
            }
        }
        catch (Exception error)
        {
            AppLog.Error("Could not load UI preferences", error);
            return true;
        }
    }

    public static void SaveNavigationPaneOpen(bool isOpen)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(DirectoryPath);
                var temporary = FilePath + ".tmp";
                File.WriteAllText(temporary, JsonSerializer.Serialize(new { navigation_pane_open = isOpen }));
                File.Move(temporary, FilePath, true);
            }
        }
        catch (Exception error) { AppLog.Error("Could not save UI preferences", error); }
    }
}
