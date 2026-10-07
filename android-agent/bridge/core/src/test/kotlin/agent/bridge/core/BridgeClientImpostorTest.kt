package agent.bridge.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** mvp-1-spec 6: an impostor on the port gets neither the secret nor any content. */
class BridgeClientImpostorTest {
    private val server = MockWebServer()
    private val secret = ByteArray(32) { 7 }

    @AfterTest
    fun tearDown() = server.shutdown()

    @Test
    fun `impostor receives only hello frames and triggers the untrusted warning`() {
        val recorded = Collections.synchronizedList(mutableListOf<String>())
        repeat(3) {
            server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    recorded += text
                    // Answer with a challenge it cannot prove.
                    webSocket.send(buildJsonObject {
                        put("type", "challenge"); put("v", 1)
                        put("nonce_s", "11".repeat(16)); put("proof_s", "22".repeat(32))
                    }.toString())
                }
                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(code, null)
                }
            }))
        }
        server.start()

        val warned = CountDownLatch(1)
        var sendAccepted = true
        val listener = object : BridgeClientListener {
            override fun onMessage(message: JsonObject) {}
            override fun onUntrustedServer(consecutiveFailures: Int) { warned.countDown() }
        }
        val client = BridgeClient("ws://127.0.0.1:${server.port}", secret, listener,
            backoff = Backoff(initialMs = 20, maxMs = 50))
        client.start()
        // Notification content offered while unauthenticated must be refused.
        sendAccepted = client.send(buildJsonObject { put("type", "incoming"); put("text", "private") })
        assertTrue(warned.await(10, TimeUnit.SECONDS), "no untrusted-server warning after 3 failed proofs")
        client.stop()

        assertFalse(sendAccepted)
        assertEquals(3, recorded.size)
        recorded.forEach { assertTrue(it.contains("\"type\":\"hello\""), it) }
        val all = recorded.joinToString()
        assertFalse(all.contains(secret.toHex()))
        assertFalse(all.contains("private"))
    }

    @Test
    fun `silent server is dropped after the handshake timeout`() {
        val closed = CountDownLatch(1)
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (code == 1008) closed.countDown()
                webSocket.close(code, null)
            }
            override fun onOpen(webSocket: WebSocket, response: Response) {}
        }))
        server.start()
        val client = BridgeClient("ws://127.0.0.1:${server.port}", secret,
            object : BridgeClientListener { override fun onMessage(message: JsonObject) {} },
            handshakeTimeoutMs = 300, backoff = Backoff(initialMs = 10_000))
        client.start()
        assertTrue(closed.await(5, TimeUnit.SECONDS))
        client.stop()
    }
}
