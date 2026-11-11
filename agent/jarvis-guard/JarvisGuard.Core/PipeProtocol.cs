using System.Text;
using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// One request, one response, over a local named pipe. The agent listens;
/// the tray and the lock surface ask. The pipe never carries the child's
/// API key - parent credentials travel one way and are answered with the hub's
/// verdict.
/// </summary>
public static class PipeProtocol
{
    public sealed class Request
    {
        public string Cmd { get; set; } = "";          // "status" | "parent-unlock"
        public string? HubUrl { get; set; }
        public string? ParentUsername { get; set; }
        public string? ParentPassword { get; set; }
    }

    public sealed class Response
    {
        public bool Ok { get; set; }
        public string? Error { get; set; }
        public GuardState? State { get; set; }          // for cmd=status
        public int UntilMinutes { get; set; }           // for cmd=parent-unlock
    }

    public static string EncodeRequest(Request r) => JsonSerializer.Serialize(r, JsonOptions.Web);
    public static string EncodeResponse(Response r) => JsonSerializer.Serialize(r, JsonOptions.Web);
    public static Request? DecodeRequest(string line) { try { return JsonSerializer.Deserialize<Request>(line, JsonOptions.Web); } catch { return null; } }
    public static Response? DecodeResponse(string line) { try { return JsonSerializer.Deserialize<Response>(line, JsonOptions.Web); } catch { return null; } }

    public static async Task<Response?> SendAsync(string pipeName, Request request, int timeoutMs = 8000)
    {
        try
        {
            await using var pipe = new System.IO.Pipes.NamedPipeClientStream(
                ".", pipeName, System.IO.Pipes.PipeDirection.InOut, System.IO.Pipes.PipeOptions.None);
            using var cts = new CancellationTokenSource(timeoutMs);
            await pipe.ConnectAsync(timeoutMs, cts.Token);
            var payload = Encoding.UTF8.GetBytes(EncodeRequest(request) + "\n");
            await pipe.WriteAsync(payload, cts.Token);
            await pipe.FlushAsync(cts.Token);
            using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);
            var line = await reader.ReadLineAsync(cts.Token);
            return DecodeResponse(line ?? "");
        }
        catch { return null; }
    }
}
