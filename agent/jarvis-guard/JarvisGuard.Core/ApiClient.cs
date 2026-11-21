using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// Thin client for the hub. The caller is this PC's own scoped agent key,
/// never a personal account key and never an admin key.
/// </summary>
public sealed class ApiClient : IDisposable
{
    private readonly HttpClient _http;
    private readonly GuardConfig _config;

    public ApiClient(GuardConfig config)
    {
        _config = config;
        _http = new HttpClient { BaseAddress = new Uri(config.HubUrl.TrimEnd('/') + "/"), Timeout = TimeSpan.FromSeconds(10) };
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", config.AgentKey);
    }

    public Task<HttpResponseMessage> GetStatusAsync() =>
        _http.GetAsync($"api/screen-time/{Uri.EscapeDataString(_config.Username)}/status?device_key={Uri.EscapeDataString(_config.DeviceKey)}");

    /// <summary><paramref name="trayRunning"/>: null while it is not known yet (just started), never a guess.</summary>
    public Task<HttpResponseMessage> ReportCapabilitiesAsync(bool lockCapable, bool? trayRunning, string webFilterMode) =>
        _http.PostAsJsonAsync($"api/user-panel/devices/{Uri.EscapeDataString(_config.DeviceKey)}/capabilities", new
        {
            capabilities = new
            {
                serviceRunning = true,
                lockCapable,
                trayRunning,
                webFilterMode, // "off" | "report" | "enforce"
                version = typeof(ApiClient).Assembly.GetName().Version?.ToString(3) ?? _config.Version,
                reportedAt = DateTime.UtcNow.ToString("O"),
            },
        });

    public Task<HttpResponseMessage> PostUsageAsync(object payload) =>
        _http.PostAsJsonAsync("api/usage", payload);

    public Task<HttpResponseMessage> PostTamperAsync(string eventName, string detail) =>
        _http.PostAsJsonAsync("api/guard/tamper", new { @event = eventName, device_key = _config.DeviceKey, detail });

    /// <summary>
    /// Ask both parents to unlock this PC for <paramref name="minutes"/> (they
    /// answer on their phones). The hub binds the request to this PC's device
    /// key; no password is ever sent from the PC.
    /// </summary>
    public Task<HttpResponseMessage> AskUnlockAsync(int minutes, string? note, string? lockReason) =>
        _http.PostAsJsonAsync("api/guard/unlock-requests", new
        {
            minutes,
            note = string.IsNullOrWhiteSpace(note) ? null : note.Trim(),
            lock_reason = lockReason ?? "",
        });

    /// <summary>This PC's own unlock request: still waiting, or a parent's answer (the hub answers only this PC).</summary>
    public Task<HttpResponseMessage> GetUnlockRequestAsync(long id) =>
        _http.GetAsync($"api/guard/unlock-requests/{id}");

    /// <summary>"Ask for more time": a request a parent answers from their phone (Grant / Not today).</summary>
    public Task<HttpResponseMessage> AskMoreTimeAsync(int minutes, string? note) =>
        _http.PostAsJsonAsync("api/screen-time/requests", new
        {
            minutes,
            note = string.IsNullOrWhiteSpace(note) ? null : note.Trim(),
            day = DateTime.Now.ToString("yyyy-MM-dd"),
        });

    /// <summary>The lock surface's "Message a parent": a chat message to each of the child's parents.</summary>
    public Task<HttpResponseMessage> MessageParentsAsync(string text) =>
        _http.PostAsJsonAsync("api/guard/message", new { text });

    /// <summary>Today's chores for the child this PC belongs to (the hub decides whose).</summary>
    public Task<HttpResponseMessage> GetChoresAsync(string day) =>
        _http.GetAsync($"api/integrations/skylight/chores?date={Uri.EscapeDataString(day)}");

    /// <summary>Tick a chore off; the hub asks a parent whether it was done right.</summary>
    public Task<HttpResponseMessage> CompleteChoreAsync(string choreId) =>
        _http.PostAsync($"api/integrations/skylight/chores/{Uri.EscapeDataString(choreId)}/complete", null);

    public void Dispose() => _http.Dispose();
}
