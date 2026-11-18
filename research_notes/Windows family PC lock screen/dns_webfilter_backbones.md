# DNS/web-category filtering backbones for a Windows 10/11 family PC (children's account)

Scope: open-source DNS filtering deployable by a parent on a Windows 10/11 PC, to power a ~200-domain category blocklist (social / gaming / streaming / chat / gambling / VPN) for a non-admin child account, starting in report-only mode. Domain-granularity only, no TLS interception. Researched Nov 2026; distros/repos verified active as of this date.

Structure below follows the researcher template: one `##` section per key question, with Takeaway / Cited Findings / Inferences / Gaps.

---

## Technitium DNS Server on Windows

### Takeaway
Best all-rounder for this use case if you want a full recursive/authoritative server with a polished web console, per-client blocking, CNAME-cloaking-aware sinkholing, built-in DoT/DoH/DoQ self-hosting, query logging, and a Windows installer + service. It is free (GPL-3.0), cross-platform, and actively maintained. It is heavier than Acrylic but far more capable than a hosts-file proxy.

### Cited Findings
- Repo: [TechnitiumSoftware/DnsServer](https://github.com/TechnitiumSoftware/DnsServer) — GPL-3.0, ~10.3k stars, ~823 forks, .NET 10 implementation, actively developed (v15 released Apr 2026; v14 Nov 2025; ~4,300 commits). Windows install via `DnsServerSetup.zip` (Windows service, auto-start); a portable build exists too.
- Feature set (from README): blocks ads/malware via one or more blocklist URLs, "downloaded every 24 hours" ([blog: Blocking Internet Ads Using DNS Sinkhole](https://blog.technitium.com/2018/10/blocking-internet-ads-using-dns-sinkhole.html)); sinkholes to 0.0.0.0/:: (configurable NXDOMAIN since v6.2); Allowed Zones whitelist override; Blocked Zones; split-horizon/geo apps; built-in DHCP; clustering; SSO/LDAP; DNSSEC; QNAME minimization; serve-stale/prefetch; persistent cache.
- Encrypted DNS: can *consume* Cloudflare/Google/Quad9/AdGuard over DoT/DoH/DoQ as forwarders, *and* can *host* DoT/DoH/DoQ on your LAN (DoH supports HTTP/1.1, HTTP/2, HTTP/3). No need to expose anything externally for a single-PC deployment.
- Per-client blocking: "REGEX based block lists with different block lists for different client IP addresses or subnet using Advanced Blocking DNS App" (README). Caveats from the author: no per-app visibility ("for most apps, its the OS that does DNS resolution"; requests arrive over the network so the DNS server cannot see the process).
- Blocklist formats: officially documented as *blocklist URLs* downloaded on a 24h cycle; Quick-Add drop-down ships popular lists and a Default set. HaGeZi explicitly lists TechnitiumDNS as a consumer of its **Wildcard Domains (onlydomains)** format (`.../wildcard/*-onlydomains.txt`), and oisd.nl lists Technitium as a supported client. I did **not** verify first-hand that Technitium parses plain hosts-format files with `0.0.0.0` prefixes vs. requires bare-domain lists; treat hosts-format compatibility as likely-but-unverified, prefer HaGeZi onlydomains or oisd.
- Logging/stats: built-in system logging and query logging (enable in Settings → Logging); Query Logs visible via the **Query Logs (Sqlite)** app, queryable with filters, "not real time though since you will need to click to get results"; timestamps are UTC ([blog comments, Oct 2024](https://blog.technitium.com/2018/10/blocking-internet-ads-using-dns-sinkhole.html)). Dashboard shows stats widgets.
- Performance: "load tested on Intel i7-8700 CPU with more than 100,000 request/second" (README) — vastly more than a family PC needs.
- Windows-specific: setup installs a Windows service (`DnsServerWindowsService`); loopback use requires adapter DNS = 127.0.0.1 (IPv4) and ::1 (IPv6). Forum posts note Windows needs TCP/UDP port 53 allowed inbound (later releases handle this automatically).
- Can't do: per-app filtering, content-aware blocking (YouTube ads, sponsored posts), HTTPS inspection; sinkhole-only. Also cannot block DoH traffic *by itself* — you must pair it with firewall/browser policy (see DoH section).

### Inferences
- For one child PC, installing Technitium on that PC with adapter DNS pinned to 127.0.0.1 is the most self-contained option: service + auto-start + web console + logs, survives reboots without router changes.
- Per-client blocking is useful if the same service also serves the parent's devices — you can scope category lists to the child's IP/subnet or MAC-leased DHCP host.

### Gaps
- Exact blocklist file-format grammar Technitium accepts (hosts with IP prefix? bare domains? adblock `||domain^`?) was not directly verified in official docs; the help system (go.technitium.com redirect) could not be fetched as static pages. AdGuard-rule-format lists are probably **not** parsed by the core blocklist feature (rule evaluation lives in the Advanced Blocking app). Verify against the app before pasting HaGeZi `adblock/*.txt` URLs — use the `wildcard/*-onlydomains.txt` variants instead.
- Precise RAM/CPU footprint on Windows (docs give throughput, not idle RAM).
- Whether Technitium has a true "dry-run / log-only" blocklisting mode: not documented; report-only must be emulated (see Report-only section).

---

## AdGuard Home on Windows

### Takeaway
The best-supported, actively maintained (38k+ stars, weekly-ish releases), cross-platform open-source option with a first-class web UI, per-client rules, mature query log with retention controls and anonymization, and the richest ecosystem of ready-made blocklists. It is the default recommendation. RAM use is higher than a raw hosts hack but modest (tens–low-hundreds of MB; ≥1 GB only if you load the 1M-entry TIF list).

### Cited Findings
- Repo: [AdguardTeam/AdGuardHome](https://github.com/AdguardTeam/AdGuardHome) — GPL-3.0, ~38.3k stars, ~2.6k forks, Go, ~4,600 commits; comparison table vs Pi-hole claims built-in DHCP, HTTPS admin UI, encrypted upstreams (DoT/DoH/DNSCrypt), runs **as a DoT/DoH server**, parental-control lists, per-client config, and cross-platform support. Chocolatey package exists (`community.chocolatey.org/packages/adguardhome`). Windows runs it natively (no Docker/WSL needed).
- Blocklists ("DNS blocklists" in Settings → Filters): import via URL or file. Both hosts-style lists and **AdGuard rule syntax** are consumed — HaGeZi's "Adblock" format is explicitly for "Pi-hole, AdGuard, AdGuard Home"; oisd publishes plain lists consumed by AdGuard Home directly (`https://big.oisd.nl`, `https://small.oisd.nl`); AdGuard Home ships its own default "AdGuard DNS filter" and "OISD small"-class defaults selectable in UI (oisd setup page lists AdGuard Home as a target).
- "AdGuard SDNS": SDNS (`sdns://`) is the format for **upstream/encrypted DNS resolvers**, and AdGuard Home uses it when you add an AdGuard DNS upstream — it is *not* a blocklist format. Blocklists are hosts/AdGuard-rule lists. (Distinction not always documented clearly upstream.)
- Per-client: README comparison lists "Per-client (device) configuration ✅" and "Access settings (choose who can use AGH DNS) ✅"; per-client `ignore_querylog` / `ignore_statistics`, per-client blocked-services (with schedules), and client lists by IP/MAC/hostname are supported in config ([Configuration wiki](https://github.com/AdguardTeam/AdGuardHome/wiki/Configuration)). Per-client **rule** overrides (different blocklists per client) are not as granular as Technitium's Advanced Blocking regex-per-subnet; client-specific settings focus on safesearch/blocked-services/log ignoring + custom upstreams.
- Encrypted upstreams: DoH/DoT/DoQ/DNSCrypt (unlike Pi-hole core, which needs extra software) — direct from README comparison.
- Resource use: Go single binary; idle footprint small, but HaGeZi warns: TIF full (1,045,391 entries) needs **≥2 GB RAM**, TIF medium (366,508) **≥1 GB**; TIF mini 187k otherwise; PRO (223,961) fine for normal desktops. `ratelimit` defaults to 20 qps (safe to raise/disable for LAN use) ([HaGeZi README](https://github.com/hagezi/dns-blocklists); [Configuration](https://adguard-dns.io/kb/adguard-home/configuration/)).
- Query log & retention: since v0.107.24 a dedicated `querylog:` section in `AdGuardHome.yaml`: `enabled`, `file_enabled`, `interval` (6h/24h/7d/30d/90d via API enum 0.25/1/7/30/90), `size_memory`, `ignored` (AdBlock rule syntax, e.g. `*.arpa`), `dir_path`; `anonymize_client_ip` in `dns:`/`querylog` scopes; per-client `ignore_querylog`/`ignore_statistics`. Effective retention ≈ **2× the rotation interval** (rotated `querylog.json.1` is still read — confirmed by maintainer in [issue #2610](https://github.com/AdguardTeam/AdGuardHome/issues/2610) and [issue #7893](https://github.com/AdguardTeam/AdGuardHome/issues/7893)). OpenWrt wiki sizing example: ~53 MB per 7 days of query logs, ~2 MB per 90 days of stats.
- Logging granularity: domain (+client IP, rule matched, action). No URLs/content — satisfies "domain-only" requirement. Privacy knobs: anonymize client IPs, ignore lists, per-client exclusion.
- Maintenance status: very active; v0.107.x line; beta/edge channels; security policy published. "We've stopped using CoreDNS" (uses AdGuardTeam/dnsproxy + urlfilter) — relevant for the CoreDNS comparison.
- Report-only nuance: AdGuard Home has no first-class "dry-run blocklist" mode; you emulate report-only by (a) running with filtering disabled but query log enabled, then evaluating logged domains against the 200-domain list offline (hagezi offers an online Blocklist Lookup), or (b) blocking only after soak period. See Report-only section.

### Inferences
- For a parent who wants minimal fuss: install AdGuard Home as a Windows service on the child PC, set adapter DNS (or loopback) to it, import oisd small or HaGeZi PRO, review query log weekly for two weeks, then enable category lists (StevenBlack social+gambling alternates or a custom ~200-domain list).
- HKLM admin policies for Chrome/Edge + loopback DNS + AdGuard Home on the same machine fully cover a non-admin child without touching the router.

### Gaps
- Exact idle RAM/CPU numbers for AdGuard Home on Windows were not found in official docs (only list-size → RAM guidance from HaGeZi and ratelimit defaults). Treat "tens of MB idle" as estimate, not sourced fact.
- SDNS-as-blocklist: no official doc confirms `sdns://` blocklists; do not rely on it — use plain list URLs.
- Whether AdGuard Home can pin a **specific** blocklist to a specific client (rather than per-client safesearch/blocked-services/log-ignore) is not documented; assume client granularity ≠ list granularity.

---

## CoreDNS with hosts/geoip/etc plugins vs AdGuard/Technitium

### Takeaway
CoreDNS is a composable DNS server/library (CNCF graduated, Go). The in-tree `hosts` plugin serves a hosts-file zone; `log`, `errors`, `cache`, `forward`, `rewrite`, `template`, `rewrite`, `reload`, `health`, `prometheus` are in-tree. There is **no in-tree geoip plugin** — geoip lookups exist only via third-party/external plugins, and an RPZ-capable path exists via external plugins too. CoreDNS has no web UI, no blocklist manager, no per-client GUI, no query-log viewer, no child-account story. Choose it only when you need DNS plumbing you fully control (split-horizon, templated answers, integration with other tooling), not when you need a curated family filter.

### Cited Findings
- Repo: [coredns/coredns](https://github.com/coredns/coredns) (CNCF graduated project; Go). AdGuard Home's own README notes "we've stopped using CoreDNS in AdGuard Home" in favor of AdGuardTeam/dnsproxy — i.e., AGH moved *away* from CoreDNS.
- HaGeZi ships RPZ- and wildcard-asterisk-format outputs explicitly for "Bind, Knot, PowerDNS, Unbound" and "Blocky (v0.23+), … OPNsense" respectively; CoreDNS is not called out as a first-class consumer of any packaged category list — you would convert lists to `hosts` format yourself.
- Technitium's Advanced Blocking app and AdGuard Home's filter engine implement regex/per-client/CNAME-cloaking semantics that CoreDNS does not provide out of the box (e.g., CNAME cloaking: block domain resolving to a blocked CNAME — Technitium feature, README).

### Inferences
- On a Windows self-hosted family PC, CoreDNS is the *wrong* backbone for this job: you would re-implement list parsing, sinkholing semantics, logging UI, and updates by hand. It wins when: (1) you already run it for k8s/homelab plumbing and want to add a `hosts` zone for ~200 domains; (2) you want deterministic, version-controlled config (Corefile) with no GUI attack surface; (3) you need split-horizon or templated CNAME/ANAME rewrites beyond what AGH/Technitium expose.
- If the parent is comfortable scripting, a defensible minimal stack is: CoreDNS + `hosts` (entries `0.0.0.0 blocked.example`) + `forward` to a DoH upstream + `log` to file + NSSM/`sc.exe` to run it as a Windows service. But you lose: one-click list updates, per-client rules, dashboards, and the oisd/HaGeZi ecosystem (they target hosts/adblock/dnsmasq/RPZ, not CoreDNS natively).

### Gaps
- Current external geoip plugin name/health (historically `coredns/geoip`) was not verified; in-tree plugin inventory should be checked against the Corefile plugin index before building on it.
- No Windows-specific performance numbers found; CoreDNS on Windows is known to work (Go) but lacks official service packaging (use NSSM or `sc.exe create`).

---

## Acrylic DNS Proxy on Windows (loopback for the child PC only)

### Takeaway
Viable, tiny, and actually maintained (v2.2.1, Feb 2025), but it is a single-author hobby project (Borland Delphi, GPL) with no per-client logic, no category awareness, no query UI, and blocklists must be hand-converted to its hosts dialect. Good as a 127.0.0.1 caching forwarder with a hand-maintained `AcrylicHosts.txt`; not great as the primary engine for a 200-domain evolving family list unless simplicity outweighs features.

### Cited Findings
- Site: [mayakron.altervista.org/support/acrylic](https://mayakron.altervista.org/support/acrylic/Home.htm) — "local DNS proxy for Windows… local HOSTS file optimized for handling hundreds of thousands of domain names with additional support for wildcards and regular expressions"; open source, Borland Delphi, GPL. Portable + service versions; service recommended. v2.2.1 released 2025-02-12 (memory-access fix); v2.2.0 Jul 2024 added cache fixes and CLI options; v2.1.0 added DoH protocol path support (`PrimaryServerProtocol=DOH`, per a Lumiun integration example using `PrimaryServerDoHProtocolPath` / `UseWinHttp=Yes`).
- GitHub mirrors: [racpast/Acrylic](https://github.com/racpast/Acrylic) (import created 2025-01; GPL-2.0), [mayakron/acrylic](https://github.com/mayakron/acrylic) (source of truth). No meaningful community, no releases on GitHub, distribution via SourceForge/Altervista.
- Config model: `AcrylicConfiguration.ini` (forwarders, DoH/SOCKS5/DoT? — DoH yes, explicit protocol path; DoT unclear), `AcrylicHosts.txt` (hosts + wildcards + regex; a domain is "free", pattern cheap, regex expensive per its own FAQ). Binds 127.0.0.1:53 by default.
- FAQ explicitly positions it next to Technitium: "A fully fledged DNS server (e.g. the Windows Server DNS Service, BIND, Technitiu[m])" as alternatives it is *not*; notes Acrylic slows with huge regex lists; recommends plain domain entries.
- No documented per-client rules, no built-in blocklist updater, no web console, no structured query log (debug log only), stats limited to cache hit display.
- When combined with DNSCrypt it works (FAQ), and with DoH via WinHTTP it works (Lumiun example).

### Inferences
- For your 200-domain list, Acrylic works: paste `0.0.0.0 d1.example …` entries into `AcrylicHosts.txt`, point the child adapter at 127.0.0.1, done. Maintenance burden = manual edits, no auto-update from HaGeZi (you'd script fetching + reformatting; HaGeZi doesn't publish an Acrylic dialect).
- Fail-open/closed: same caveat as loopback generally — if the service stops, name resolution stops (fail-closed) unless you leave a second (unfiltered) DNS configured, which defeats filtering. Prefer auto-restart service + watchdog over secondary DNS.
- Prefer AdGuard Home or Technitium unless the parent explicitly wants the smallest possible moving part and is comfortable editing INI files.

### Gaps
- DoT (not DoH) forwarding support is not clearly documented; assume DoH works, DoT uncertain.
- Current Windows 11 compatibility beyond the 2025-02 release note was not independently verified (no user reviews sampled).
- No evidence of signed binaries; SourceForge distribution + unsigned Delphi binaries may trip SmartScreen — verify publisher/hash.

---

## Blocklist sources for categories (social / gaming / streaming / chat / gambling / VPN)

### Takeaway
There is no single reputable "gaming/chat/streaming-only" list. The practical recipe: one low-FP general list (OISD small/big or HaGeZi PRO) + category alternates from StevenBlack (social, gambling) + HaGeZi Gambling / Social / VPN-bypass lists + your own ~200 curated domains (Discord, Steam community, Twitch, Roblox, etc.). Avoid vice-laden mega-lists on a child's machine unless you're prepared for false positives. URLhaus/HaGeZi TIF are threat lists, not category lists — use them as background protection, not for content categories.

### Cited Findings
**StevenBlack/hosts** ([github.com/StevenBlack/hosts](https://github.com/StevenBlack/hosts), MIT, ~31.4k stars, updated Nov 08 2026)
- Unified hosts file (adware+malware) 78,215 domains; **alternates** ship `social` (3,808 social-only; 82,289 unified+social), `gambling` (6,723 / 85,224), `fakenews` (2,187 / 80,700), `porn` (76,801 / 154,595), and any combination (e.g. `gambling-social` 10,531 social+gambling-only). Raw URLs: `https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/<name>/hosts`. Includes gaming-relevant minecraft-hosts tracking list by jamiemansfield (CC0-1.0).
- Windows caveat (explicit in README): large hosts files degrade Windows; may require disabling the **DNS Client service** or using their `disable-dnscache-service-win.cmd`; `updateHostsWindows.ps1` for Win10/11; `--compress` flag improves Windows performance. If you filter via a DNS server instead of the hosts file, this caveat doesn't apply — another point for a DNS backbone.
- Categories offered: fakenews, social, gambling, porn only. **No streaming/chat/gaming/VPN extension.**

**HaGeZi — dns-blocklists** ([github.com/hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists), GPL-3.0, ~28.7k stars, continuously updated; jsdelivr CDN canonical links e.g. `https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/…`)
- Multi-tier general lists: LIGHT 88,073 / NORMAL 207,301 / PRO 223,961 / PRO++ 241,481 / ULTIMATE 264,198 entries — recommend **PRO** (low breakage) + **TIF** (threat intel; medium 366,508 if RAM-constrained). "Don't add PRO + PRO++ — they build on each other."
- Category lists (entries as of Nov 2026): **Gambling** — full / medium / mini (link `adblock/gambling.txt` etc.); **Social Networks** — `adblock/social.txt`; **DoH/VPN/Tor/Proxy Bypass** — complete `adblock/doh-vpn-proxy-bypass.txt` (16,424 domains) or DoH-only `adblock/doh.txt` (3,568) + **DoH IPs** for firewall use (`ips/doh.txt`); **NSFW**; **Fake** (18,839); **Pop-Up Ads** (78,179); Piracy; URL Shortener.
- Formats: `adblock/` (AdGuard Home/Pi-hole/uBlock), `dnsmasq/`, `wildcard/` asterisk (Blocky/OPNsense), `wildcard/` onlydomains ("DNSCloak, DNSCrypt, FRITZ!Box, **TechnitiumDNS** …"), `rpz/` (Bind/Knot/PowerDNS/Unbound). NRD/DGA lists exist but are "use at your own risk".
- Warnings (verbatim gist): PRO++ and Ultimate can break legitimate sites; Ultimate blocks some META/WhatsApp/Microsoft trackers (Windows Spotlight, Xbox Live achievement history) — unblock guidance at `share/microsoft.txt`, `share/facebook.txt`. Update cadence: every build; lists pulled by CDN so entries change daily.

**OISD** ([oisd.nl](https://oisd.nl/)) — in operation since Nov 2018; "as few false positives as possible", girlfriend-test ethos; 4 lists: `https://big.oisd.nl`, `https://small.oisd.nl`, `https://nsfw.oisd.nl`, `https://nsfw-small.oisd.nl` (AdGuard Home/Pi-hole format). big ⊃ small; nsfw superset of nsfw-small. Recommended starter for report-only. **License not prominently stated** — treat as free-to-use but not clearly OSI-licensed (gap).

**firebog.net** (the Big Blocklist Collection, [firebog.net](https://firebog.net/)) — curated, single-maintainer (WaLLy3K) index of third-party lists sorted into Suspicious/Advertising/Tracking/Malicious/Other; green-ticked lists "least likely to interfere"; strikethrough = not recommended. Useful raw URLs: `https://v.firebog.net/hosts/…` mirrors (W3K Blacklist, Easylist, EasyPrivacy, Prigent, RPiList Malware/Phishing, KADhosts, WindowsSpyBlocker spy.txt `raw.githubusercontent.com/crazy-max/WindowsSpyBlocker/master/data/hosts/spy.txt` for Windows telemetry). Also `v.firebog.net/hosts/lists.php` gives the full URL-only + CSV dump for automation; URLs via mirrors count Cloudflare analytics for the maintainer. License: it's an aggregator — each listed list carries its own license (mostly permissive, e.g. MIT/CC); no single firebog license. Lists include an "Other/Adult" section (Prigent Adult 90 MB/3M+ entries — too big, skip).

**URLhaus** (abuse.ch, [urlhaus.abuse.ch/downloads/hostfile/](https://urlhaus.abuse.ch/downloads/hostfile/)) — hosts-format download of live malware URLs/domains; free, no account; continuously updated; malware-focused (not a content category list). Also [phishing.army](https://phishing.army/downloads/phishing_army_blocklist_extended.txt) (extended phishing list) and HaGeZi TIF for overlap.

**Firebog/StevenBlack category matrix relevant here:**
- Social: StevenBlack `social` (~3.8k), HaGeZi Social → covers FB/IG/TikTok/Snap/X/Reddit/Discord-ish mains.
- Gambling: StevenBlack `gambling` (~6.7k), HaGeZi Gambling full/medium/mini.
- VPN/proxy/DoH bypass: HaGeZi `doh.txt` / `doh-vpn-proxy-bypass.txt` — purpose-built to stop the child tunneling around your DNS.
- Streaming/chat/gaming main sites: **no quality dedicated list exists** — curate ~200 domains (twitch.tv, discord.com/canary/ptb, discord.gg, roblox.com, minecraft.net, steampowered.com, store.epicgames.com, chatgpt? no—) manually; HaGeZi Fake/Pop-Up/Porn are not substitutes.

### Inferences
- Recommended trio for category filtering on a child PC: **OISD small or HaGeZi PRO** (low-FP base) + **StevenBlack social+gambling alternates** (or HaGeZi Social + Gambling, whichever format your backbone eats) + **HaGeZi DoH-only bypass list** (so the child can't just enable browser DoH) + a hand-maintained ~200-domain list for Twitch/Discord/Roblox/Steam/Epic/BBchat.
- Skip HaGeZi Ultimate / WindowsSpyBlocker spy list on a family PC unless you enjoy broken Store logins and Xbox achievements.
- All of the above are URL-subscribe, auto-updating daily/24h-cycle — good fit for a set-and-forget parent.

### Gaps
- License for OISD not clearly published (not necessarily unlicensed; unverified).
- No maintained, well-curated "gaming" or "streaming" category list found; HaGeZi has gambling/social only.
- hagezi entry counts change per build; figures quoted are Nov 2026 README values.
- Update cadence of StevenBlack unified file: regenerated on merged data changes (typically days–weeks), not on a fixed schedule; gambling/social alternates likewise manual.

---

## DoH/DoT blocking on Windows Home/Pro — from the child's (non-admin) account

### Takeaway
Layered approach works without TLS interception: (1) pin adapter DNS to the loopback/LAN filter with `netsh` (child lacks admin → can't revert), (2) kill browser DoH via machine-wide HKLM (or child-only HKCU) policies for Chrome/Edge, (3) block outbound 853/tcp+udp for the child's SID only via Windows Firewall, optionally (4) block known DoH resolver IPs with HaGeZi DoH-IP list at the firewall. None of this breaks HTTPS — it just makes DNS honest again. HKLM policies apply to all users; HKCU policies apply per-user — use HKCU for a child-only knob.

### Cited Findings
- **Chrome policy**: `HKLM\SOFTWARE\Policies\Google\Chrome\DnsOverHttpsMode` = `off` (REG_SZ); per Chromium ADMX, `HKCU\SOFTWARE\Policies\Google\Chrome\DnsOverHttpsMode` also maps. Values: `off` disables, `automatic` = DoH with insecure fallback, `secure` = DoH only. If policy unset on managed devices, DoH is not sent; on unmanaged installs the browser may still attempt DoH against a known DoH resolver paired with the system resolver ([admx.offsec.nl / Chromium policy](https://admx.offsec.nl/policy/google/google/google-chrome/controls-the-mode-of-dns-over-https/)).
- **Edge policy**: `HKLM\SOFTWARE\Policies\Microsoft\Edge\DnsOverHttpsMode` = `off` (REG_SZ); GP path "Administrative Templates/Microsoft Edge"; ADMX file `MSEdge.admx`; mandatory, dynamic-refresh, Windows ≥83 ([Microsoft Edge browser policies: DnsOverHttpsMode](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/dnsoverhttpsmode), updated 2026-05; same content via deployedge/microsoft-edge-browser-policies). Pair with `DnsOverHttpsTemplates` only if you want to *force* a resolver — for blocking you want `off`.
- **Windows 11 native DoH**: per-interface; set via `netsh dns add encryption server=1.1.1.1 dohtemplate=https://1dot1dot1dot1.cloudflare-dns.com/dns-query dohsubnet=0.0.0.0/0` (documented pattern; not re-verified this session). Template must match the resolver; mismatch/“does not support encryption” states confuse the UI. There is no clean machine-wide GPO for Windows' built-in DoH — it rides per-adapter settings and DHCP. (Confidence: moderate; see Gaps.)
- **Per-adapter vs global DNS**: Windows DNS settings are **per adapter** (netsh `interface ip set dns` / `interface ipv6 set dns` per interface alias). A single global override does not exist; DHCP leases can re-supply router DNS on renew unless you set the adapter to static DNS. `netsh interface ip set dnsservers "Wi-Fi" static 127.0.0.1` pins it. Validate with `ipconfig /all` and `Get-DnsClientServerAddress`.
- **Firewall per-SID**: Windows Firewall (netsh advfirewall / WFAS) rules accept a `users=` scoping (SID/domain\user) — the supported pattern for "block 853 for the child only" is an **outbound** rule with `remoteport=853 protocol=TCP` (+ UDP) scoped to the child's SID, authored from an elevated context or GPO (Computer Config → Policies → Windows Settings → Security → Windows Defender Firewall with Advanced Security → Outbound Rules → scope "users"). Exact `netsh advfirewall firewall add rule … user=S-1-5-21-…` parameter spelling must be confirmed against the current [netsh advfirewall firewall reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-advfirewall-firewall) — not re-verified in this session (Gaps).
- **HaGeZi DoH/VPN/Proxy list** exists precisely for this: block 3,568 known DoH resolver domains (or 16,424 incl. VPN/proxy/Tor) at the DNS layer; plus an IPv4 IP list for firewall use. Blocking 853 stops DoT; blocking DoH needs domain+IP measures because it rides 443.
- **Child can't escalate**: changing HKLM, firewall rules, adapter DNS, or installing a VPN all require admin. A standard (non-admin) child cannot persist past any of the above. What a child *can* do: portable VPN apps (run as user) that tunnel 443 to a VPN — only mitigations are application control (AppLocker/WDAC, or parental-control/SmartScreen) — out of scope but flag it.
- **Win11 "DoH settings corruption"**: widely reported symptom — adapter shows the DoH template greyed/"not encrypted", or `netsh dns show encryption` errors after server/template changes; workaround is removing and re-adding the encryption entry per server. No official Microsoft root-cause note found; treat as UI/config-drift annoyance, not corruption of the registry. (Gaps.)

### Inferences
- Minimal sufficient set: static loopback DNS on the child adapter + HKLM (or HKCU) `DnsOverHttpsMode=off` for Chrome+Edge + outbound 853 rule scoped to child SID + OISD/HaGeZi DoH-bypass list in the resolver. Firefox needs its own policy file (`policies.json`, `DNSOverHTTPS: disabled`) — Firefox is not covered by Chrome/Edge keys. (Not asked, but adjacent; note it.)
- HKCU policies are the cleanest "child-only, parent unaffected" mechanism; HKLM is machine-wide and simpler. Use HKCU `Software\Policies\…` for both browsers so the parent's own DoH/privacy prefs aren't touched.

### Gaps
- Exact `netsh advfirewall` per-user parameter syntax (`user=` accepts SID/name; exact enum values) not verified — check MS Learn before scripting; GPO WFAS route is verified conceptually but the exact UI path edition differences (Home lacks gpedit) matter: **Windows Home** has no gpedit, so HKLM/HKCU registry + `netsh` are the enforcement path; netsh advfirewall works on Home.
- No official Microsoft statement on Windows 11 built-in DoH "corruption" behavior was found; per-adapter DoH config silently persisting across DHCP renews is unverified.
- Encrypted Client Hello (ECH) and QUIC (HTTP/3) DNS bypass are not addressed by any of the above — domain-level DNS filtering never sees inside QUIC SNI when ECH is deployed; expect this to grow over 2026. Not solveable without content inspection (out of scope).

---

## Report-only logging design (Pi-hole-style domain logs, retention)

### Takeaway
Both leading candidates give you domain-only logs with retention knobs: AdGuard Home (rotation interval 6h–90d, effective ≈2×, optional client-IP anonymization, per-client opt-out, `querylog.json` files or none) and Technitium (Settings → Logging + Query Logs (Sqlite) app; UTC timestamps; not real-time). Neither has a true "dry-run block" switch, so report-only = *log everything, block nothing* (or block only your ~200-domain test list), then mine the log offline against the list. Pi-hole's `gravity`/FTL log is the reference format — AGH's `querylog.json` records `{domain, client, rule, action, time}` which is equivalent for auditing.

### Cited Findings
- AdGuard Home query log (v0.107.24+): `querylog: {enabled, file_enabled, interval, size_memory, ignored[], ignored_enabled, dir_path}`; rotation intervals via API enum {0.25=6h, 1, 7, 30, 90 days}; `anonymize_client_ip` (DNS-level `anonymize_client_ip`); per-client `ignore_querylog`/`ignore_statistics`; effective retention ≈ 2× interval ([Configuration KB](https://adguard-dns.io/kb/adguard-home/configuration/); [Configuration wiki](https://github.com/AdguardTeam/AdGuardHome/wiki/Configuration); maintainer comment in [#2610](https://github.com/AdguardTeam/AdGuardHome/issues/2610) and [#7893](https://github.com/AdguardTeam/AdGuardHome/issues/7893)). OpenWrt sizing: ~53 MB / 7 days query logs; ~2 MB / 90 days stats ([OpenWrt wiki](https://openwrt.org/docs/guide-user/services/dns/adguard-home)). Query log is searchable in UI; REST API `/querylog` (openapi) for scripted export.
- Technitium: Settings → Logging enables query logging; **Query Logs (Sqlite)** app gives filtered queries ("not real time… need to click to get results"); UTC timestamps (author: "The date time in logs is in UTC"); dashboard stats widgets; API exists (APIDOCS.md) for automation.
- Pi-hole reference: gravity log format = one domain per line with action (`gravity` list); FTL DB has `MAXDBDAYS` (commonly cited default 365 in forensic write-ups — community source, treat as indicative) — included here only as the comparator AGH/Technitium logs emulate.
- Log content = domain (+client IP unless anonymized) + rule/action. **No URLs, no content, no page titles** — satisfies "domain-only logs". Both can prune aggressively; set interval=7d during soak, 30d after.
- For report-only operation: keep blocklists **disabled**, query log on, per-client anonymize off (you want per-device attribution), and feed `querylog.json` exports to a simple differ against your ~200-domain category list (Python: match eTLD+1 of logged domains against list). AdGuard Home also lets you add lists but disable them in UI while keeping log — blocking is toggleable per list without delete.
- hagezi provides an online **Blocklist Lookup** to check a domain against all HaGeZi lists; Pi-hole forum "Commonly Whitelisted Domains" and `anudeepND/whitelist` are the canonical false-positive remedy references.

### Inferences
- Minimal viable report-only loop: AdGuard Home, filtering OFF (or only oisd small ON if you also want a smoke test), interval=7d, anonymize off, run 2 weeks, weekly export → offline match against category list → promote matched domains to block.
- Keep `ignored: [*.arpa]` (rDNS noise) — otherwise PTR floods swamp the log.

### Gaps
- Technitium's log file format/rotation mechanics (file names, retention in days) were not verified from docs; only the Sqlite app workflow was sourced (blog comment, Oct 2024).
- AGH issue #7893 (2025–2026, open) shows the "interval" is a *file rotation*, not strict lookback — effective retention ≈2× interval and UI may show older entries; plan capacity accordingly.
- No evidence either tool logs "would-have-blocked" when lists are disabled — report-only cannot be a toggle; it's a workflow.

---

## LAN-level alternative (router DNS handoff/OpenWrt) vs device-level loopback

### Takeaway
For a **single** family PC, device-level is the right call: install Technitium or AdGuard Home on that PC (Windows service, auto-start), pin the adapter to 127.0.0.1, done — zero router coupling, parent keeps full control, child account unaffected. Router-level filtering (OpenWrt + AdGuard Home/dns-adblock, or just DHCP handing out an AGH IP) is the right call when you later want every device covered (TVs, consoles, other PCs) — but for one child PC it adds a failure domain and admin overhead for no benefit.

### Cited Findings
- Technitium docs/blog: install on a spare LAN machine, set router DHCP DNS to it, all devices covered ([Technitium blog, Oct 2018](https://blog.technitium.com/2018/10/blocking-internet-ads-using-dns-sinkhole.html)); requires inbound TCP/UDP 53 allowed on that host.
- AdGuard Home: official OpenWrt wiki exists (`openwrt.org/docs/guide-user/services/dns/adguard-home`) including flash-storage caveats (logs must live off eMMC; short retention recommended); OpenWrt can run AGH, dnsmasq+adblock, or plain dnsmasq with HaGeZi dnsmasq-format lists.
- HaGeZi dnsmasq/RPZ formats exist precisely for router-class gear (dnsmasq ≥2.86, OpenWrt ≥22, OPNsense, FRITZ!Box v8.40+) — no Windows box required.
- Windows DHCP-supplied DNS can be overridden per-adapter statically on the child PC regardless of what the router hands out; the two approaches compose (router as fallback, PC as enforcement) but one is enough.

### Inferences
- Decision rule: if the goal is *this one PC*, choose device-level. Device-level survives router swaps, ISP router DNS hijack, travel (same machine keeps its policy), and doesn't touch parent's other devices.
- Router-level becomes attractive later if the parent wants the same filtering on Android/console/TV — then OpenWrt (or the ISP router's DNS handoff to a Pi/mini-PC running AGH) beats per-device agents. Stopgap without new hardware: none of this requires OpenWrt; a static-DNS lease reservation on the stock router + LAN AGH on the child PC covers the same ground.
- Loopback (127.0.0.1) vs LAN-IP forwarding on the child PC: loopback keeps DNS on-box (simpler, no inbound needed); LAN-IP is only needed if the filter runs elsewhere.

### Gaps
- Specific OpenWrt versions/package names current as of Nov 2026 not verified (adblock package vs AGH container strategy changes per release); treat as "feasible, standard, docs exist", not a ready-made recipe.
- Whether ISP-locked routers (non-Flashable OEM) permit custom DNS handoff was not evaluated — most do (LAN DNS field), but this is router-specific; device-level sidesteps the question entirely.

---

## Risks (HTTPS breakage, false positives, fail-open, Xbox/Microsoft services)

### Takeaway
DNS filtering cannot break TLS itself — worst case it returns 0.0.0.0/NXDOMAIN for a domain, and the site fails to load. Real risks are (1) blocked shared-CDN/auth endpoints breaking legit apps (Store, Xbox Live, EA/Origin, Steam downloads), (2) over-aggressive lists (HaGeZi Ultimate, WindowsSpyBlocker spy) breaking Windows features, (3) fail-closed loopback outages when the filter service dies, (4) child bypassing via DoH/VPN despite controls, (5) expectation management — DNS blocks whole domains, not "videos" or "chats". Mitigate with: conservative base list (OISD small / hagezi PRO), staged rollout, a documented unblock procedure, auto-restart service, and NO secondary unfiltered DNS.

### Cited Findings
- Sinkholing → 0.0.0.0 / `::` / optional NXDOMAIN; blocking happens pre-connection, cert validation never starts — HTTPS "breakage" manifests as timeout/refused, not cert errors (except Technitium Block Page, which shows SSL errors — author confirmed Block Page is unusable for HTTPS sites: "users will mostly see SSL certificate error notices") ([Technitium blog](https://blog.technitium.com/2018/10/blocking-internet-ads-using-dns-sinkhole.html)).
- Known false-positive pattern: EA/Origin — symptom "missing download button", fix = whitelist `cdn.optimizely.com` and `split.io` (uses EA Desktop; from [firebog whitelisting guidance](https://firebog.net/) mirroring AdAway list). Commonly Whitelisted Domains: t.ly/bit.ly shorteners, s.shopify.com. Gaming-adjacent: Hagezi Ultimate blocks some Microsoft trackers → "Windows Spotlight / Xbox Live Achievements Activity History" breakage; Microsoft unblock list at `share/microsoft.txt`; META/WhatsApp web breakage → unblock `share/facebook.txt`. Author's stance: Ultimate = "experienced users... someone can unblock things".
- `player.microsoft.com` (Microsoft Store / Xbox / PC Game Pass web) rides Microsoft's telemetry-shared infrastructure — aggressive Microsoft-telemetry lists can catch Store/Game Pass endpoints; gaming remains functional only if these resolve. WindowsSpyBlocker `spy.txt` is explicitly a *telemetry* list and its own README warns about breaking Windows features; firebog lists it under Tracking, not malware.
- HaGeZi gambling/social are category lists built for "block the whole category" — they will block e.g. twitch.tv-adjacent chat infra if you add chat domains yourself; Xbox Live and Steam rely on CDNs you must NOT block (steamstatic, riotcdn, epic games CDN) or downloads die.
- Fail-open vs fail-closed: loopback resolver down → all resolution fails (fail-closed). Technitium Windows service + service recovery options, or NSSM for CoreDNS, or AGH as service all auto-restart. Do **not** set a secondary (e.g. 8.8.8.8) on the adapter — Windows will race/fallback queries to it whenever the primary is slow/down, punching a permanent hole past the filter. (Behavior per [MS DNS client docs](https://learn.microsoft.com/en-us/windows-server/networking/dns/dns-client) — not re-fetched, standard documented behavior; mark confidence high but note.)
- Bypass: child using DoH browsers bypasses plain DNS filtering — mitigated per DoH section; definitive bypass remains user-installed VPN/Tor (admin-only on managed PC) or cellular tethering — out of scope for DNS-only controls.
- HTTPS ecosystem drift: QUIC/ECH adoption will increasingly hide SNI from on-path observers; DNS-answer-based blocking is unaffected (it never sees SNI), which is *why* DNS filtering is the right layer here.

### Inferences
- Rollout plan: Weeks 1–2 report-only (AGH logging, no blocking) → review hits (expect telemetry noise: google-analytics, crashlytics — filter via HaGeZi PRO rather than by appending "block analytics") → enable OISD small/PRO → tune (whitelist EA/Steam/Store CDNs) → finally category lists. Keep a printed unblock runbook for the parent.
- Test matrix before locking: Microsoft Store sign-in, Xbox PC Game Pass install, Steam download, Roblox join, Epic login, Discord voice, Twitch playback — each exercises different CDNs; expect to whitelist 2–5 domains.
- "Fail-open" is the wrong target for a safety control; aim for **fail-operational with instant manual recovery**: service auto-restart + parent ability to toggle lists off in 30 seconds from the AGH web UI (`adguard.home` LAN URL or 127.0.0.1:3000/80).

### Gaps
- No controlled test data found quantifying which specific auth/CDN domains break per list; the gaming-launcher CDN breakage list must be discovered empirically during the soak phase (that's the point of report-only).
- No sourced statement on Roblox/Steam/Epic exact domain inventory (deliberately avoided inventing one).
- Windows DNS client fallback/race behavior (secondary DNS usage timing) not re-verified against current MS Learn; treat as well-known best practice, not audited fact.

---

## Bottom line for the report writer

- **Pick**: AdGuard Home (primary) or Technitium (if parent wants DHCP/clustering/DoT self-hosting) on the child PC as a Windows service; adapter DNS → 127.0.0.1; Chrome/Edge `DnsOverHttpsMode=off` via HKCU (child-only) or HKLM; HaGeZi `doh.txt` in the resolver; report-only for 2 weeks, then oisd small + HaGeZi PRO + StevenBlack social+gambling + a ~200-domain curated list.
- **Skip**: CoreDNS (hand-rolled, no UI), Acrylic (works but hobby-grade, no per-client), HaGeZi Ultimate, WindowsSpyBlocker spy list, any Prigent-scale adult mega-list, and secondary/unfiltered DNS on the adapter.
- **Accept**: domain-granularity only; chat/streaming blocked wholesale (can't do "Twitch but not chat"); bypass via admin-installed VPN impossible for non-admin child.
- **Verify before deploying**: exact `netsh advfirewall` per-SID syntax; Windows Home vs Pro policy path (Home: registry-only, no gpedit); Technitium blocklist file format (use HaGeZi `*-onlydomains.txt`); AGH query-log effective retention (≈2× interval).
