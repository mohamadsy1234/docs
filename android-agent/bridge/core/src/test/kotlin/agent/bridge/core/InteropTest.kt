package agent.bridge.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assumptions.assumeTrue
import java.io.File
import java.net.ServerSocket
import java.nio.file.Files
import java.security.SecureRandom
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Kotlin Bridge against the real Python agent (agent/agent.py), process to
 * process: the cross-language proof of mvp-1-spec 2.2.1.
 */
class InteropTest {
    private val agentDir = File(System.getProperty("agent.dir") ?: "../../agent")
    private lateinit var home: File
    private lateinit var agent: Process
    private var port = 0
    private val secret = ByteArray(32).also { SecureRandom().nextBytes(it) }

    @BeforeTest
    fun startAgent() {
        val probe = ProcessBuilder("python3", "-c", "import websockets").start()
        assumeTrue(probe.waitFor() == 0, "python3 with websockets is required for interop tests")

        home = Files.createTempDirectory("agent-home").toFile()
        val config = File(home, ".config/agent").apply { mkdirs() }
        port = ServerSocket(0).use { it.localPort }
        File(config, "config.toml").writeText("port = $port\n")
        File(config, ".env").apply {
            writeText("API_SECRET=${secret.toHex()}\n")
            setReadable(false, false); setWritable(false, false)
            setReadable(true, true); setWritable(true, true)
        }
        agent = ProcessBuilder("python3", File(agentDir, "agent.py").path)
            .apply { environment()["HOME"] = home.path }
            .redirectOutput(ProcessBuilder.Redirect.DISCARD)
            .redirectError(ProcessBuilder.Redirect.DISCARD)
            .start()
        waitForEvent("listening")
    }

    @AfterTest
    fun stopAgent() {
        if (::agent.isInitialized) {
            agent.destroy() // SIGTERM
            agent.waitFor(10, TimeUnit.SECONDS)
        }
        if (::home.isInitialized) home.deleteRecursively()
    }

    private fun journal(): List<JsonObject> {
        val f = File(home, ".local/state/agent/journal.jsonl")
        if (!f.exists()) return emptyList()
        return f.readLines().map { Json.parseToJsonElement(it).jsonObject }
    }

    private fun events() = journal().map { it["event"]!!.jsonPrimitive.content }

    private fun waitForEvent(event: String, timeoutMs: Long = 8000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (event in events()) return
            Thread.sleep(50)
        }
        throw AssertionError("$event not in journal: ${events()}")
    }

    private class RecordingUi : ShadowUi {
        val shown = LinkedBlockingQueue<Triple<String, String, String>>()
        override fun show(token: String, senderName: String, proposedText: String) { shown += Triple(token, senderName, proposedText) }
        override fun cancel(token: String) {}
        override fun showExpired() {}
        override fun showSendFailed(senderName: String) {}
        override fun showUntrustedServer() {}
    }

    @Test
    fun `full shadow round trip against the Python agent`() {
        val ui = RecordingUi()
        val fired = LinkedBlockingQueue<Pair<String, String>>()
        val opened = CountDownLatch(1)
        lateinit var client: BridgeClient
        val core = BridgeCore<String>(
            transport = { m -> client.send(m) },
            ui = ui,
            sender = { handle, text -> fired.add(handle to text) },
            audit = { _, _ -> },
            hasConsent = { true },
        )
        val listener = object : BridgeClientListener by core {
            override fun onSessionOpened() { core.onSessionOpened(); opened.countDown() }
        }
        client = BridgeClient("ws://127.0.0.1:$port", secret, listener)
        client.start()
        assertTrue(opened.await(10, TimeUnit.SECONDS), "handshake with the Python agent failed")

        core.onNotificationPosted(PostedNotification("0|com.whatsapp|1|chat|10", 1000L, "Ahmed",
            "مرحبا، هل أنت متاح؟", isGroup = false, replyHandle = "pending-intent-1"))
        val (token, sender, text) = ui.shown.poll(10, TimeUnit.SECONDS) ?: throw AssertionError("no proposal")
        assertEquals("Ahmed", sender)
        assertTrue(text.isNotBlank())

        core.onUserSend(token)
        assertEquals("pending-intent-1" to text, fired.poll(5, TimeUnit.SECONDS))
        waitForEvent("notification_sent")
        client.stop()

        // The agent's view of the same exchange.
        val names = events()
        assertTrue(listOf("session_opened", "incoming", "proposal_sent", "notification_sent").all { it in names }, "$names")
        assertFalse("protocol_violation" in names)
        assertFalse("handshake_failed" in names)
    }

    @Test
    fun `wrong secret never opens a session`() {
        val warned = CountDownLatch(1)
        val wrong = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val client = BridgeClient("ws://127.0.0.1:$port", wrong, object : BridgeClientListener {
            override fun onMessage(message: JsonObject) {}
            override fun onUntrustedServer(consecutiveFailures: Int) { warned.countDown() }
        }, backoff = Backoff(initialMs = 20, maxMs = 50))
        client.start()
        assertTrue(warned.await(10, TimeUnit.SECONDS))
        client.stop()
        assertFalse("session_opened" in events())
        assertFalse("incoming" in events())
    }
}
