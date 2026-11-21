using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// One request, one response, over the local named pipe <c>\\.\pipe\JarvisGuard</c>.
/// The SYSTEM agent listens; the tray and the lock surface ask. Each is one
/// line of JSON ending in "\n" (<see cref="Request"/> in, <see cref="Response"/>
/// out), then the connection closes.
///
/// Both ends read with a cap (<see cref="MaxRequestBytes"/>,
/// <see cref="MaxResponseBytes"/>) and a time limit, so a client that sends
/// nothing, or never stops sending, cannot hold the agent. The client talks
/// only to a pipe owned by SYSTEM (<see cref="PipeTrust"/>): a standard user
/// who creates a pipe of the same name first cannot make it look like the
/// agent's, so they cannot collect what the lock surface sends.
///
/// Adding a command: a new <see cref="Request.Cmd"/> value (and any fields it
/// needs, all optional), a case in the agent's dispatcher
/// (GuardService.HandlePipeRequestAsync), and an answer in
/// <see cref="Response"/>. Unknown commands are answered with an error, never
/// dropped, so an older agent tells a newer lock surface plainly.
/// </summary>
public static class PipeProtocol
{
    /// <summary>The longest request line the agent reads (the usage report is the largest).</summary>
    public const int MaxRequestBytes = 64 * 1024;
    /// <summary>The longest answer the tray or the lock surface reads (today's chores is the largest).</summary>
    public const int MaxResponseBytes = 512 * 1024;
    /// <summary>How long the agent waits for a connected client's request line.</summary>
    public static readonly TimeSpan RequestReadTimeout = TimeSpan.FromSeconds(10);

    public sealed class Request
    {
        public string Cmd { get; set; } = "";          // "status" | "unlock-request" | "unlock-status" | "offline-unlock" | "ask-time" | "message" | "chores" | "chore-done" | "usage"
        public string? HubUrl { get; set; }
        public int Minutes { get; set; }               // for cmd=ask-time and cmd=unlock-request
        public string? Note { get; set; }              // for cmd=ask-time and cmd=unlock-request: why, in the child's words
        public Dictionary<string, long>? Apps { get; set; } // for cmd=usage: foreground ms per exe since the last report (empty: the tray's heartbeat)
        public string? Text { get; set; }              // for cmd=message: the child's message to their parents
        public string? ChoreId { get; set; }           // for cmd=chore-done
        public string? Code { get; set; }              // for cmd=offline-unlock: a parent's offline code
    }

    public sealed class Response
    {
        public bool Ok { get; set; }
        public string? Error { get; set; }
        public GuardState? State { get; set; }          // for cmd=status
        public int UntilMinutes { get; set; }           // for cmd=unlock-status (approved) / offline-unlock
        public string? UnlockStatus { get; set; }       // for cmd=unlock-request / unlock-status: an UnlockRequest status (None, Pending, Approved, ...)
        public List<ChoreItem>? Chores { get; set; }    // for cmd=chores
        public string? Info { get; set; }               // a line to show on success (who a message reached)
    }

    public static string EncodeRequest(Request r) => JsonSerializer.Serialize(r, JsonOptions.Web);
    public static string EncodeResponse(Response r) => JsonSerializer.Serialize(r, JsonOptions.Web);
    public static Request? DecodeRequest(string line) { try { return JsonSerializer.Deserialize<Request>(line, JsonOptions.Web); } catch (JsonException) { return null; } }
    public static Response? DecodeResponse(string line) { try { return JsonSerializer.Deserialize<Response>(line, JsonOptions.Web); } catch (JsonException) { return null; } }

    /// <summary>A line longer than the reader allows.</summary>
    public sealed class LineTooLongException(int limit) : IOException($"The line is longer than {limit} bytes.");

    /// <summary>
    /// One "\n"-terminated UTF-8 line from <paramref name="stream"/>, at most
    /// <paramref name="maxBytes"/> long, within <paramref name="timeout"/>.
    /// Null when the other end closed before sending anything. Throws
    /// <see cref="LineTooLongException"/> past the cap and
    /// <see cref="TimeoutException"/> when the time runs out. Reads past the
    /// newline are dropped: there is one line per connection.
    /// </summary>
    public static async Task<string?> ReadLineAsync(Stream stream, int maxBytes, TimeSpan timeout, CancellationToken ct = default)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout);
        var line = new MemoryStream();
        var buffer = new byte[4096];
        try
        {
            while (true)
            {
                var n = await stream.ReadAsync(buffer, cts.Token);
                if (n == 0) return line.Length == 0 ? null : Encoding.UTF8.GetString(line.ToArray());
                var newline = Array.IndexOf(buffer, (byte)'\n', 0, n);
                var take = newline >= 0 ? newline : n;
                if (line.Length + take > maxBytes) throw new LineTooLongException(maxBytes);
                line.Write(buffer, 0, take);
                if (newline >= 0) return Encoding.UTF8.GetString(line.ToArray()).TrimEnd('\r');
            }
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new TimeoutException($"No complete line within {timeout.TotalSeconds:0} s.");
        }
    }

    /// <summary>
    /// Send one request to the agent. Never null: when it does not work the
    /// answer is not ok and says why (the service is not running, it did not
    /// answer in time, or the pipe is not the agent's).
    /// </summary>
    public static async Task<Response?> SendAsync(string pipeName, Request request, int timeoutMs = 8000)
    {
        NamedPipeClientStream? pipe = null;
        try
        {
            // Read and write data only. Opening with GENERIC_WRITE (the
            // PipeDirection constructors) would also ask for
            // FILE_CREATE_PIPE_INSTANCE, which the agent's ACL deliberately
            // gives no user; Identification lets the agent see who is asking
            // but never act as them.
            pipe = new NamedPipeClientStream(".", pipeName, PipeAccessRights.ReadWrite, PipeOptions.Asynchronous,
                TokenImpersonationLevel.Identification, HandleInheritability.None);
            using var cts = new CancellationTokenSource(timeoutMs);
            await pipe.ConnectAsync(timeoutMs, cts.Token);
            var untrusted = PipeTrust.CheckServer(pipe);
            if (untrusted is not null) return Fail(untrusted);
            var payload = Encoding.UTF8.GetBytes(EncodeRequest(request) + "\n");
            await pipe.WriteAsync(payload, cts.Token);
            await pipe.FlushAsync(cts.Token);
            var line = await ReadLineAsync(pipe, MaxResponseBytes, TimeSpan.FromMilliseconds(timeoutMs), cts.Token);
            if (line is null) return Fail("The Jarvis Guard service closed the connection without an answer.");
            return DecodeResponse(line) ?? Fail("The Jarvis Guard service sent an answer this program cannot read. Is it a different version?");
        }
        catch (TimeoutException) { return Fail("The Jarvis Guard service did not answer in time. Try again in a moment."); }
        catch (OperationCanceledException) { return Fail("The Jarvis Guard service is not answering. It may not be running."); }
        catch (LineTooLongException) { return Fail("The Jarvis Guard service sent an answer that is too long."); }
        catch (UnauthorizedAccessException) { return Fail("This account may not talk to the Jarvis Guard service."); }
        catch (IOException ex) { return Fail($"Could not talk to the Jarvis Guard service: {ex.Message}"); }
        finally
        {
            if (pipe is not null) await pipe.DisposeAsync();
        }
    }

    private static Response Fail(string error) => new() { Ok = false, Error = error };
}
