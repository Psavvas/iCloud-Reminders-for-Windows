using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;

namespace Reminders.Windows.Services;

public sealed class SidecarClient : IAsyncDisposable
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true, PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private Process? _process;
    private StreamWriter? _input;
    private long _nextId;
    private bool _stopping;
    public event EventHandler<SidecarEventArgs>? EventReceived;
    public event EventHandler<string>? Stopped;
    public bool IsRunning => _process is { HasExited: false };

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        if (IsRunning) return;
        _stopping = false;
        var executable = FindExecutable();
        var dataDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RemindersSync");
#if DEBUG
        var developmentData = Environment.GetEnvironmentVariable("REMINDERS_DATA_DIR");
        if (!string.IsNullOrWhiteSpace(developmentData)) dataDirectory = Path.GetFullPath(developmentData);
#endif
        Directory.CreateDirectory(dataDirectory);
        var start = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = Path.GetDirectoryName(executable)!,
        };
        start.ArgumentList.Add("--data-dir");
        start.ArgumentList.Add(dataDirectory);

        _process = new Process { StartInfo = start, EnableRaisingEvents = true };
        _process.Exited += (_, _) => HandleStopped($"The sync service exited with code {_process?.ExitCode}.");
        if (!_process.Start()) throw new InvalidOperationException("Windows could not start the sync service.");

        _input = _process.StandardInput;
        _input.AutoFlush = true;
        _ = ReadOutputAsync(_process.StandardOutput, cancellationToken);
        _ = DrainErrorsAsync(_process.StandardError, cancellationToken);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        await CallAsync("ping", new { }, timeout.Token);
    }

    public async Task<T> CallAsync<T>(string method, object? parameters = null, CancellationToken cancellationToken = default) =>
        (await CallAsync(method, parameters, cancellationToken)).Deserialize<T>(Json) ?? throw new InvalidDataException($"The {method} response was empty.");

    public async Task<JsonElement> CallAsync(string method, object? parameters = null, CancellationToken cancellationToken = default)
    {
        if (!IsRunning || _input is null) throw new SidecarException("SIDECAR_DOWN", "The sync service is not running.");
        var id = Interlocked.Increment(ref _nextId);
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = completion;

        var line = JsonSerializer.Serialize(new { id, method, @params = parameters ?? new { } }, Json);
        await _writeLock.WaitAsync(cancellationToken);
        try { await _input.WriteLineAsync(line.AsMemory(), cancellationToken); }
        catch { _pending.TryRemove(id, out _); throw; }
        finally { _writeLock.Release(); }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(90));
        using var registration = timeout.Token.Register(() => completion.TrySetCanceled(timeout.Token));
        try { return await completion.Task; } finally { _pending.TryRemove(id, out _); }
    }

    private async Task ReadOutputAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        try
        {
            while (await reader.ReadLineAsync(cancellationToken) is { } line)
            {
                if (line.Length == 0) continue;
                if (line.Length > 1_048_576)
                {
                    AppLog.Error("Discarding an oversized sync service message", new InvalidDataException($"{line.Length} characters"));
                    continue;
                }
                // A single unparseable or unexpected line must fail at most one
                // call. Letting it escape to the outer catch would tear down
                // the whole client while the sidecar is still running fine.
                try
                {
                    DispatchLine(line);
                }
                catch (Exception ex)
                {
                    AppLog.Error("Discarding a malformed sync service message", ex);
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            AppLog.Error("Sync service output failed", ex);
            HandleStopped(ex.Message);
        }
    }

    private void DispatchLine(string line)
    {
        using var document = JsonDocument.Parse(line);
        var root = document.RootElement;

        if (root.TryGetProperty("event", out var eventName))
        {
            var data = root.TryGetProperty("data", out var node) ? node.Clone() : default;
            EventReceived?.Invoke(this, new(eventName.GetString() ?? "", data));
            return;
        }

        if (!root.TryGetProperty("id", out var idNode) || !idNode.TryGetInt64(out var id)) return;
        if (!_pending.TryRemove(id, out var completion)) return;

        if (root.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True)
        {
            // A malformed success frame fails this one call rather than
            // throwing past the reader and stopping the client.
            if (root.TryGetProperty("result", out var result)) completion.TrySetResult(result.Clone());
            else completion.TrySetException(new SidecarException("BAD_RESPONSE", "The sync service returned a response with no result."));
            return;
        }

        var error = root.TryGetProperty("error", out var node2) ? node2 : default;
        completion.TrySetException(new SidecarException(
            error.Text("code", "ERROR"),
            error.Text("message", "The sync service returned an error."),
            error.Text("detail", "")));
    }

    private static async Task DrainErrorsAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        try
        {
            while (await reader.ReadLineAsync(cancellationToken) is { } line)
            {
                Debug.WriteLine("[sidecar] " + line);
                AppLog.Info("sidecar: " + line);
            }
        }
        catch (OperationCanceledException) { }
    }

    private void HandleStopped(string reason)
    {
        if (_stopping) return;
        foreach (var call in _pending.Values)
        {
            call.TrySetException(new SidecarException("SIDECAR_DOWN", "The sync service stopped.", reason));
        }
        _pending.Clear();
        Stopped?.Invoke(this, reason);
    }

    private static string FindExecutable()
    {
#if DEBUG
        var overridden = Environment.GetEnvironmentVariable("REMINDERS_SIDECAR");
        if (!string.IsNullOrWhiteSpace(overridden) && File.Exists(overridden)) return Path.GetFullPath(overridden);
#endif
        var candidates = new List<string> { Path.Combine(AppContext.BaseDirectory, "reminders-sidecar.exe") };
#if DEBUG
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 7 && current is not null; i++, current = current.Parent)
        {
            candidates.Add(Path.Combine(current.FullName, "dist-sidecar", "reminders-sidecar.exe"));
        }
#endif
        return candidates.FirstOrDefault(File.Exists) ?? throw new FileNotFoundException("Build the Rust sync service with scripts\\build-sidecar.ps1 first.");
    }
    public async ValueTask DisposeAsync()
    {
        _stopping = true;
        if (IsRunning)
        {
            // Bound the graceful shutdown. Without a token this inherits the
            // 90s call timeout, so a wedged sidecar would hang window close
            // for a minute and a half before we ever reach Kill.
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
            try { await CallAsync("shutdown", new { }, timeout.Token); } catch { }
            if (_process is { HasExited: false }) _process.Kill(true);
        }
        _process?.Dispose();
        _writeLock.Dispose();
    }
}

public sealed record SidecarEventArgs(string Name, JsonElement Data);
internal static class JsonHelpers
{
    public static string Text(this JsonElement value, string name, string fallback = "") => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var node) && node.ValueKind == JsonValueKind.String ? node.GetString() ?? fallback : fallback;
    public static bool Flag(this JsonElement value, string name) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var node) && node.ValueKind == JsonValueKind.True;
    public static long Number(this JsonElement value, string name) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var node) && node.TryGetInt64(out var number) ? number : 0;
}
