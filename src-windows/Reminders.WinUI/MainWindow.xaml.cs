using Microsoft.UI.Windowing;
using Microsoft.Windows.AppNotifications;
using Microsoft.Windows.AppNotifications.Builder;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using Reminders.Windows.Services;
using System.Collections.ObjectModel;
using System.Numerics;
using System.Text.Json;
using Windows.Graphics;
using Windows.UI.ViewManagement;

namespace Reminders.Windows;

public sealed partial class MainWindow : Window
{
    private readonly SidecarClient _sidecar = new();
    private readonly ObservableCollection<ReminderItem> _reminders = [];
    private readonly DispatcherTimer _searchTimer = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private readonly DispatcherTimer _statusTimer = new() { Interval = TimeSpan.FromSeconds(15) };
    private readonly DispatcherTimer _notificationTimer = new() { Interval = TimeSpan.FromSeconds(30) };
    private readonly DispatcherTimer _syncTimer = new() { Interval = TimeSpan.FromMinutes(1) };
    private readonly DispatcherTimer _paneHoverOpenTimer = new() { Interval = TimeSpan.FromMilliseconds(280) };
    private readonly DispatcherTimer _paneHoverCloseTimer = new() { Interval = TimeSpan.FromMilliseconds(420) };
    private List<ReminderList> _lists = [];
    private JsonElement _settings;
    private ReminderItem? _selected;
    private NavEntry _view = new("Today", "", NavKind.Smart, "today");
    private string _sort = "manual";
    private bool _loadingDetail;
    private bool _demo;
    private int _minutesSinceSync;
    private bool _notificationsRegistered;
    private bool _panePinnedOpen;
    private bool _hoverExpanded;
    private bool? _temporaryPaneTarget;
    private bool _initializingPane = true;

    public MainWindow()
    {
        InitializeComponent();
        var settingsAccelerator = new KeyboardAccelerator { Key = (global::Windows.System.VirtualKey)188, Modifiers = global::Windows.System.VirtualKeyModifiers.Control };
        settingsAccelerator.Invoked += SettingsAccelerator_Invoked;
        Root.KeyboardAccelerators.Add(settingsAccelerator);
        Navigation.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(Navigation_PointerMoved), true);
        Navigation.AddHandler(UIElement.PointerExitedEvent, new PointerEventHandler(Navigation_PointerExited), true);
        foreach (var item in Navigation.MenuItems.Concat(Navigation.FooterMenuItems).OfType<NavigationViewItem>()) AttachPaneHover(item);
        ReminderList.ItemsSource = _reminders;
        DetailPriority.SelectedIndex = 0;
        _searchTimer.Tick += async (_, _) => { _searchTimer.Stop(); await LoadRemindersAsync(); };
        _statusTimer.Tick += async (_, _) => await RefreshStatusAsync();
        _notificationTimer.Tick += async (_, _) => await CheckNotificationsAsync();
        _syncTimer.Tick += async (_, _) => await BackgroundSyncAsync();
        _paneHoverOpenTimer.Tick += (_, _) => OpenPaneForHover();
        _paneHoverCloseTimer.Tick += (_, _) => CloseHoverPane();
        _sidecar.EventReceived += Sidecar_EventReceived;
        _sidecar.Stopped += (_, reason) => DispatcherQueue.TryEnqueue(() => ShowInfo("Sync service stopped", reason, InfoBarSeverity.Error));
        SetWindowSize();
        _panePinnedOpen = UiPreferences.LoadNavigationPaneOpen();
        Navigation.IsPaneOpen = _panePinnedOpen;
        _initializingPane = false;
        Activated += MainWindow_Activated;
        Closed += MainWindow_Closed;
        try { AppNotificationManager.Default.Register(); _notificationsRegistered = true; } catch { }
    }

    private void SetWindowSize()
    {
        var handle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var id = Microsoft.UI.Win32Interop.GetWindowIdFromWindow(handle);
        var appWindow = AppWindow.GetFromWindowId(id);
        appWindow.Resize(new SizeInt32(1280, 800));
        var icon = Path.Combine(AppContext.BaseDirectory, "Assets", "icon-v2.ico");
        if (File.Exists(icon)) appWindow.SetIcon(icon);
    }

    private async void MainWindow_Activated(object sender, WindowActivatedEventArgs args)
    {
        Activated -= MainWindow_Activated;
        if (Environment.GetCommandLineArgs().Contains("--demo", StringComparer.OrdinalIgnoreCase) || Environment.GetEnvironmentVariable("REMINDERS_DEMO") == "1")
        {
            LoadDemo();
            return;
        }
        await BootAsync();
    }

    private async Task BootAsync()
    {
        SetGateState("Starting the sync service…");
        try
        {
            if (!_sidecar.IsRunning) await _sidecar.StartAsync();
            SetGateState("Checking your account…");
            var status = await _sidecar.CallAsync("auth_status", new { });
            _settings = await _sidecar.CallAsync("settings", new { });
            ApplyTheme(_settings.Text("theme", "system"));
            if (status.Flag("authenticated") || status.Flag("has_cache"))
            {
                AuthGate.Visibility = Visibility.Collapsed;
                await RefreshAllAsync();
                _statusTimer.Start(); _notificationTimer.Start(); _syncTimer.Start();
                if (!status.Flag("authenticated") && !status.Flag("restoring")) ShowInfo("Sign in to resume syncing", "Cached reminders and queued edits remain available.", InfoBarSeverity.Warning);
                return;
            }
            if (status.Flag("restoring")) { SetGateState("Signing you back in…"); return; }
            ShowLogin();
        }
        catch (Exception error)
        {
            SetGateState("The sync service isn't available.", false);
            GateError.Message = error.Message; GateError.IsOpen = true;
        }
    }

    private void SetGateState(string status, bool progress = true)
    {
        AuthGate.Visibility = Visibility.Visible; GateStatus.Text = status; GateProgress.Visibility = progress ? Visibility.Visible : Visibility.Collapsed;
        LoginFields.Visibility = Visibility.Collapsed; CodeFields.Visibility = Visibility.Collapsed; GateError.IsOpen = false;
    }
    private void ShowLogin()
    {
        AuthGate.Visibility = Visibility.Visible; GateProgress.Visibility = Visibility.Collapsed; GateStatus.Text = "Sign in to iCloud"; LoginFields.Visibility = Visibility.Visible; CodeFields.Visibility = Visibility.Collapsed;
        AppleIdBox.Focus(FocusState.Programmatic);
    }

    private async Task RefreshAllAsync()
    {
        await LoadNavigationAsync();
        await LoadRemindersAsync();
        await RefreshStatusAsync();
    }

    private async Task LoadNavigationAsync()
    {
        if (_demo) return;
        var listsTask = _sidecar.CallAsync<List<ReminderList>>("lists", new { });
        var tagsTask = _sidecar.CallAsync<List<string>>("tags", new { });
        var countsTask = _sidecar.CallAsync("smart_counts", new { });
        await Task.WhenAll(listsTask, tagsTask, countsTask);
        _lists = listsTask.Result;
        DetailList.ItemsSource = _lists.Where(list => !list.IsGroup).ToList();
        var counts = countsTask.Result;
        SetBadge("smart:today", counts.Number("today")); SetBadge("smart:upcoming", counts.Number("upcoming")); SetBadge("smart:all", counts.Number("all")); SetBadge("smart:completed", counts.Number("completed"));
        while (Navigation.MenuItems.Count > 7) Navigation.MenuItems.RemoveAt(7);
        foreach (var list in _lists.Where(list => !list.IsGroup))
            Navigation.MenuItems.Add(CreateNavigationItem(list.Title, "\uE8A5", $"list:{list.Id}", list.Count));
        var tags = tagsTask.Result.Distinct(StringComparer.CurrentCultureIgnoreCase).Order().ToList();
        if (tags.Count > 0)
        {
            Navigation.MenuItems.Add(new NavigationViewItemHeader { Content = "Tags" });
            foreach (var tag in tags) Navigation.MenuItems.Add(CreateNavigationItem("#" + tag, "#", $"tag:{tag}"));
        }
    }

    private NavigationViewItem CreateNavigationItem(string label, string glyph, string tag, long count = 0)
    {
        var item = new NavigationViewItem
        {
            Content = label, Tag = tag, Icon = new FontIcon { Glyph = glyph }, InfoBadge = count > 0 ? new InfoBadge { Value = (int)Math.Min(count, 99) } : null
        };
        AttachPaneHover(item);
        return item;
    }

    private void AttachPaneHover(NavigationViewItem item)
    {
        item.PointerEntered += NavigationPaneItem_PointerEntered;
        item.PointerExited += NavigationPaneItem_PointerExited;
    }

    private void NavigationPaneItem_PointerEntered(object sender, PointerRoutedEventArgs e)
    {
        _paneHoverCloseTimer.Stop();
        if (!_panePinnedOpen && !_hoverExpanded && !Navigation.IsPaneOpen && !_paneHoverOpenTimer.IsEnabled)
            _paneHoverOpenTimer.Start();
    }

    private void NavigationPaneItem_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        _paneHoverOpenTimer.Stop();
        if (_hoverExpanded && !_paneHoverCloseTimer.IsEnabled) _paneHoverCloseTimer.Start();
    }
    private void SetBadge(string tag, long count)
    {
        var item = Navigation.MenuItems.OfType<NavigationViewItem>().FirstOrDefault(candidate => candidate.Tag?.ToString() == tag);
        if (item is not null) item.InfoBadge = count > 0 ? new InfoBadge { Value = (int)Math.Min(count, 99) } : null;
    }

    private async Task LoadRemindersAsync()
    {
        if (_demo) { UpdateRows(); return; }
        try
        {
            var query = new Dictionary<string, object?> { ["include_completed"] = ShowCompleted.IsOn, ["search"] = string.IsNullOrWhiteSpace(SearchBox.Text) ? null : SearchBox.Text.Trim(), ["sort"] = _sort };
            query[_view.Kind switch { NavKind.List => "list_id", NavKind.Tag => "tag", _ => "scope" }] = _view.Key;
            var rows = await _sidecar.CallAsync<List<ReminderItem>>("reminders", query);
            var selectedId = _selected?.Id; _reminders.Clear(); foreach (var row in rows) _reminders.Add(row);
            UpdateRows();
            if (selectedId is not null) ReminderList.SelectedItem = _reminders.FirstOrDefault(row => row.Id == selectedId);
        }
        catch (Exception error) { ShowInfo("Couldn't load reminders", error.Message, InfoBarSeverity.Error); }
    }
    private void UpdateRows()
    {
        EmptyState.Visibility = _reminders.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        RowCount.Text = _reminders.Count == 1 ? "1 reminder" : $"{_reminders.Count:N0} reminders";
    }
    private async Task RefreshStatusAsync()
    {
        if (_demo) return;
        try
        {
            var status = await _sidecar.CallAsync("sync_status", new { });
            var conflicts = status.Number("conflicts"); ConflictItem.Visibility = conflicts > 0 ? Visibility.Visible : Visibility.Collapsed; ConflictItem.Content = conflicts == 1 ? "1 sync conflict" : $"{conflicts} sync conflicts";
        }
        catch { }
    }
    private async Task CheckNotificationsAsync()
    {
        if (_demo || !_notificationsRegistered) return;
        try
        {
            var plan = await _sidecar.CallAsync("due_notifications", new { });
            if (!plan.TryGetProperty("toasts", out var toasts)) return;
            foreach (var toast in toasts.EnumerateArray())
            {
                var notification = new AppNotificationBuilder().AddText(toast.Text("title", "Reminder")).AddText(toast.Text("body", "")).BuildNotification();
                AppNotificationManager.Default.Show(notification);
            }
        }
        catch { }
    }
    private async Task BackgroundSyncAsync()
    {
        if (_demo || ++_minutesSinceSync < Math.Clamp((int)_settings.Number("sync_minutes"), 5, 60)) return;
        _minutesSinceSync = 0;
        try { await _sidecar.CallAsync("sync", new { }); } catch { }
    }

    private async void SignIn_Click(object sender, RoutedEventArgs e)
    {
        SetGateState("Signing in…");
        try { await _sidecar.CallAsync("login", new { apple_id = AppleIdBox.Text.Trim(), password = PasswordBox.Password }); PasswordBox.Password = ""; await BootAsync(); }
        catch (SidecarException error) when (error.Code == "2FA_REQUIRED") { try { await _sidecar.CallAsync("request_2fa", new { }); } catch { } GateProgress.Visibility = Visibility.Collapsed; GateStatus.Text = "Enter the code sent to your Apple devices"; CodeFields.Visibility = Visibility.Visible; }
        catch (SidecarException error) when (error.Code == "TERMS_REQUIRED") { await AcceptTermsAsync(); }
        catch (Exception error) { ShowLogin(); GateError.Message = error.Message; GateError.IsOpen = true; }
    }
    private async Task AcceptTermsAsync()
    {
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "Updated iCloud terms", Content = "Apple needs you to accept its updated terms before syncing.", PrimaryButtonText = "Accept and continue", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Primary };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary)
        {
            SetGateState("Accepting terms…");
            try { await _sidecar.CallAsync("login", new { apple_id = AppleIdBox.Text.Trim(), password = PasswordBox.Password, accept_terms = true }); PasswordBox.Password = ""; await BootAsync(); }
            catch (Exception error) { ShowLogin(); GateError.Message = error.Message; GateError.IsOpen = true; }
        }
        else ShowLogin();
    }
    private async void Verify_Click(object sender, RoutedEventArgs e)
    {
        try { await _sidecar.CallAsync("submit_2fa", new { code = CodeBox.Text.Trim() }); CodeBox.Text = ""; await BootAsync(); }
        catch (Exception error) { GateError.Message = error.Message; GateError.IsOpen = true; }
    }

    private void Demo_Click(object sender, RoutedEventArgs e) => LoadDemo();
    private void LoadDemo()
    {
        _demo = true; AuthGate.Visibility = Visibility.Collapsed;
        _settings = JsonDocument.Parse("""{"theme":"system","sync_minutes":10,"notifications_enabled":true}""").RootElement.Clone(); ApplyTheme("system");
        _lists = DemoData.CreateLists(); DetailList.ItemsSource = _lists;
        Navigation.MenuItems.Add(CreateNavigationItem("Inbox", "\uE8A5", "list:inbox", 3)); Navigation.MenuItems.Add(CreateNavigationItem("Work", "\uE821", "list:work", 2)); Navigation.MenuItems.Add(CreateNavigationItem("Personal", "\uE77B", "list:personal", 1)); Navigation.MenuItems.Add(new NavigationViewItemHeader { Content = "Tags" }); Navigation.MenuItems.Add(CreateNavigationItem("#errands", "#", "tag:errands"));
        _reminders.Clear(); foreach (var reminder in DemoData.CreateReminders()) _reminders.Add(reminder);
        UpdateRows(); ReminderList.SelectedIndex = 0;
        ShowInfo("Demo mode", "Sample data stays in memory. Nothing is connected to iCloud.", InfoBarSeverity.Informational);
    }

    private async void Navigation_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected) { await ShowSettingsAsync(); return; }
        if (args.SelectedItemContainer?.Tag?.ToString() is not string tag) return;
        if (tag == "sync") { if (!_demo) await _sidecar.CallAsync("sync", new { }); ShowInfo("Syncing", "Checking iCloud for changes…", InfoBarSeverity.Informational); return; }
        if (tag == "conflicts") { await ResolveConflictsAsync(); return; }
        var split = tag.Split(':', 2); if (split.Length != 2) return;
        _view = new(args.SelectedItemContainer.Content?.ToString() ?? "Reminders", "", split[0] switch { "list" => NavKind.List, "tag" => NavKind.Tag, _ => NavKind.Smart }, split[1]);
        ViewTitle.Text = _view.Label; ViewSubtitle.Text = _view.Kind == NavKind.Tag ? "Filtered by tag" : ""; ShowDetail(null); await LoadRemindersAsync();
    }

    private void Navigation_PaneOpening(NavigationView sender, object args)
    {
        if (_initializingPane) return;
        if (_temporaryPaneTarget is true) { _temporaryPaneTarget = null; return; }
        _hoverExpanded = false;
        _panePinnedOpen = true;
        UiPreferences.SaveNavigationPaneOpen(true);
    }

    private void Navigation_PaneClosing(NavigationView sender, object args)
    {
        if (_initializingPane) return;
        if (_temporaryPaneTarget is false) { _temporaryPaneTarget = null; _hoverExpanded = false; return; }
        _hoverExpanded = false;
        _panePinnedOpen = false;
        UiPreferences.SaveNavigationPaneOpen(false);
    }

    private void Navigation_PointerMoved(object sender, PointerRoutedEventArgs e)
    {
        var pointerX = e.GetCurrentPoint(Navigation).Position.X;
        if (!_panePinnedOpen && !_hoverExpanded && !Navigation.IsPaneOpen && pointerX <= Navigation.CompactPaneLength + 8)
        {
            _paneHoverCloseTimer.Stop();
            if (!_paneHoverOpenTimer.IsEnabled) _paneHoverOpenTimer.Start();
            return;
        }
        _paneHoverOpenTimer.Stop();
        if (_hoverExpanded && pointerX > Navigation.OpenPaneLength)
        {
            if (!_paneHoverCloseTimer.IsEnabled) _paneHoverCloseTimer.Start();
        }
        else _paneHoverCloseTimer.Stop();
    }

    private void Navigation_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        _paneHoverOpenTimer.Stop();
        if (_hoverExpanded && !_paneHoverCloseTimer.IsEnabled) _paneHoverCloseTimer.Start();
    }

    private void OpenPaneForHover()
    {
        _paneHoverOpenTimer.Stop();
        if (_panePinnedOpen || Navigation.IsPaneOpen) return;
        _hoverExpanded = true;
        _temporaryPaneTarget = true;
        Navigation.IsPaneOpen = true;
    }

    private void CloseHoverPane()
    {
        _paneHoverCloseTimer.Stop();
        if (!_hoverExpanded || _panePinnedOpen) return;
        _temporaryPaneTarget = false;
        Navigation.IsPaneOpen = false;
    }

    private void SearchBox_TextChanged(AutoSuggestBox sender, AutoSuggestBoxTextChangedEventArgs args) { if (args.Reason == AutoSuggestionBoxTextChangeReason.UserInput) { _searchTimer.Stop(); _searchTimer.Start(); } }
    private async void Sort_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not RadioMenuFlyoutItem selected) return;
        _sort = selected.Tag?.ToString() ?? "manual"; await LoadRemindersAsync();
    }
    private async void ShowCompleted_Toggled(object sender, RoutedEventArgs e) { if (Root.IsLoaded) await LoadRemindersAsync(); }
    private async void Complete_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as CheckBox)?.DataContext is not ReminderItem reminder || _demo) return;
        try { await _sidecar.CallAsync("update_reminder", new { id = reminder.Id, completed = reminder.Completed }); await RefreshAllAsync(); }
        catch (Exception error) { reminder.Completed = !reminder.Completed; ShowInfo("Couldn't update reminder", error.Message, InfoBarSeverity.Error); }
    }

    private void ReminderList_SelectionChanged(object sender, SelectionChangedEventArgs e) => ShowDetail(ReminderList.SelectedItem as ReminderItem);
    private void ShowDetail(ReminderItem? reminder)
    {
        _selected = reminder; _loadingDetail = true; NoSelection.Visibility = reminder is null ? Visibility.Visible : Visibility.Collapsed; DetailPane.Visibility = reminder is null ? Visibility.Collapsed : Visibility.Visible;
        if (reminder is not null)
        {
            DetailTitle.Text = reminder.Title; DetailNotes.Text = reminder.Description; DetailList.SelectedValue = reminder.ListId; DetailPriority.SelectedItem = DetailPriority.Items.OfType<ComboBoxItem>().FirstOrDefault(item => item.Tag?.ToString() == reminder.Priority.ToString());
            if (DateTimeOffset.TryParse(reminder.DueDate, out var due)) { DetailDate.Date = due; DetailTime.Time = due.LocalDateTime.TimeOfDay; } else DetailDate.Date = null;
            DetailAllDay.IsOn = reminder.AllDay; DetailTime.IsEnabled = !reminder.AllDay; DetailFlagged.IsOn = reminder.Flagged;
        }
        SaveButton.IsEnabled = false; _loadingDetail = false;
        if (reminder is not null) AnimateDetailPane();
    }
    private void AnimateDetailPane()
    {
        if (!new UISettings().AnimationsEnabled) return;
        var visual = ElementCompositionPreview.GetElementVisual(DetailPane);
        var compositor = visual.Compositor;
        var easing = compositor.CreateCubicBezierEasingFunction(new Vector2(0.16f, 1f), new Vector2(0.3f, 1f));
        var fade = compositor.CreateScalarKeyFrameAnimation();
        fade.InsertKeyFrame(0f, 0f); fade.InsertKeyFrame(1f, 1f, easing); fade.Duration = TimeSpan.FromMilliseconds(180);
        var slide = compositor.CreateVector3KeyFrameAnimation();
        slide.InsertKeyFrame(0f, new Vector3(18f, 0f, 0f)); slide.InsertKeyFrame(1f, Vector3.Zero, easing); slide.Duration = fade.Duration;
        visual.StartAnimation("Opacity", fade); visual.StartAnimation("Offset", slide);
    }
    private void Detail_Changed(object sender, object e) { if (!_loadingDetail && _selected is not null) { SaveButton.IsEnabled = true; DetailTime.IsEnabled = !DetailAllDay.IsOn; } }
    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        if (_selected is null) return;
        if (_demo) { _selected.Title = DetailTitle.Text; _selected.Description = DetailNotes.Text; SaveButton.IsEnabled = false; return; }
        string? due = null; if (DetailDate.Date is DateTimeOffset date) due = DetailAllDay.IsOn ? date.ToString("yyyy-MM-dd") : date.Date.Add(DetailTime.Time).ToString("yyyy-MM-dd'T'HH:mm:ss");
        var priority = long.Parse(((ComboBoxItem?)DetailPriority.SelectedItem)?.Tag?.ToString() ?? "0");
        try { await _sidecar.CallAsync("update_reminder", new { id = _selected.Id, title = DetailTitle.Text.Trim(), description = DetailNotes.Text, due_date = due, all_day = DetailAllDay.IsOn, flagged = DetailFlagged.IsOn, priority }); SaveButton.IsEnabled = false; await LoadRemindersAsync(); }
        catch (Exception error) { ShowInfo("Couldn't save reminder", error.Message, InfoBarSeverity.Error); }
    }
    private async void Delete_Click(object sender, RoutedEventArgs e)
    {
        if (_selected is null) return;
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "Delete reminder?", Content = _selected.DisplayTitle, PrimaryButtonText = "Delete", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Close };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        if (_demo) _reminders.Remove(_selected); else await _sidecar.CallAsync("delete_reminder", new { id = _selected.Id }); ShowDetail(null); await LoadRemindersAsync();
    }

    private async void Add_Click(object sender, RoutedEventArgs e) => await ShowNewReminderAsync();
    private async void NewAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; await ShowNewReminderAsync(); }
    private void SearchAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; SearchBox.Focus(FocusState.Programmatic); }
    private async void SettingsAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; await ShowSettingsAsync(); }
    private async Task ShowNewReminderAsync()
    {
        var title = new TextBox { Header = "Title", PlaceholderText = "What needs doing?" }; var notes = new TextBox { Header = "Notes", AcceptsReturn = true, MinHeight = 80 }; var list = new ComboBox { Header = "List", ItemsSource = _lists.Where(item => !item.IsGroup).ToList(), DisplayMemberPath = "Title", SelectedValuePath = "Id", HorizontalAlignment = HorizontalAlignment.Stretch }; list.SelectedIndex = 0;
        var stack = new StackPanel { Spacing = 12 }; stack.Children.Add(title); stack.Children.Add(notes); stack.Children.Add(list);
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "New reminder", Content = stack, PrimaryButtonText = "Add", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Primary };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(title.Text) || list.SelectedValue is not string listId) return;
        if (_demo) _reminders.Add(new() { Id = Guid.NewGuid().ToString(), ListId = listId, Title = title.Text.Trim(), Description = notes.Text }); else await _sidecar.CallAsync("create_reminder", new { list_id = listId, title = title.Text.Trim(), description = notes.Text }); await LoadRemindersAsync();
    }

    private async Task ShowSettingsAsync()
    {
        var theme = new ComboBox { Header = "App theme", HorizontalAlignment = HorizontalAlignment.Stretch, ItemsSource = new[] { "Match Windows", "Light", "Dark" }, SelectedIndex = 0 }; var sync = new NumberBox { Header = "Sync interval (minutes)", Minimum = 5, Maximum = 60, Value = _settings.Number("sync_minutes") is 0 ? 10 : _settings.Number("sync_minutes"), SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact }; var notifications = new ToggleSwitch { Header = "Due-date notifications", IsOn = !_settings.TryGetProperty("notifications_enabled", out var enabled) || enabled.GetBoolean() };
        var stack = new StackPanel { Spacing = 14, MinWidth = 360 }; stack.Children.Add(theme); stack.Children.Add(sync); stack.Children.Add(notifications);
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "Settings", Content = stack, PrimaryButtonText = "Done", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Primary };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || _demo) return;
        var themeValue = theme.SelectedIndex switch { 1 => "light", 2 => "dark", _ => "system" }; _settings = await _sidecar.CallAsync("set_settings", new { theme = themeValue, sync_minutes = (int)sync.Value, notifications_enabled = notifications.IsOn }); ApplyTheme(themeValue);
    }
    private async Task ResolveConflictsAsync()
    {
        if (_demo) return; var conflicts = await _sidecar.CallAsync<List<ConflictItem>>("conflicts", new { });
        foreach (var conflict in conflicts) { var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "Resolve sync conflict", Content = "Keep the version edited on this PC, or the current iCloud version?", PrimaryButtonText = "Keep this PC", SecondaryButtonText = "Keep iCloud", CloseButtonText = "Later" }; var result = await dialog.ShowAsync(); if (result == ContentDialogResult.None) break; await _sidecar.CallAsync("resolve_conflict", new { id = conflict.Id, keep = result == ContentDialogResult.Primary ? "local" : "remote" }); }
        await RefreshAllAsync();
    }
    private void ApplyTheme(string theme) => Root.RequestedTheme = theme switch { "dark" => ElementTheme.Dark, "light" => ElementTheme.Light, _ => ElementTheme.Default };
    private void ShowInfo(string title, string message, InfoBarSeverity severity) { AppInfoBar.Title = title; AppInfoBar.Message = message; AppInfoBar.Severity = severity; AppInfoBar.IsOpen = true; }

    private void Sidecar_EventReceived(object? sender, SidecarEventArgs e) => DispatcherQueue.TryEnqueue(async () =>
    {
        switch (e.Name)
        {
            case "ready": await BootAsync(); break;
            case "auth_changed": if (e.Data.Flag("authenticated")) await BootAsync(); else ShowLogin(); break;
            case "sync_started": ShowInfo("Syncing", "Checking iCloud for changes…", InfoBarSeverity.Informational); break;
            case "sync_finished": AppInfoBar.IsOpen = false; await RefreshAllAsync(); break;
            case "sync_error": ShowInfo("Sync failed", e.Data.Text("message", "Try again in a moment."), InfoBarSeverity.Error); break;
            case "conflict": await RefreshStatusAsync(); break;
        }
    });
    private async void MainWindow_Closed(object sender, WindowEventArgs args)
    {
        _statusTimer.Stop(); _notificationTimer.Stop(); _syncTimer.Stop(); _paneHoverOpenTimer.Stop(); _paneHoverCloseTimer.Stop();
        if (_notificationsRegistered) { try { AppNotificationManager.Default.Unregister(); } catch { } }
        await _sidecar.DisposeAsync();
    }
}
