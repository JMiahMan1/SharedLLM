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

    public Task<HttpResponseMessage> ReportCapabilitiesAsync(bool lockCapable, bool trayRunning, string webFilterMode) =>
        _http.PostAsJsonAsync($"api/user-panel/devices/{Uri.EscapeDataString(_config.DeviceKey)}/capabilities", new
        {
            capabilities = new
            {
                serviceRunning = true,
                lockCapable,
                trayRunning,
                webFilterMode, // "off" | "report" | "enforce"
                version = _config.Version,
                reportedAt = DateTime.UtcNow.ToString("O"),
            },
        });

    public Task<HttpResponseMessage> PostUsageAsync(object payload) =>
        _http.PostAsJsonAsync("api/usage", payload);

    public Task<HttpResponseMessage> PostTamperAsync(string eventName, string detail) =>
        _http.PostAsJsonAsync("api/guard/tamper", new { @event = eventName, device_key = _config.DeviceKey, detail });

    /// <summary>A parent's own login, typed on the child's PC, lifts the guard for ten minutes.</summary>
    public Task<HttpResponseMessage> ParentUnlockAsync(string parentUsername, string parentPassword) =>
        _http.PostAsJsonAsync("api/guard/parent-unlock", new
        {
            username = parentUsername,
            password = parentPassword,
            device = "windows-pc",
            device_key = _config.DeviceKey,
        });

    /// <summary>"Ask for more time": a request a parent answers from their phone (Grant / Not today).</summary>
    public Task<HttpResponseMessage> AskMoreTimeAsync(int minutes, string? note) =>
        _http.PostAsJsonAsync("api/screen-time/requests", new
        {
            minutes,
            note = string.IsNullOrWhiteSpace(note) ? null : note.Trim(),
            day = DateTime.Now.ToString("yyyy-MM-dd"),
        });

    public void Dispose() => _http.Dispose();
}
