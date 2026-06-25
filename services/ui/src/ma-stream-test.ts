/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-unused-vars */
import { SendspinPlayer } from '@sendspin/sendspin-js'

// Expose for the HTML page to use
;(window as any).SendspinPlayer = SendspinPlayer
console.log('[MA-Test] SendspinPlayer loaded:', typeof SendspinPlayer)

// ═══════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════
let sendspinWs: WebSocket | null = null
let jsonrpcWs: WebSocket | null = null
let player: SendspinPlayer | null = null
const audio: HTMLAudioElement | null = null
let playerId = ''
let msgIdCounter = 0
const reconnectAttempts = 0
const logsEnabled = { sendspin: true, jsonrpc: true }
let audioElement: HTMLAudioElement | null = null
let heartbeatInterval: ReturnType<typeof setInterval> | null = null

// ═══════════════════════════════════════════════════════════
// DOM refs
// ═══════════════════════════════════════════════════════════
const logEl = document.getElementById('log') as HTMLElement
const statusEl = document.getElementById('connection-status') as HTMLElement
const apiTokenInput = document.getElementById('api-token') as HTMLInputElement
const btnConnect = document.getElementById('btn-connect') as HTMLButtonElement
const btnDisconnect = document.getElementById('btn-disconnect') as HTMLButtonElement
const btnReconnect = document.getElementById('btn-reconnect') as HTMLButtonElement
const btnClearLog = document.getElementById('btn-clear-log') as HTMLButtonElement
   const btnToggleSendspin = document.getElementById('btn-toggle-sendspin') as HTMLButtonElement
    const btnToggleJsonrpc = document.getElementById('btn-toggle-jsonrpc') as HTMLButtonElement
    const btnListPlayers = document.getElementById('btn-list-players') as HTMLButtonElement
    const btnListQueues = document.getElementById('btn-list-queues') as HTMLButtonElement
    const btnTestPlayerQuery = document.getElementById('btn-test-player-query') as HTMLButtonElement
    const btnRegisterPlayer = document.getElementById('btn-register-player') as HTMLButtonElement
    const btnPlay = document.getElementById('btn-play') as HTMLButtonElement
const btnPause = document.getElementById('btn-pause') as HTMLButtonElement
const btnPlayUri = document.getElementById('btn-play-uri') as HTMLButtonElement
const volumeSlider = document.getElementById('volume-slider') as HTMLInputElement
const volumeValue = document.getElementById('volume-value') as HTMLSpanElement
const searchInput = document.getElementById('search-input') as HTMLInputElement
const searchDomain = document.getElementById('search-domain') as HTMLSelectElement
const btnSearch = document.getElementById('btn-search') as HTMLButtonElement
  const searchResults = document.getElementById('search-results') as HTMLElement
    const uriInput = document.getElementById('uri-input') as HTMLInputElement
    const debugOutput = document.getElementById('debug-output') as HTMLElement
    const audioPlayer = document.getElementById('audio-player') as HTMLAudioElement

// ═══════════════════════════════════════════════════════════
// Logging
// ═══════════════════════════════════════════════════════════
function log(msg: string, type: string = 'info', raw?: any) {
    if (type === 'error' && !logsEnabled['errors']) return
    if (raw && !logsEnabled[raw as keyof typeof logsEnabled]) return

    const time = new Date().toISOString().split('T')[1].slice(0, -1)
    const entry = document.createElement('div')
    entry.className = 'log-entry'
    let content = `<span class="log-time">[${time}]</span>`

    if (raw) {
        const tag = raw === 'sendspin' ? '<span class="tag tag-sendspin">SPIN</span>' :
                    raw === 'jsonrpc' ? '<span class="tag tag-jsonrpc">JSON</span>' :
                    '<span class="tag tag-ws">WS</span>'
        content += tag
        if (typeof raw === 'object' && (raw as any).frameType === 'send') {
            content += `<span class="log-info"> SENT</span>`
            content += ` ${escapeHtml(typeof (raw as any).data === 'string' ? (raw as any).data.substring(0, 500) : `<binary ${(raw as any).data.byteLength}B>`)}`
        } else {
            content += `<span class="log-info"> RECV</span>`
            content += ` ${escapeHtml(typeof raw === 'string' ? raw.substring(0, 500) : `<binary ${(raw as any).byteLength}B>`)}`
        }
    } else {
        content += `<span class="log-${type}"> ${msg}</span>`
    }

    entry.innerHTML = content
    logEl.appendChild(entry)
    logEl.scrollTop = logEl.scrollHeight

    while (logEl.children.length > 1000) {
        logEl.removeChild(logEl.firstChild)
    }
}

function escapeHtml(str: string): string {
    const div = document.createElement('div')
    div.textContent = str
    return div.innerHTML
}

function setStatus(msg: string, type: string = 'info') {
    statusEl.innerHTML = `<div class="status ${type}">${msg}</div>`
}

function updateConnectionStatus(sendspinReady: boolean, jsonrpcReady: boolean) {
    const all = sendspinReady && jsonrpcReady
    btnConnect.disabled = all
    btnDisconnect.disabled = !all
    btnReconnect.disabled = !all
    btnPlay.disabled = !all
    btnPause.disabled = !all
    btnPlayUri.disabled = !all

    if (!sendspinReady && !jsonrpcReady) {
        setStatus('Disconnected', 'info')
    } else if (all) {
        setStatus('Connected — sendspin + JSON-RPC active', 'active')
    } else if (sendspinReady) {
        setStatus('Sendspin connected — waiting for JSON-RPC', 'warn')
    } else {
        setStatus('JSON-RPC connected — waiting for sendspin', 'warn')
    }
}

function getPlayerId(): string {
    let id = localStorage.getItem('sendspin_webplayer_id')
    if (!id) {
        const arr = new Uint32Array(10)
        crypto.getRandomValues(arr)
        id = arr.join('')
        localStorage.setItem('sendspin_webplayer_id', id)
    }
    return id
}

// ═══════════════════════════════════════════════════════════
// JSON-RPC
// ═══════════════════════════════════════════════════════════
function sendJsonRpc(command: string, args: any, expectResult: boolean = true): Promise<any> {
    return new Promise((resolve, reject) => {
        if (!jsonrpcWs || jsonrpcWs.readyState !== WebSocket.OPEN) {
            reject(new Error('JSON-RPC WebSocket not connected'))
            return
        }

        const id = `counter${msgIdCounter + 1}`
        msgIdCounter++

        const payload = JSON.stringify({
            message_id: id,
            command: command,
            args: args,
        })

        log(`JSON-RPC: ${command}`, 'info', { frameType: 'send', data: payload })

        // For fire-and-forget commands, don't wait for result
        if (!expectResult) {
            jsonrpcWs.send(payload)
            return
        }

        const timeout = setTimeout(() => {
            reject(new Error(`JSON-RPC ${command} timed out`))
        }, 10000)

        const onMessage = (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data)
                if (data?.message_id === id && data?.type === 'RESULT') {
                    clearTimeout(timeout)
                    jsonrpcWs!.removeEventListener('message', onMessage)
                    resolve(data.result)
                }
                if (data?.message_id === id && data?.error) {
                    clearTimeout(timeout)
                    jsonrpcWs!.removeEventListener('message', onMessage)
                    reject(new Error(`JSON-RPC error: ${JSON.stringify(data.error)}`))
                }
            } catch { /* not our response */ }
        }

        jsonrpcWs.addEventListener('message', onMessage)
        jsonrpcWs.send(payload)
    })
}

// ═══════════════════════════════════════════════════════════
// Connect
// ═══════════════════════════════════════════════════════════
async function connect() {
    const urlParams = new URLSearchParams(window.location.search)
    const urlToken = urlParams.get('token')?.trim()
    if (urlToken) {
        apiTokenInput.value = urlToken
    }

    let token = localStorage.getItem('jarvis_api_key')
    if (!token && urlToken) {
        token = urlToken
    }
    if (!token && apiTokenInput?.value) {
        token = apiTokenInput.value.trim()
    }
    if (!token) {
        setStatus('No token — login to UI first, pass ?token=xxx in URL, or enter API key above', 'error')
        return
    }

    playerId = getPlayerId()
    document.getElementById('info-playerId')!.textContent = playerId
    log(`[MAWebPlayer] initPlayer called`, 'info')
    log(`[MAWebPlayer] playerId: ${playerId}`, 'info')

    setStatus('Connecting...', 'warn')

    try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
        const sendspinUrl = `${wsProtocol}://${window.location.host}/api/sendspin?token=${encodeURIComponent(token)}`
        const jsonrpcUrl = `${wsProtocol}://${window.location.host}/api/ma-jsonrpc?token=${encodeURIComponent(token)}`

        log(`[MAWebPlayer] Sendspin URL: ${sendspinUrl.substring(0, 60)}...`, 'info')
        log(`[MAWebPlayer] JSON-RPC URL: ${jsonrpcUrl.substring(0, 60)}...`, 'info')

        audioElement = audioPlayer

        // ── Create SendspinPlayer with an explicit tokenized WebSocket ──
        log(`[MAWebPlayer] Creating SendspinPlayer...`, 'info')
        sendspinWs = new WebSocket(sendspinUrl)
        player = new SendspinPlayer({
            playerId: playerId,
            webSocket: sendspinWs,
            audioElement: audioElement,
            onStateChange: (state) => {
                console.log('[SendspinPlayer] State change:', state)
                if (logsEnabled.sendspin) {
                    const prevState = player?.isConnected ? (state.isPlaying ? 'playing' : 'idle') : 'disconnected'
                    const newState = state.isPlaying ? 'playing' : 'idle'
                    if (prevState !== newState) {
                        log(`[MAWebPlayer] State: ${prevState} → ${newState}`, 'info')
                    }
                    if (state.currentStreamFormat) {
                        const fmt = state.currentStreamFormat
                        log(`[MAWebPlayer] Stream: ${fmt.codec} ${fmt.sample_rate}Hz ${fmt.channels}ch ${fmt.bit_depth}bit`, 'success')
                    }
                    if (state.serverState?.metadata?.title) {
                        log(`[MAWebPlayer] Now playing: ${state.serverState.metadata.title}`, 'success')
                    }
                }
                setState({
                    isPlaying: state.isPlaying,
                    volume: Math.round(state.volume),
                    muted: state.muted,
                    playerState: state.isPlaying ? 'playing' : (state.isConnected ? 'idle' : null),
                    mediaTitle: state.serverState?.metadata?.title || null,
                    mediaArtist: state.serverState?.metadata?.artist || null,
                    duration: state.serverState?.metadata?.duration || 0,
                    position: state.serverState?.position || 0,
                })
            },
        })

        // Connect SendspinPlayer (this creates the WebSocket and sends client/hello)
        // The gateway intercepts client/hello and sends auth to MA on behalf of the client
        await player.connect()
        sendspinWs = player.core.wsManager.ws as unknown as WebSocket
        log(`[MAWebPlayer] SendspinPlayer connected`, 'success')

        // ── JSON-RPC WebSocket (control) ──
        jsonrpcWs = new WebSocket(jsonrpcUrl)

        jsonrpcWs.onopen = () => {
            log(`[MAWebPlayer] JSON-RPC proxy connected`, 'info')
            updateConnectionStatus(sendspinWs && sendspinWs.readyState === WebSocket.OPEN, true)
            startHeartbeat()
        }

        jsonrpcWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data)

                if (logsEnabled.jsonrpc) {
                    const type = data?.type
                    const msgId = data?.message_id
                    const preview = JSON.stringify(data).substring(0, 200)
                    log(`[MAWebPlayer] JSON-RPC ${type || 'EVENT'} ${msgId || ''}`, 'info', { frameType: 'recv', data: preview })
                }

                // Handle MA events: queue_updated, player_updated
                if (data?.event === 'queue_updated') {
                    handleQueueUpdated(data.data)
                }
                if (data?.event === 'player_updated') {
                    handlePlayerUpdated(data.data)
                }
            } catch { /* non-JSON, ignore */ }
        }

        jsonrpcWs.onclose = (event) => {
            log(`[MAWebPlayer] JSON-RPC closed: code=${event.code} reason=${event.reason || 'none'}`, event.code === 1000 ? 'success' : 'warn')
            stopHeartbeat()
            updateConnectionStatus(sendspinWs && sendspinWs.readyState === WebSocket.OPEN, false)
        }

        jsonrpcWs.onerror = () => {
            log(`[MAWebPlayer] JSON-RPC proxy error`, 'error')
        }

        // MA's websocket API expects command messages only; do not inject ad-hoc pings.
        const startHeartbeat = () => {
            clearInterval(heartbeatInterval!)
            heartbeatInterval = null
        }
        const stopHeartbeat = () => {
            clearInterval(heartbeatInterval!)
            heartbeatInterval = null
        }

        // Wait for connections
        const connTimeout = 15000
        const start = Date.now()
        while (Date.now() - start < connTimeout) {
            const sReady = sendspinWs && sendspinWs.readyState === WebSocket.OPEN
            const jReady = jsonrpcWs && jsonrpcWs.readyState === WebSocket.OPEN
            if (sReady && jReady) {
                log(`[MAWebPlayer] Both WebSocket connections established`, 'success')
                break
            }
            await new Promise(r => setTimeout(r, 500))
        }

        setState({
            isConnected: true,
            playerState: 'idle',
            volume: 70,
            muted: false,
            isPlaying: false,
            mediaTitle: null,
            mediaArtist: null,
            position: 0,
            duration: 0,
        })

        setStatus('Connected — sendspin + JSON-RPC active', 'success')
        log(`[MAWebPlayer] Player initialized and connected`, 'success')

    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        log(`[MAWebPlayer] Init failed: ${msg}`, 'error')
        setStatus(`Init failed: ${msg}`, 'error')
    }
}

// ═══════════════════════════════════════════════════════════
// Disconnect
// ═══════════════════════════════════════════════════════════
function disconnect() {
    log(`[MAWebPlayer] disconnect called`, 'info')
    stopHeartbeat()
    if (audioElement) {
        audioElement.pause()
        audioElement.src = ''
    }
    player?.disconnect()
    player = null
    sendspinWs?.close(1000, 'shutdown')
    sendspinWs = null
    jsonrpcWs?.close(1000, 'shutdown')
    jsonrpcWs = null
    updateConnectionStatus(false, false)
    setState({
        isConnected: false,
        playerState: null,
        isPlaying: false,
    })
    setStatus('Disconnected', 'info')
    log(`[MAWebPlayer] Player shutdown complete`, 'info')
}

function reconnect() {
    log(`[MAWebPlayer] Reconnecting...`, 'info')
    disconnect()
    setTimeout(() => connect(), 1000)
}

// ═══════════════════════════════════════════════════════════
// JSON-RPC control methods
// ═══════════════════════════════════════════════════════════
async function cmdPlay() {
    log(`[MAWebPlayer] cmdPlay called, player: ${playerId}`, 'info')
    try {
        player?.sendCommand('play')
    } catch (err) {
        log(`[MAWebPlayer] cmd_play failed: ${(err as Error).message}`, 'error')
    }
}

async function cmdPause() {
    log(`[MAWebPlayer] cmdPause called, player: ${playerId}`, 'info')
    try {
        player?.sendCommand('pause')
    } catch (err) {
        log(`[MAWebPlayer] cmd_pause failed: ${(err as Error).message}`, 'error')
    }
}

async function setVolumeVolume(volume: number) {
    log(`[MAWebPlayer] setVolume called: ${volume}`, 'info')
    try {
        if (audioElement) {
            audioElement.volume = volume / 100
        }
        player?.setVolume(volume)
    } catch (err) {
        log(`[MAWebPlayer] setVolume failed: ${(err as Error).message}`, 'error')
    }
}

// ═══════════════════════════════════════════════════════════
// playMedia
// ═══════════════════════════════════════════════════════════
async function playMedia(mediaUri: string) {
    log(`[MAWebPlayer] play_media: ${mediaUri.substring(0, 80)}..., player: ${playerId}`, 'info')
    try {
        // Use player_queues/play_media (the MA web player pattern) with queue_id
        const playResult = await sendJsonRpc('player_queues/play_media', {
            queue_id: playerId,
            media: mediaUri,
            custom_data: { source_change: false },
        }, true)
        log(`[MAWebPlayer] play_media response: ${JSON.stringify(playResult).substring(0, 200)}`, 'info')
        // Start local browser playback via Sendspin
        player?.sendCommand('play')
        log(`[MAWebPlayer] play_media + cmd_play sent`, 'success')
    } catch (err) {
        log(`[MAWebPlayer] play_media failed: ${(err as Error).message}`, 'error')
        // Fallback: try players/play_media as backup
        try {
            log(`[MAWebPlayer] Trying fallback: players/play_media`, 'warn')
            const playResult = await sendJsonRpc('players/play_media', {
                player_id: playerId,
                media: mediaUri,
                play_handle: null,
                enqueue: 'play',
            }, true)
            log(`[MAWebPlayer] Fallback play_media response: ${JSON.stringify(playResult).substring(0, 200)}`, 'info')
            player?.sendCommand('play')
        } catch (fallbackErr) {
            log(`[MAWebPlayer] Fallback also failed: ${(fallbackErr as Error).message}`, 'error')
        }
    }
}

// ═══════════════════════════════════════════════════════════
// Search
// ═══════════════════════════════════════════════════════════
async function searchMedia(query: string) {
    const token = localStorage.getItem('jarvis_api_key')
    if (!token) return

    const domain = searchDomain.value
    let url: string
    let resultsKey = 'results'

    if (domain === 'music_assistant') {
        url = `/api/media/music-assistant/search?query=${encodeURIComponent(query)}&limit=20`
    } else {
        url = `/api/media/audiobookshelf/search?q=${encodeURIComponent(query)}&limit=20`
        resultsKey = 'books'
    }

    try {
        log(`[MAWebPlayer] Searching ${domain}: ${query}`, 'info')
        const resp = await fetch(url, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        const results = data?.[resultsKey] || []
        log(`[MAWebPlayer] Search returned ${results.length} results from ${domain}`, results.length ? 'success' : 'warn')
        renderSearchResults(results, domain)
    } catch (err) {
        log(`[MAWebPlayer] Search failed: ${(err as Error).message}`, 'error')
    }
}

function renderSearchResults(results: any[], domain: string) {
    searchResults.innerHTML = ''
    if (!results.length) {
        searchResults.innerHTML = '<div style="padding: 0.5rem; color: #64748b; font-size: 0.8rem;">No results found</div>'
        return
    }

    results.forEach(r => {
        const div = document.createElement('div')
        div.className = 'search-item'
        const uri = r.uri || r.id || ''
        const name = domain === 'music_assistant' ? (r.name || uri) : (r.title || uri)
        const meta = domain === 'music_assistant' ? (r.artist || r.type || '') : (r.author || r.narrator || '')
        div.innerHTML = `
            <div>
                <div class="search-item-name">${escapeHtml(name)}</div>
                <div class="search-item-meta">${escapeHtml(meta)}${r.duration ? ` · ${Math.round(r.duration / 60)}m` : ''}</div>
            </div>
            <button class="search-item-btn" onclick="playFromSearch('${uri.replace(/'/g, "\\'")}')">▶ Play</button>
        `
        searchResults.appendChild(div)
    })
}

async function playFromSearch(uri: string) {
    if (!uri) {
        log('No URI to play', 'error')
        return
    }
    log(`[MAWebPlayer] Play from search: ${uri.substring(0, 60)}...`, 'info')
    if (!player || !sendspinWs || sendspinWs.readyState !== WebSocket.OPEN || !jsonrpcWs || jsonrpcWs.readyState !== WebSocket.OPEN) {
        log(`[MAWebPlayer] Initializing player before search playback...`, 'info')
        await connect()
    }
    await playMedia(uri)
}

;(window as any).playFromSearch = playFromSearch

// ═══════════════════════════════════════════════════════════
// MA Event handlers
// ═══════════════════════════════════════════════════════════
function handleQueueUpdated(data: any) {
    log(`[MAWebPlayer] MA event: queue_updated`, 'info')
    const current = data?.current_item
    if (current) {
        setState({
            mediaTitle: current.name || null,
            mediaArtist: current.artist || null,
            duration: current.duration || 0,
        })
        document.getElementById('info-title')!.textContent = current.name || '-'
        document.getElementById('info-artist')!.textContent = current.artist || '-'
        document.getElementById('info-duration')!.textContent = `${Math.round(current.duration || 0)}s`
    }

    const queueState = data?.state
    if (queueState === 'playing') setState({ isPlaying: true })
    else if (queueState === 'paused' || queueState === 'idle') setState({ isPlaying: false })
}

function handlePlayerUpdated(data: any) {
    log(`[MAWebPlayer] MA event: player_updated`, 'info')
    if (data?.volume_level !== undefined) {
        const vol = Math.round(data.volume_level * 100)
        setState({ volume: vol })
        volumeSlider.value = vol
        volumeValue.textContent = vol
        document.getElementById('info-volume')!.textContent = `${vol}%`
    }
    if (data?.is_volume_muted !== undefined) {
        setState({ muted: data.is_volume_muted })
        document.getElementById('info-muted')!.textContent = data.is_volume_muted ? 'Yes' : 'No'
    }
    if (data?.position !== undefined) {
        setState({ position: data.position })
        document.getElementById('info-position')!.textContent = `${Math.round(data.position)}s`
        updateProgress()
    }
    if (data?.duration !== undefined) {
        setState({ duration: data.duration })
    }
}

function updateProgress() {
    const pos = document.getElementById('info-position')!.textContent
    const dur = document.getElementById('info-duration')!.textContent
    const pNum = parseFloat(pos) || 0
    const dNum = parseFloat(dur) || 0
    const pct = dNum > 0 ? Math.round((pNum / dNum) * 100) : 0
    document.getElementById('info-progress')!.textContent = `${pct}%`
}

// ═══════════════════════════════════════════════════════════
// State tracker
// ═══════════════════════════════════════════════════════════
function setState(updates: any) {
    if (updates.isConnected !== undefined) {
        updateConnectionStatus(updates.isConnected, updates.isConnected)
    }
    if (updates.isPlaying !== undefined) {
        document.getElementById('info-playing')!.textContent = updates.isPlaying ? 'Yes' : 'No'
    }
    if (updates.playerState !== undefined) {
        document.getElementById('info-state')!.textContent = updates.playerState || '-'
    }
    if (updates.mediaTitle !== undefined) {
        document.getElementById('info-title')!.textContent = updates.mediaTitle || '-'
    }
    if (updates.mediaArtist !== undefined) {
        document.getElementById('info-artist')!.textContent = updates.mediaArtist || '-'
    }
    if (updates.duration !== undefined) {
        document.getElementById('info-duration')!.textContent = `${Math.round(updates.duration || 0)}s`
    }
    if (updates.position !== undefined) {
        document.getElementById('info-position')!.textContent = `${Math.round(updates.position || 0)}s`
        updateProgress()
    }
}

// ═══════════════════════════════════════════════════════════
// Debug helpers
// ═══════════════════════════════════════════════════════════
async function listPlayers() {
    const token = localStorage.getItem('jarvis_api_key')
    if (!token) { log('No token', 'error'); return }
    try {
        const resp = await fetch('/api/ma-jsonrpc/debug/players', {
            headers: { 'Authorization': `Bearer ${token}` },
        })
        const data = await resp.json()
        debugOutput.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`
        log(`[MAWebPlayer] Players: ${JSON.stringify(data).substring(0, 500)}`, 'info')
    } catch (err) {
        debugOutput.innerHTML = `<pre style="color: #f87171;">Error: ${(err as Error).message}</pre>`
        log(`[MAWebPlayer] List players failed: ${(err as Error).message}`, 'error')
    }
}

async function listQueues() {
    const token = localStorage.getItem('jarvis_api_key')
    if (!token) { log('No token', 'error'); return }
    try {
        const resp = await fetch('/api/ma-jsonrpc/debug/queues', {
            headers: { 'Authorization': `Bearer ${token}` },
        })
        const data = await resp.json()
        debugOutput.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`
        log(`[MAWebPlayer] Queues: ${JSON.stringify(data).substring(0, 500)}`, 'info')
    } catch (err) {
        debugOutput.innerHTML = `<pre style="color: #f87171;">Error: ${(err as Error).message}</pre>`
        log(`[MAWebPlayer] List queues failed: ${(err as Error).message}`, 'error')
    }
}

async function testPlayerQuery() {
    const token = localStorage.getItem('jarvis_api_key')
    if (!token) { log('No token', 'error'); return }
    try {
        const resp = await fetch(`/api/ma-jsonrpc/debug/player/${encodeURIComponent(playerId)}`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
        const data = await resp.json()
        debugOutput.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`
        log(`[MAWebPlayer] Player query: ${JSON.stringify(data).substring(0, 500)}`, 'info')
    } catch (err) {
        debugOutput.innerHTML = `<pre style="color: #f87171;">Error: ${(err as Error).message}</pre>`
        log(`[MAWebPlayer] Player query failed: ${(err as Error).message}`, 'error')
    }
}

async function registerPlayer() {
    log(`[MAWebPlayer] registerPlayer: sending client/hello via sendspin...`, 'info')
    if (!sendspinWs || sendspinWs.readyState !== WebSocket.OPEN) {
        log(`[MAWebPlayer] Sendspin not connected`, 'error')
        return
    }
    const hello = {
        type: "client/hello",
        payload: {
            client_id: playerId,
            name: "Test Browser Player",
            version: 1,
            supported_roles: ["player@v1", "controller@v1", "metadata@v1"],
            device_info: { product_name: "Test Browser", manufacturer: "Test", software_version: "1.0" },
            "player@v1_support": {
                supported_formats: [{ codec: "opus", sample_rate: 48000, channels: 2, bit_depth: 16 }],
                buffer_capacity: 5242880,
                supported_commands: ["volume", "mute"],
            },
        },
    }
    sendspinWs.send(JSON.stringify(hello))
    log(`[MAWebPlayer] Sent client/hello: ${JSON.stringify(hello).substring(0, 200)}`, 'info')
}

// ═══════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════
function init() {
    const saved = localStorage.getItem('jarvis_api_key')
    const authStatus = document.getElementById('auth-status')
    if (saved) {
        apiTokenInput.value = saved
        if (authStatus) authStatus.style.display = 'none'
        log(`[MAWebPlayer] Token auto-detected from localStorage`, 'success')
    } else {
        log(`[MAWebPlayer] No token — login to UI first`, 'warn')
    }

    // Event listeners
    btnConnect.addEventListener('click', connect)
    btnDisconnect.addEventListener('click', disconnect)
    btnReconnect.addEventListener('click', reconnect)
    btnClearLog.addEventListener('click', () => { logEl.innerHTML = '' })

    btnPlay.addEventListener('click', async () => {
        await cmdPlay()
    })

    btnPause.addEventListener('click', async () => {
        await cmdPause()
    })

    btnPlayUri.addEventListener('click', async () => {
        const uri = uriInput.value.trim()
        if (!uri) { log('No URI provided', 'error'); return }
        await playMedia(uri)
    })

    volumeSlider.addEventListener('input', (e) => {
        volumeValue.textContent = e.target.value
        setVolumeVolume(parseInt(e.target.value))
    })
    volumeSlider.addEventListener('change', (e) => {
        setVolumeVolume(parseInt(e.target.value))
    })

    btnSearch.addEventListener('click', () => {
        const q = searchInput.value.trim()
        if (!q) { log('Enter search query', 'warn'); return }
        log(`[MAWebPlayer] Search button clicked, query: "${q}"`, 'info')
        searchMedia(q)
    })

    // Debug buttons
    btnListPlayers.addEventListener('click', listPlayers)
    btnListQueues.addEventListener('click', listQueues)
    btnTestPlayerQuery.addEventListener('click', testPlayerQuery)
    btnRegisterPlayer.addEventListener('click', registerPlayer)

    // Enter key on search input
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault()
            log('[MAWebPlayer] Enter key pressed on search input', 'info')
            btnSearch.click()
        }
    })

    // Enter key on URI input
    uriInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') btnPlayUri.click()
    })

    // Log page load
    log(`MA Web Player Test loaded`, 'info')
    log(`Server: ${window.location.origin}`, 'info')
    log(`Player ID: ${getPlayerId()}`, 'info')
    log(`Architecture: uses @sendspin/sendspin-js`, 'info')
    log(`Flow: sendspin (audio transport) + JSON-RPC (control)`, 'info')
}

init()
