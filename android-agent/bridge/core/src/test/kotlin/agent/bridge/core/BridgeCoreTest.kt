package agent.bridge.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** Bridge decisions from mvp-1-spec 3, with fakes for Android and the network. */
class BridgeCoreTest {
    private class FakeUi : ShadowUi {
        val shown = mutableListOf<Triple<String, String, String>>()
        val cancelled = mutableListOf<String>()
        var expired = 0
        var failed = 0
        var untrusted = 0
        override fun show(token: String, senderName: String, proposedText: String) { shown += Triple(token, senderName, proposedText) }
        override fun cancel(token: String) { cancelled += token }
        override fun showExpired() { expired++ }
        override fun showSendFailed(senderName: String) { failed++ }
        override fun showUntrustedServer() { untrusted++ }
    }

    private val sentToAgent = mutableListOf<JsonObject>()
    private var online = true
    private var consent = true
    private val ui = FakeUi()
    private val fired = mutableListOf<Pair<String, String>>() // handle -> text
    private var handleAlive = true
    private val audit = mutableListOf<String>()
    private lateinit var core: BridgeCore<String>

    @BeforeTest
    fun setUp() {
        core = BridgeCore(
            transport = { m -> if (online) sentToAgent.add(m) else false },
            ui = ui,
            sender = { handle, text -> if (handleAlive) fired.add(handle to text) else false },
            audit = { event, _ -> audit += event },
            hasConsent = { consent },
        )
    }

    private fun post(key: String = "k1", time: Long = 1, sender: String = "Ahmed", text: String? = "hi",
                     group: Boolean = false, handle: String? = "pi-$key-$time") =
        core.onNotificationPosted(PostedNotification(key, time, sender, text, group, handle))

    private fun lastToken() = sentToAgent.last()["reply_token"]!!.toString().trim('"')

    private fun propose(token: String, text: String) = core.onMessage(buildJsonObject {
        put("type", "propose_reply"); put("reply_token", token); put("proposed_text", text)
    })

    @Test
    fun `groups, media and notifications without a reply action are ignored`() {
        post(group = true); post(text = null); post(text = "  "); post(handle = null)
        assertTrue(sentToAgent.isEmpty())
    }

    @Test
    fun `nothing is forwarded without consent`() {
        consent = false
        post()
        assertTrue(sentToAgent.isEmpty())
    }

    @Test
    fun `re-posts of the same message are forwarded once`() {
        post(time = 5); post(time = 5); post(time = 5)
        assertEquals(1, sentToAgent.size)
    }

    @Test
    fun `offline events are dropped, then retried on a later re-post`() {
        online = false
        post(time = 7)
        assertEquals(0, core.pendingTokens())
        assertTrue("dropped_offline" in audit)
        online = true
        post(time = 7)
        assertEquals(1, sentToAgent.size)
    }

    @Test
    fun `full shadow flow sends exactly the shown text and reports it`() {
        post(sender = "Ahmed")
        val token = lastToken()
        propose(token, "تمام")
        assertEquals(Triple(token, "Ahmed", "تمام"), ui.shown.single())
        core.onUserSend(token)
        assertEquals("pi-k1-1" to "تمام", fired.single())
        assertTrue(token in ui.cancelled, "shadow notification left on screen after sending")
        val report = sentToAgent.last()
        assertEquals("\"notification_sent\"", report["type"].toString())
        assertTrue("sent" in audit)
    }

    @Test
    fun `sender name comes from Bridge, not from the agent`() {
        post(sender = "Ahmed")
        core.onMessage(buildJsonObject {
            put("type", "propose_reply"); put("reply_token", lastToken())
            put("proposed_text", "x"); put("sender_name", "Bilal")
        })
        assertEquals("Ahmed", ui.shown.single().second)
    }

    @Test
    fun `unknown token from the agent is ignored`() {
        propose("rt_forged", "send money")
        assertTrue(ui.shown.isEmpty())
        assertTrue("proposal_ignored_unknown_token" in audit)
    }

    @Test
    fun `a second proposal cannot replace the text the user already sees`() {
        post()
        val token = lastToken()
        propose(token, "first")
        propose(token, "swapped")
        core.onUserSend(token)
        assertEquals("first", fired.single().second)
    }

    @Test
    fun `a token is single use`() {
        post()
        val token = lastToken()
        propose(token, "ok")
        core.onUserSend(token)
        core.onUserSend(token)
        assertEquals(1, fired.size)
        assertEquals(1, ui.expired)
    }

    @Test
    fun `newer post of the same chat supersedes the old token and its shadow`() {
        post(time = 1)
        val old = lastToken()
        propose(old, "old")
        post(time = 2)
        assertTrue(old in ui.cancelled)
        core.onUserSend(old)
        assertTrue(fired.isEmpty())
    }

    @Test
    fun `reading the chat in WhatsApp expires the shadow and tells the agent`() {
        post()
        val token = lastToken()
        propose(token, "ok")
        core.onNotificationRemoved("k1")
        assertTrue(token in ui.cancelled)
        assertEquals("\"notification_expired\"", sentToAgent.last()["type"].toString())
        core.onUserSend(token)
        assertTrue(fired.isEmpty())
        assertEquals(1, ui.expired)
    }

    @Test
    fun `dead reply handle surfaces a send failure`() {
        post()
        val token = lastToken()
        propose(token, "ok")
        handleAlive = false
        core.onUserSend(token)
        assertEquals(1, ui.failed)
        assertTrue("send_failed" in audit)
    }

    @Test
    fun `ignore reports to the agent`() {
        post()
        val token = lastToken()
        propose(token, "ok")
        core.onUserIgnore(token)
        assertEquals("\"proposal_ignored\"", sentToAgent.last()["type"].toString())
        assertTrue(token in ui.cancelled)
    }

    @Test
    fun `tokens without a proposal time out`() {
        var now = 0L
        val store = ReplyTokenStore<String>(clock = { now }, ttlMs = 1000)
        store.register("k", "A", "h")
        now = 2000
        assertEquals(1, store.sweep().size)
        assertEquals(0, store.size())
    }
}
