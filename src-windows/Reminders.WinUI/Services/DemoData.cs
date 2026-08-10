namespace Reminders.Windows.Services;

internal static class DemoData
{
    public static List<ReminderList> CreateLists() =>
    [
        new() { Id = "inbox", Title = "Inbox", Count = 3 },
        new() { Id = "work", Title = "Work", Count = 2 },
        new() { Id = "personal", Title = "Personal", Count = 1 }
    ];

    public static List<ReminderItem> CreateReminders() =>
    [
        new() { Id = "1", ListId = "inbox", Title = "Review the project plan", Description = "Add notes before tomorrow's check-in", DueDate = DateTimeOffset.Now.AddHours(2).ToString("O"), Priority = 1 },
        new() { Id = "2", ListId = "personal", Title = "Pick up groceries", DueDate = DateTimeOffset.Now.AddDays(1).ToString("O"), Tags = ["errands"] },
        new() { Id = "3", ListId = "work", Title = "Send the updated mockups", Dirty = 1 }
    ];
}
