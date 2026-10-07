package agent.bridge.core

import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

interface BridgeClientListener {
    fun onSessionOpened() {}
    fun onSessionClosed(reason: String) {}
    fun onMessage(message: JsonObject)
    /** The server failed its proof this many times in a row (mvp-1-spec 2.2: warn the user at 3). */
    fun onUntrustedServer(consecutiveFailures: Int) {}
}

/** Sends a message on the authenticated session; false when there is none. */
fun interface Transport {
    fun send(message: JsonObject): Boolean
}

/** Exponential reconnect delay, 1s doubling to 60s, reset after a successful handshake. */
class Backoff(private val initialMs: Long = 1_000, private val maxMs: Long = 60_000) {
    private var next = initialMs
    @Synchronized fun nextDelayMs(): Long = next.also { next = minOf(next * 2, maxMs) }
    @Synchronized fun reset() { next = initialMs }
}

/**
 * WebSocket client for the agent (mvp-1-spec 2). The server must prove it
 * knows the secret before this client sends anything beyond `hello`; until
 * then [send] refuses every message, so notification content can never reach
 * an impostor holding the port.
 */
class BridgeClient(
    private val url: String,
    private val secret: ByteArray,
    private val listener: BridgeClientListener,
    private val http: OkHttpClient = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build(),
    private val scheduler: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor(),
    private val backoff: Backoff = Backoff(),
    private val handshakeTimeoutMs: Long = 5_000,
) : Transport {

    private enum class State { STOPPED, HANDSHAKING, AUTHENTICATED }

    private var state = State.STOPPED
    private var socket: WebSocket? = null
    private var nonceB: ByteArray? = null
    private var channel: Channel? = null
    private var proofFailures = 0
    private var pending: ScheduledFuture<*>? = null
    private var running = false

    @Synchronized
    fun start() {
        if (running) return
        running = true
        connect()
    }

    @Synchronized
    fun stop() {
        running = false
        pending?.cancel(false)
        socket?.close(1000, "bridge stopping")
        reset()
    }

    @Synchronized
    fun isAuthenticated(): Boolean = state == State.AUTHENTICATED

    @Synchronized
    override fun send(message: JsonObject): Boolean {
        val ch = channel
        val ws = socket
        if (state != State.AUTHENTICATED || ch == null || ws == null) return false
        return ws.send(ch.seal(message))
    }

    private fun connect() {
        state = State.HANDSHAKING
        val request = Request.Builder().url(url).build()
        socket = http.newWebSocket(request, Listener())
    }

    private fun reset() {
        state = State.STOPPED
        socket = null
        nonceB = null
        channel = null
    }

    private fun scheduleReconnect() {
        if (!running) return
        val delay = backoff.nextDelayMs()
        pending = scheduler.schedule({ synchronized(this) { if (running && socket == null) connect() } },
            delay, TimeUnit.MILLISECONDS)
    }

    private fun fail(ws: WebSocket, reason: String) {
        ws.close(1008, reason.take(120))
        // A hostile or broken peer may never answer the close frame; OkHttp would
        // then wait 60s before giving up. Cut the connection after a second.
        scheduler.schedule({ ws.cancel() }, 1, TimeUnit.SECONDS)
    }

    private inner class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            synchronized(this@BridgeClient) {
                if (webSocket !== socket) return
                val nonce = Protocol.newNonce()
                nonceB = nonce
                webSocket.send(Protocol.helloFrame(nonce))
                pending = scheduler.schedule({
                    synchronized(this@BridgeClient) {
                        if (webSocket === socket && state == State.HANDSHAKING) fail(webSocket, "handshake timeout")
                    }
                }, handshakeTimeoutMs, TimeUnit.MILLISECONDS)
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            var message: JsonObject? = null
            var opened = false
            var untrusted = 0
            synchronized(this@BridgeClient) {
                if (webSocket !== socket) return
                try {
                    when (state) {
                        State.HANDSHAKING -> {
                            val nb = nonceB ?: throw ProtocolException("challenge before hello")
                            val nonceS = try {
                                Protocol.readChallenge(text, secret, nb)
                            } catch (e: ProtocolException) {
                                proofFailures += 1
                                if (proofFailures >= 3) untrusted = proofFailures
                                throw e
                            }
                            webSocket.send(Protocol.authFrame(secret, nb, nonceS))
                            channel = Protocol.clientChannel(Protocol.sessionKey(secret, nb, nonceS))
                            state = State.AUTHENTICATED
                            proofFailures = 0
                            pending?.cancel(false)
                            backoff.reset()
                            opened = true
                        }
                        State.AUTHENTICATED -> message = channel!!.open(text)
                        State.STOPPED -> return
                    }
                } catch (e: ProtocolException) {
                    fail(webSocket, e.message ?: "protocol violation")
                }
            }
            // Callbacks run outside the lock so listeners may call send().
            if (untrusted > 0) listener.onUntrustedServer(untrusted)
            if (opened) listener.onSessionOpened()
            message?.let(listener::onMessage)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            synchronized(this@BridgeClient) {
                if (webSocket === socket) fail(webSocket, "binary frames are not allowed")
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = ended(webSocket, "closed $code $reason")

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
            ended(webSocket, "failure ${t.javaClass.simpleName}: ${t.message}")

        private fun ended(webSocket: WebSocket, reason: String) {
            val wasAuthenticated: Boolean
            synchronized(this@BridgeClient) {
                if (webSocket !== socket) return
                wasAuthenticated = state == State.AUTHENTICATED
                pending?.cancel(false)
                reset()
                scheduleReconnect()
            }
            if (wasAuthenticated) listener.onSessionClosed(reason)
        }
    }
}
