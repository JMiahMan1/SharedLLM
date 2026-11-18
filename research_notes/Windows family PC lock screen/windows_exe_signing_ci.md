# Packaging, Signing, and CI/CD Delivery for a Self-Built Open-Source Windows .NET Agent (`jarvis-guard.exe`) via GitHub Actions

Research date: 2026-11-10. Scope: Windows 10/11 targets, single household self-hosted publishing, free/cheap path preferred, no app stores, current as of 2026 (several 2024–2025 sources flagged as stale where noted).

---

## .NET 8 single-file publish for win-x64 (self-contained), trimming/AOT viability, NativeAOT status 2026

### Takeaway
Self-contained single-file publish for win-x64 is the standard, well-supported path for a WinForms tray app + Windows Service; expect ~15–25 MB Hello-World-class output (no official number for this exact app profile). NativeAOT in 2026 supports Windows x64/Arm64 but is **not a good fit for WinForms** — WinForms is not supported by Native AOT; trimming is also risky for reflection-heavy WinForms/DI code. Use self-contained single-file JIT.

### Cited Findings
- Single-file self-contained publish is configured via `PublishSingleFile=true`, `SelfContained=true`, `RuntimeIdentifier=win-x64`, e.g. `dotnet publish -r win-x64`. Works for any project shape including WinForms. — [Single-file deployment (Microsoft Learn)](https://learn.microsoft.com/en-us/dotnet/core/deploying/single-file/overview)
- A self-contained single-file Hello World is reported at ~20 MB by community members. — [Stack Overflow: Single .exe file is too large](https://stackoverflow.com/questions/71575038/single-exe-file-is-too-large)
- Native AOT deployment supports Windows x64 and Arm64 (x64/Arm64/x86 for some scenarios) and publishes with `dotnet publish -r win-x64`, but Microsoft Learn's Native AOT compatibility docs list requirements that WinForms does not meet (no `System.Windows.Forms` support in the AOT-compatible framework feature set; WinForms relies on reflection, COM interop, and dynamic code paths AOT cannot guarantee). Verified framework limitations: AOT requires library trim-compatibility annotations and no runtime code generation. — [Native AOT deployment overview](https://learn.microsoft.com/en-us/dotnet/core/deploying/native-aot/)
- .NET 10 (current in late 2026 per Microsoft servicing) adds platform-specific .NET tools packaging for self-contained and Native AOT tools; indicates Native AOT tooling is maturing but primarily for CLI/tools, not WinForms desktop UI. — [Andrew Lock, Exploring .NET 10](https://andrewlock.net/exploring-dotnet-10-preview-features-7-packaging-self-contained-and-native-aot-dotnet-tools-for-nuget/)
- Visual Studio 2026 ships self-contained publish tooling; some 2026 reports of VS2026 publish UI producing incorrect single-file output exist (developer community issue) — not authoritative but worth pinning SDK behavior in CI rather than trusting IDE publish. — [Developer Community issue 11019257](https://developercommunity.visualstudio.com/t/11019257)
- Windows Service + WinForms tray in one EXE is a common single-project pattern (service host + tray client or tray-in-service with session-0 awareness); nothing in single-file publish blocks it, but note a service runs in session 0 and cannot show UI — the tray must be a separate per-user process or the service must spawn/elevate a user-session helper. (Design inference; see Gaps.)

### Inferences
- Recommended profile: `net8.0-windows` (or `net10.0-windows` if targeting current LTS/STS; net8.0 is LTS until Nov 2026 — **near end of support; prefer net8.0 only if you already have it, else net10.0**), self-contained, single-file, `win-x64`, trimming **disabled** (WinForms trimming is effectively unsupported), ready-to-run (`PublishReadyToRun=true`) for faster cold start instead of NativeAOT. Expected size ~60–90 MB for a full WinForms app with runtime included (inference from the 20 MB Hello-World datapoint + WinForms payload; not a published figure — treat as estimate).
- If startup size matters later, NativeAOT is viable for a *separate* headless service binary (no WinForms) but not for the tray app; running two binaries complicates the signing/update story.

### Gaps
- No official Microsoft size figure for a WinForms self-contained single-file exe (no source found; measured numbers vary).
- No authoritative 2026 statement found specifically on NativeAOT + WinForms support matrix beyond general "AOT requires AOT-compatible" constraints; recommend staying JIT.

---

## Authenticode signing in 2026: OV vs EV vs Azure Artifact (Trusted) Signing vs SignPath OSS vs other options — realistic for a personal GitHub project

### Takeaway
For a personal open-source project from a personal GitHub, the realistic free path is **SignPath Foundation** (free, but publishes under SignPath Foundation's name + manual per-release approval + strict OSS terms) or **Azure Artifact Signing (formerly Trusted Signing)** at **$9.99/month** (US/Canada individuals only; orgs in ~12 countries). Signed reputation builds slowly regardless; EV no longer skips SmartScreen, so don't pay the EV premium.

### Cited Findings

**Azure Artifact Signing (renamed from Azure Trusted Signing; rebranded ~Jan 2026)** — [Artifact Signing overview](https://learn.microsoft.com/en-us/azure/artifact-signing/overview); [GitHub: Azure/artifact-signing-action](https://github.com/Azure/artifact-signing-action); [Tech Community blog: Trusted Signing open to individuals (public preview)](https://techcommunity.microsoft.com/blog/microsoft-security-blog/trusted-signing-is-now-open-for-individual-developers-to-sign-up-in-public-previ/4273554); renamed to Artifact Signing Jan 2026 — [artifact-signing-action PR #108](https://github.com/Azure/artifact-signing-action/commit/3faf9d2debc72605cc2a91573f35f6d291d13089)
- Pricing (as of Oct 2026 per my-ssl.com, corroborated by [Azure pricing page](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/) which could not be fetched verbatim): **Basic $9.99/month, up to 5,000 signatures/month, 1 certificate profile of each type; Premium $99.99/month, 100,000 signatures; $0.005/extra signature; ~$119.88/year for Basic.** — [my-ssl.com comparison, Oct 2026](https://my-ssl.com/learn/azure-trusted-signing-vs-code-signing-certificate); [gdgsoft.com FAQ](https://www.gdgsoft.com/faq/azure-trusted-signing-cost-effective-exe-code-signing). *Flag: pricing from secondary sources; verify on the Azure pricing page — Microsoft has changed tiers before (preview had different SKU names).*
- **Eligibility**: Public Trust certificates for **organizations** in US, Canada, EU, UK, Australia, New Zealand, Japan, South Korea, Singapore, Switzerland, Norway, Israel. **Individual developers: US and Canada only.** Private Trust has no geographic restriction. — [Quickstart: Set up Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
- Individual Public Trust validation: Azure **billing account must be Account Type "Individual"**; legal name + sold-to address must match government-issued ID; identity proofed via third party (AU10TIX) with mobile ID scan + Microsoft Authenticator Verified ID. Public validation takes **1–20 business days**. Email verification links expire in 7 days. — [Quickstart](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
- Account types: **Basic vs Premium SKU**; Public Trust, Private Trust, Public Trust Test, VBS Enclave, CI policy profile types; certificates live in FIPS 140-3 L3 HSMs; zero-touch cert lifecycle; supports digest signing (file never leaves endpoint) — [Overview](https://learn.microsoft.com/en-us/azure/artifact-signing/overview)
- **No EV option**; no hardware token needed; integrates with CI/CD (GitHub Actions, Azure DevOps); Microsoft's recommended non-Store path — [SmartScreen reputation doc](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- OIDC federation from GitHub Actions is the recommended auth (id-token: write + azure/login; no long-lived secrets). Official action `azure/artifact-signing-action@v2` with `azure/login@v3`:
```yaml
on: [push]
permissions: { id-token: write, contents: read }
jobs:
  build-and-sign:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v5
      - run: dotnet publish -c Release -r win-x64 --self-contained
      - uses: azure/login@v3
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - uses: azure/artifact-signing-action@v2
        with:
          endpoint: https://eus.codesigning.azure.net/
          signing-account-name: <account>
          certificate-profile-name: <profile>
          files-folder: ${{ github.workspace }}\...\bin\Release\net8.0-windows
          files-folder-filter: exe,dll
          file-digest: SHA256
          timestamp-rfc3161: http://timestamp.acs.microsoft.com
          timestamp-digest: SHA256
```
— [Azure/artifact-signing-action README](https://github.com/Azure/artifact-signing-action) (example verbatim from repo)
- Timestamp endpoint used by Microsoft: `http://timestamp.acs.microsoft.com` (RFC 3161). Note gist comments questioning/verifying it; signtool SDK 10.0.26100.0 reportedly **rejects RFC 3161 HTTPS timestamp URLs** (needs http://) on Server 2022 runners. — [GitHub gist fd754e...](https://gist.github.com/Manouchehri/fd754e402d98430243455713efada710); [Microsoft Q&A 6033027](https://learn.microsoft.com/en-sg/answers/questions/6033027/signtool-sdk-10-0-26100-0-rejects-rfc-3161-https-u)
- Hanselman walkthrough of signing with Trusted Signing in GitHub Actions exists — [hanselman.com](https://www.hanselman.com/blog/automatically-signing-a-windows-exe-with-azure-trusted-signing-dotnet-sign-and-github-actions); alternative via `sign` CLI (dotnet tool) — [idunno.org §Code signing with Azure Artifact Signing](https://idunno.org/net-code-nupkg-signing-in-github-actions/) with a full two-job build→download-artifact→windows-latest→sign YAML including `attestations: write` permission.
- Region availability: Brazil South, Central US, East US, Japan East, Korea Central, North Central US, North Europe, Poland Central, South Central US, Switzerland North, West Central/US/US2/US3, West Europe — [Quickstart table](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)

**SignPath Foundation / signpath.io OSS** — [signpath.org](https://signpath.org/); [Terms](https://signpath.org/terms.html)
- Free for OSI-approved open source projects without commercial dual-licensing; no personal ID upload needed (they verify binary↔repo provenance instead).
- **But**: certificate is issued to "SignPath Foundation" — publisher shows as *SignPath Foundation*, not you. **Every release needs manual approval**; build must be verifiable from repo source; team must have MFA + authors/reviewers/approvers roles; you must specify a "**Code signing policy**" on your project homepage/download page naming SignPath.io/Foundation + team roles + privacy policy; signed binaries must carry product-name/version metadata enforced via SignPath artifact configuration. Violations → cert revoked.
- Eligibility questions for a personal parent-run project: "actively maintained", "already released", "described on download page". A hobby single-household tool *may* qualify but the mandatory code-signing-policy page + metadata enforcement + approval latency are real friction; also a family-monitoring tool could plausibly be seen as touching "security of execution environment" rules — not prohibited (it detects breaches/protects children, doesn't exploit), but expect scrutiny; SignPath has sole discretion, no appeal. **Realistic but bureaucratic; publisher name will not be yours.**

**OV / EV certificates (CA-issued, e.g. Certum, DigiCert, Sectigo via KeyLocker etc.)**
- OV ~$99/yr (My-SSL figure; certum-style) — no country restriction; publisher name = your personal/org name; EV no longer grants instant SmartScreen reputation — [my-ssl.com](https://my-ssl.com/learn/azure-trusted-signing-vs-code-signing-certificate); [SmartScreen doc: "EV certificates no longer bypass SmartScreen… Paying a premium for EV solely to avoid SmartScreen warnings is no longer justified."](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- DigiCert KeyLocker and similar cloud HSM offerings remove the FIPS-token requirement but are priced for orgs; realistic only via reseller (~$300–500/yr class) — *no primary source fetched for 2026 KeyLocker pricing; treat as gap.*

**Self-signed**: behaves identically to unsigned for SmartScreen purposes — do not bother for distribution. — [SmartScreen doc table](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

### Inferences
- **Best free path for this exact scenario** (US/Canada individual parent, open-source, own GitHub, self-hosted server): Azure Artifact Signing Basic at $9.99/mo if the parent is in US/Canada with an Individual billing account — best publisher identity (their own name), native GitHub OIDC, no secret storage of keys. SignPath Foundation is the $0 alternative if the project fits its terms (OSI license, public homepage with code-signing policy, manual approval per release — acceptable for a family tool since releases are rare).
- Do **not** buy EV for SmartScreen reasons (obsolete). OV only makes sense if outside the Artifact Signing eligibility countries or Azure account friction is unacceptable; OV still shows your name and builds reputation, but costs ~$99–300/yr and stores keys in KeyLocker-class HSM/token.
- Signing frequency: Artifact Signing's 5,000 signatures/month covers thousands of releases — irrelevant at family scale; the $9.99 is pure fixed cost for publisher identity.

### Gaps
- Exact 2026 DigiCert KeyLocker / Certum / Sectigo cloud signing prices not fetched from primary sources (flag stale/secondary pricing above).
- Whether SignPath Foundation would accept a parental-control OSS tool is unverifiable without applying; terms give Sole discretion.
- Artifact Signing private-trust path (no geo limits, but builds **private** PKI trust only — useless for public SmartScreen but free of identity friction; could be useful if children PCs pre-trust the root — probably overkill vs NSIS/Inno self-sign + local CA).

---

## GitHub Actions mechanics: windows-latest, setup-dotnet, attestations, OIDC, SBOM, retention, self-publish YAML in the wild

### Takeaway
`windows-latest` now = **Windows Server 2025** (VS2022 image; VS2026 migration to `windows-latest`/`windows-2025` began June 2026, default flip per 2026 changelog). Use `setup-dotnet`, OIDC (`id-token: write`) to Azure, `azure/artifact-signing-action@v2`, and `actions/attest-build-provenance` / `actions/attest` for provenance + SBOM (both require `attestations: write` and public repos or GitHub Advanced Security for private). Microsoft Store-signed is not required; self-hosted family server just needs the signed exe + checksums.

### Cited Findings
- Runner images: `windows-latest`, `windows-2025`, `windows-2025-vs2026` all map to Windows Server 2025 images; VS2026 became default in the 2025 image via migration starting **June 8, 2026**; `windows-2025-vs2026` GA'd 2026-02/05. — [actions/runner-images README](https://github.com/actions/runner-images); [GitHub Changelog Feb 2026](https://github.blog/changelog/2026-02-05-github-actions-early-february-2026-updates/); [runner-images issue #14017](https://github.com/actions/runner-images/issues/14017)
- Images include full .NET SDKs (VS2022/VS2026 image includes multiple SDKs); still use `actions/setup-dotnet` to pin `dotnet-version` for reproducibility — [setup-dotnet pattern appears in idunno.org workflow](https://idunno.org/net-code-nupkg-signing-in-github-actions/)
- **Artifact attestations**: `actions/attest-build-provenance` (v2/v3 era; docs now reference `actions/attest@v4`) generates Sigstore-signed provenance binding artifact digest → workflow; verify with `gh attestation verify`; **available on GitHub Free/Pro/Team for public repos** (private needs GHEC/GHAS). Requirements: `permissions: id-token: write` + `attestations: write`. — [actions/attest-build-provenance](https://github.com/actions/attest-build-provenance); [GitHub docs: artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds)
- **SBOM**: create SBOM (e.g. `anchore/sbom-action`), then `actions/attest` with `sbom-path` input; verification of SBOM attestations now requires `--predicate-type` (CLI default switched to provenance in Feb 2025). — [GitHub docs SBOM section](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations#generating-an-attestation-for-a-software-bill-of-materials-sbom); [GitHub Changelog Feb 2025](https://github.blog/changelog/2025-02-18-recent-improvements-to-artifact-attestations/)
- **OIDC to Azure**: workflow needs `permissions: id-token: write`; register an Entra app + federated credential (subject `repo:owner/repo:ref:refs/heads/main` or environment); use `azure/login@v3` with `client-id/tenant-id/subscription-id` secrets — no client secret stored. — [Connect from GitHub Actions with OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect); [Configure OIDC in Azure (GitHub docs)](https://docs.github.com/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-azure)
- Real-world YAML: `azure/artifact-signing-action` README example (quoted in previous section); Scott Hanselman's GitHub Actions Trusted Signing post; idunno.org full two-job workflow (build ubuntu → artifact → sign on windows-latest with `attestations: write`, `id-token: write`) — [idunno.org](https://idunno.org/net-code-nupkg-signing-in-github-actions/). *No verified public repo named "Navel" with signing workflows was found — see Gaps.*
- Publishing self-hosted: nothing prevents `curl`/`scp`/`rsync`/`Invoke-WebRequest` PUT in a final step to the family server; Release assets also possible via `softprops/action-gh-release` (not fetched; standard practice, gap).
- Retention: `actions/upload-artifact` default retention; set `retention-days: 7` as seen in idunno.org; release binaries attached to GitHub Releases don't expire. Attestations persist as repo objects.
- **Pattern for self-publish flows in the wild**: Tauri updater uses a static `latest.json`/`update.json` + minisign/ed25519 signature checked by the app; Rust `self_update` crate checks GitHub Releases API + sha256 of assets; .NET has no first-party equivalent (hence DIY version.json+SHA256SUMS, next section). — [Tauri v1 updater docs (static JSON example)](https://v1.tauri.app/v1/guides/distribution/updater/#server-support); [Tauri v2 updater](https://v2.tauri.app/plugin/updater/)

### Inferences
- Minimal release workflow: `build job (windows-latest, setup-dotnet@v5, dotnet publish)` → `sign step (azure/login OIDC + azure/artifact-signing-action@v2)` → `attest (attest-build-provenance + attest sbom)` → `upload (scp/curl to family server + write version.json + SHA256SUMS)`. Keep signing in a separate job with `environment: release` + required reviewer if you want a manual gate (idunno.org pattern).
- Note: Artifact Signing action disables dependency caching by default for good reason (cache poisoning); leave cache off or audit it.

### Gaps
- Could not verify a public repo literally named "Navel" with .NET signing workflows (search surfaced nothing); nearest real examples cited above.
- Exact `windows-latest` preinstalled .NET SDK versions in the VS2026 image not fetched (check the runner-images readmes linked).
- No fetched source for a ready-made ".NET exe → self-hosted server via scp" GitHub Action; straightforward to hand-roll.

---

## SmartScreen reputation for small self-signed projects — what users see, how reputation builds, practical mitigations, Defender submit-for-analysis

### Takeaway
EV certificates no longer skip SmartScreen (explicit Microsoft statement). Signed (OV/Artifact Signing) new files still warn until hash/cert reputation accumulates organically ("several weeks and hundreds of clean installs," no hard threshold). Reputation does **not** transfer across versions unless signed with the same cert. There is no mechanism to manually submit for SmartScreen reputation review; false positives *can* be submitted to Microsoft's Security Intelligence portal. For a single household, the pragmatic answer is: sign with a stable identity, and have kids'/parents' PCs either click "More info → Run anyway" once, or pre-allow the publisher.

### Cited Findings
- SmartScreen evaluates **publisher reputation** (signed? known cert?) and **file hash reputation** (download volume without malicious indicators). Unknown hash/publisher ⇒ warning. — [SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- Certificate table (Microsoft, updated 2026-05): Microsoft Store ⇒ no warning; valid OV/EV ⇒ "unrecognized" warning until reputation accumulates (publisher name **is** shown); no signature ⇒ "Windows protected your PC", Run anyway required; **self-signed ⇒ identical to unsigned**. **"EV certificates no longer bypass SmartScreen… Paying a premium for EV solely to avoid SmartScreen warnings is no longer justified."** — [same doc](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- Reputation builds automatically; no exact threshold; "several weeks and hundreds of clean installs from a wide audience." No need/mechanism for manual SmartScreen review for consumer endpoints; enterprise can submit to [Microsoft Security Intelligence portal](https://www.microsoft.com/en-us/wdsi/filesubmission) to accelerate trust for managed deployments. — [same doc](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- Unsigned reputation is per-file — every new version starts at zero reputation. Signed files can inherit certificate reputation across versions ⇒ **always sign releases with the same cert**. — [same doc](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- Smart App Control (Windows 11) may supersede SmartScreen: can block unsigned executables outright; applies to all executables, not just downloads. — [same doc](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- Real-world small-vendor experience (2026-06 blog): reputation resets each release cycle in practice; "scary" popup pattern; users must click *More info → Run anyway*. — [Markdown Monster / West Wind blog, Jun 2026](https://markdownmonster.west-wind.com/blog/posts/2026/Jun/01/Windows-Protected-your-PC-Dealing-with-Windows-SmartScreen-on-Installation)
- Artifact Signing is Microsoft's recommended non-Store path; reputation accumulates over time regardless of which cert you use (no instant trust). — [SmartScreen doc, Artifact Signing section](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- False positive submission workflow: Microsoft Security Intelligence portal (wdsi/filesubmission) → "submit a file/detection" → Microsoft reclassifies; typical for heuristic blunders on small-vendor exes; also report via the File Submission form referenced in enterprise section of the SmartScreen doc. (URL from SmartScreen doc.)

### Inferences
- For ≤5 family PCs you control, SmartScreen friction is nearly a non-issue if you control the machines: deploy via a one-time elevated install (Inno `/VERYSILENT` from CI-time elevation, or Intune/GPO/PDQ elsewhere — each out of scope but standard) and SmartScreen never gates the service because it isn't a web download. SmartScreen only bites the *first* download of the installer from your server; after elevation-time install or Mark-of-the-Web removal (`Unblock-File`), no prompts.
- If distributing the installer via browser download: sign every release, keep the same cert, expect first-run warnings for new versions for a while; communicate "More info → Run anyway" to family members; pre-seed reputation by downloading it on each family PC before rolling the service out.
- Negative reputation is a real risk if the tool trips "potentially unwanted application" heuristics (keyloggers/parents-monitoring overlap!) — file a wdsi submission early if flagged; avoid packed/obfuscated builds.

### Gaps
- No public data on how long Artifact-Signing-signed individual apps take to clear SmartScreen in 2026 (Microsoft gives no threshold).
- Co-signing (dual signatures) exists in Authenticode (sha1+sha256 legacy) but provides no SmartScreen benefit; not investigated deeply.

---

## Auto-update architecture borrowing from APK OTA: version.json, SHA256SUMS, signature verification, transactional swap, service restart, rollback

### Takeaway
No first-party .NET equivalent exists; the proven pattern (Tauri updater, Rust self_update, APK OTA) is: service polls a static `version.json` on your server → downloads artifact + SHA256SUMS (+ optional detached signature) → verifies → renames the *running* exe aside (rename-on-lock works on Windows even for running images) → installs new exe → restarts service via SCM → rolls back if the new exe fails a post-start health check.

### Cited Findings
- Tauri updater: static JSON manifest (`latest.json`) with `version`, `pub_date`, per-platform `{url, signature}`; app holds an ed25519 pubkey and refuses artifacts whose signature doesn't match — i.e., **signature verification is mandatory and independent of Authenticode**. — [Tauri v1 updater docs](https://v1.tauri.app/v1/guides/distribution/updater/#server-support); [Tauri v2 updater](https://v2.tauri.app/plugin/updater/)
- Tauri Windows install modes: `passive` (no user interaction) vs `basicUi`/`quiet`; updates typically run the installer in passive mode on Windows, not a raw binary swap — i.e., even mature updaters delegate to an installer rather than hot-swap the exe. — [Tauri v2 updater, Windows installMode](https://v2.tauri.app/plugin/updater/)
- Rust `self_update` crate: checks GitHub Releases, downloads asset, verifies SHA-256 digest from the release metadata before replacing the binary. (Referenced from crate ecosystem; specific docs not fetched — see Gaps.)
- Authenticode signature on the exe *itself* already binds publisher identity; a separate SHA256SUMS + detached signature (or Authenticode verification via `WinVerifyTrust`) is defense-in-depth against truncated/mitm'd downloads from your own server.

### Inferences (design, no single source)
Recommended shape for `jarvis-guard`:
1. CI publishes `jarvis-guard-<version>.exe`, `SHA256SUMS` (sha256 of exe), and `version.json` (`{version, url, sha256, "authenticode": true}`) to the family server over HTTPS (or via WebDAV/rsync over SSH).
2. Updater service (LocalSystem) on schedule: GET version.json; if newer, GET exe to temp dir; verify SHA-256 vs `SHA256SUMS`; verify Authenticode (e.g., `signtool verify /pa /v` or WinVerifyTrust) — refuse on mismatch; optionally verify an ed25519 detached signature if you want APK-style mandatory signature semantics.
3. Transactional swap: `RenameFile("jarvis-guard.exe", "jarvis-guard.exe.bak")` — legal on Windows for a *running* exe (the running image keeps a handle to the renamed file; the path is freed). Copy new exe into place. `sc.exe stop JarvisGuard` / `Start-Service` or `net stop/start`. Health check: new service sets `HKLM\SOFTWARE\...\LastGoodVersion` or a pipe/HTTP ping within N seconds; on failure or crash-loop, restore `.bak`, restart, alert parent via chosen channel, and pin `version.json` offered version or write a rollback sentinel.
4. Keep updater **outside** the updatable exe (tiny stable bootstrap service, rarely updated) to avoid self-bricking — the classic APK "updater as part of an unprivileged SystemUI" split.
5. Mark-of-the-Web: files downloaded by a service typically don't carry MOTW; irrelevant for a service path, but `Unblock-File` semantics apply if a human downloads the installer.

Practical notes: do the swap in the **service** (session 0, elevated), not the tray app; tray app just notifies "update installed, click to restart." Rollback on failed start: SCM recovery actions (`sc.exe failure JarvisGuard reset= 86400 actions= restart/60000/restart/60000/restart/60000`) + the `.bak` restore gives belt-and-braces.

### Gaps
- No fetched primary source for Rust `self_update` internals (general knowledge).
- No published .NET-native auto-update library endorsed by Microsoft for this scenario (ClickOnce exists but is Store/enterprise-flavored and ill-suited to a self-hosted family server + tray + service combo; not investigated deeply — gap).
- No benchmark data on swap-on-running-exe reliability across 20H2→11 25H2 (widely documented behavior in forums; no authoritative doc fetched).

---

## Windows service installation from CI's artifact: Inno Setup with /VERYSILENT, direct sc.exe, silent uninstall, ACL hardening

### Takeaway
Inno Setup is the standard free path; `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=` gives fully unattended installs and installs services via `[Run]` (or a custom `[Code]` `AfterInstall`/`InitializeSetup`) or `sc.exe`. Direct `sc.exe create` works for a bare-exe service but you lose uninstaller/repair/upgrade UX. ACL-harden install dir (service exe SYSTEM:FullControl, Users:ReadExecute only), never leave world-writable updaters.

### Cited Findings
- Inno `/SP-`, `/SILENT` (progress window), `/VERYSILENT` (no UI), `/NOICONS`, `/LOG="C:\path.log"`, `/SUPPRESSMSGBOXES`, `/NORESTART` — [Inno Setup command-line parameters](https://jrsoftware.org/ishelp/topic_setupcmdline.htm); silent uninstall switches identical — [Inno Setup uninstaller parameters](https://jrsoftware.org/ishelp/topic_uninstcmdline.htm)
- Fully silent invocation that works in Intune-type automation: `setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\Logs\app.log"` — [Splashtop silent-install reference](https://www.splashtop.com/blog/silent-install-exe)
- Inno supports services via `[Run]` post-install commands or `[Code]` sections; common pattern is `sc.exe stop/delete/create/start` or registering via `service --install` hook supported by generic .NET service templates. (Pattern known widely; only the Inno CLI docs were fetched — see Gaps.)

### Inferences (design)
- Recommended: build the installer in CI (`iscc.exe` on the windows runner — Inno is preinstalled? **Not by default on GitHub images; install via `choco install innosetup`** — choco is present on windows runners; gap on whether VS2026 image preinstalls Inno — assume not, use choco/winget). Steps: publish → sign exe → sign the **setup.exe** itself with same cert (Inno output is also a PE and needs Authenticode for SmartScreen) → attach to release → CI curls it to family server.
- `sc.exe create jarvis-guard binPath= "\"C:\Program Files\jarvis-guard\jarvis-guard.exe\" --service" start= auto DisplayName= "Jarvis Guard"` then `sc.exe failure ...` for restart-on-crash. Quote `binPath=` carefully (trailing space after `=` is mandatory: `binPath= "..."`).
- ACL hardening: `icacls "C:\Program Files\jarvis-guard" /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "Users:(OI)(CI)RX" /T`. Deny write for `Users` so unprivileged kids cannot swap or delete the agent. Protect the update channel: only the updater service (running as SYSTEM) writes the install dir; tray app runs as the child — verifies, never writes.
- Uninstall: `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES` (per-machine) — Inno auto-generates; ensure `[UninstallRun]` stops/deletes the service first.
- If you want zero-installer: `sc.exe` + pre-staged exe copied by CI still requires an elevated bootstrap on each PC — the one-time cost Inno wraps nicely including service registration, Start Menu/tray shortcut, and Add/Remove Programs entry.

### Gaps
- Whether `windows-2025`/`windows-2025-vs2026` images preinstall Inno Setup (likely not; choco/winget step needed) — not verified.
- Inno `[Run]`/`[Code]` service-registration example not fetched from jrsoftware docs (ishelp has them but not read).
- No source on ACLing the update staging dir specifically for this scenario — standard Windows ACL practice, unsourced.

---

## Common pitfalls: timestamp servers, signing CI flakiness, cert expiry mid-flight

### Takeaway
Use RFC 3161 with `/tr http://timestamp.acs.microsoft.com /td SHA256` (or timestamp.digicert.com); **never** mix legacy `/t` with RFC3161; HTTPS timestamp URLs are rejected by current signtool — use `http://`. Timestamp flakiness is the #1 CI failure mode; retry, and pick a reachable server. Cert expiry mid-flight: timestamped signatures remain valid after cert expiry — an expired cert with a valid RFC 3161 timestamp still verifies. That's the whole point of timestamping.

### Cited Findings
- DigiCert timestamping: RFC 3161 URL `http://timestamp.digicert.com`; signtool usage `/tr http://timestamp.digicert.com /td SHA256`. — [DigiCert KB: Troubleshooting Timestamping Problems](https://knowledge.digicert.com/solution/troubleshooting-timestamping-problems)
- Timestamp server reachability failures are a recurring CI/build failure mode: "The specified timestamp server either could not be reached or returned an invalid response" (choco build issue). — [chocolatey/choco#2293](https://github.com/chocolatey/choco/issues/2293)
- SHA-384/SHA-512 timestamp digests stopped being honored by timestamp.digicert.com (May 2025); SHA-256 works; sectigo's RFC3161 server accepts more digests. — [gist fd754e... comment thread](https://gist.github.com/Manouchehri/fd754e402d98430243455713efada710)
- signtool SDK 10.0.26100.0 **rejects RFC 3161 HTTPS timestamp URLs** (isolated GitHub-hosted Server 2022, OS 10.0.20348.0) — use `http://` URLs (RFC3161 timestamps are themselves integrity-protected; TLS not required). — [Microsoft Q&A 6033027](https://learn.microsoft.com/en-sg/answers/questions/6033027/signtool-sdk-10-0-26100-0-rejects-rfc-3161-https-u)
- Artifact Signing action pins `timestamp-rfc3161: http://timestamp.acs.microsoft.com` and `timestamp-digest: SHA256` — Microsoft's own action uses http://. — [Azure/artifact-signing-action](https://github.com/Azure/artifact-signing-action)
- **CODLL / Authenticode**: signature must cover every signed PE; modified-after-signing binaries break (see SmartScreen doc "Do not modify signed files"). — [SmartScreen doc](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation). (Unsigned dependencies inside a signed installer are common and tolerated; sign what you ship.)
- Authenticode OID for code signing: legacy SHA1 1.3.6.1.5.5.7.3 was deprecated; current certs use the same EKU `1.3.6.1.5.5.7.3.3` (Code Signing) with SHA-256 signatures + RFC 3161 countersignature instead of the legacy `/t` (which used the older PKCS#7 timestamp). *Note: the assignment's "1.3.6.1.5.5.7.3" is the EKU prefix; the relevant distinctions are signature digest (SHA256) and timestamp protocol (RFC 3161 vs legacy).* No primary source fetched confirming the legacy-OID retirement date — flag as unverified specifics; Microsoft/DigiCert docs above corroborate "always use /tr + /td SHA256, never /t".
- Cert expiry mid-flight: **timestamped** Authenticode signatures remain valid after signing cert expiry (timestamp proves signing occurred during validity). Untimestamped signatures die with the cert. Not re-verified via a fetched Microsoft doc, but this is the documented purpose of RFC 3161 countersignature — *partially unsourced in this session (see Gaps)*; practically: never sign CI artifacts without a timestamp.
- Identity-validation churn for Artifact Signing: changing any identity detail forces a **new identity validation** (1–20 business days) and issues new certs — changing the signing identity destroys accumulated SmartScreen publisher reputation (keep identity stable). — [Quickstart, identity validation notes](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart); [SmartScreen doc, consistent identity](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

### Inferences
- CI hardening for timestamps: (1) use `/td SHA256` only; (2) choose timestamp host with best reachability from Azure runners (`timestamp.acs.microsoft.com` ties into Artifact Signing; `timestamp.digicert.com` is the general default); (3) wrap sign step in retry (`for i in 1..3; signtool ... /tr ...`); (4) if timestamp fails but signing succeeded, **do not ship untimestamped** — fail the build; (5) validate with `signtool verify /pa /v /tw` in CI before upload.
- Cert expiry: Artifact Signing certificates are short-lived (issued per profile, zero-touch renewal) — Microsoft handles renewal inside HSM; your action keeps working as long as the profile exists. OV/EV certs expire annually — calendar it; re-timestamping old releases isn't possible, but old releases remain valid thanks to timestamps.
- "What breaks if cert expires mid-flight": new signings stop (or fall back to the renewed cert automatically under Artifact Signing); **already-shipped timestamped files stay valid**; SmartScreen publisher reputation may be diluted by identity change (new cert ⇒ new publisher signal until accumulated).

### Gaps
- No fetched Microsoft doc explicitly stating "timestamped signature survives cert expiry" (knowledge-standard; not cited inline).
- Exact CI flake rate / SLA for `timestamp.acs.microsoft.com` and DigiCert's RFC3161 endpoint (no public stats).
- Legacy 1.3.6.1.5.5.7.3 vs RFC3161 EKU specifics not re-verified against Microsoft Learn in this session — treated cautiously above.

---

## Bottom line for the family-PC scenario (synthesis)

1. Build: `dotnet publish -r win-x64 --self-contained true -p:PublishSingleFile=true` on `windows-latest` (= Server 2025), pinned `setup-dotnet`, ReadyToRun on, trimming off, net8.0→move to net10.0 window (net8.0 LTS ended Nov 2026).
2. Sign: Azure Artifact Signing Basic $9.99/mo if parent is a US/Canada individual with Individual billing account (OIDC, `azure/artifact-signing-action@v2`, `http://timestamp.acs.microsoft.com /td SHA256`). Else SignPath Foundation (free, but SignPath-publisher name + manual approval + code-signing-policy page on your repo/site). OV ~$99/yr as fallback outside eligibility. Never self-sign for distribution.
3. Attest: `actions/attest-build-provenance` + SBOM via `actions/attest` (public repo; attestations free on GitHub Free for public repos).
4. Publish: curl/scp signed exe + `SHA256SUMS` + `version.json` to the family server from CI; attach to GitHub Release too.
5. Update: stable bootstrap service; version.json poll; SHA256 + Authenticode (WinVerifyTrust) verify; rename-on-lock swap; SCM stop/start; `.bak` rollback on health-check failure.
6. Install: Inno Setup `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`; sign setup.exe too; ACL the install dir (SYSTEM/Admins full, Users RX); children never have write; updater runs as SYSTEM.
7. SmartScreen: expect first-download warnings; they decay with volume; no manual review queue for consumer endpoints; EV is pointless; communicate "More info → Run anyway" and/or pre-unblock (`Unblock-File`) on the family PCs; watch for "PUA" false positives due to parental-control heuristics and file early with wdsi.
