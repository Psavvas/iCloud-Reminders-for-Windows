using Microsoft.UI.Xaml;

namespace Reminders.Windows;

public partial class App : Application
{
    private Window? _window;
    public App()
    {
        InitializeComponent();
        UnhandledException += (_, args) => Services.AppLog.Error("Unhandled UI exception", args.Exception);
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        _window.Activate();
    }
}
