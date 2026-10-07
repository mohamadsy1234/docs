package agent.bridge.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** A WhatsApp notification as the Android layer sees it (mvp-1-spec 3, stage 1). */
data class PostedNotification<H>(
    val sbnKey: String,
    /** Timestamp of the newest message in the notification. */
    val messageTime: Long,
    val senderName: String,
    /** Text of the newest message; null for media. */
    val text: String?,
    val isGroup: Boolean,
    /** The reply action's PendingIntent + RemoteInput; null if the notification has none. */
    val replyHandle: H?,
)

/** The shadow notification surface (mvp-1-spec 3, stage 3). */
interface ShadowUi {
    fun show(token: String, senderName: String, proposedText: String)
    fun cancel(token: String)
    /** The user acted on a proposal whose token no longer exists. */
    fun showExpired()
    fun showSendFailed(senderName: String)
    /** The agent failed its proof repeatedly (mvp-1-spec 2.2). */
    fun showUntrustedServer()
}

/** Fires the WhatsApp reply action with [text]; false if the handle is dead. */
fun interface ReplySender<H> {
    fun send(handle: H, text: String): Boolean
}

/** Append-only audit log; Bridge's copy is the authoritative one (mvp-1-spec 4). */
fun interface AuditLog {
    fun record(event: String, fields: Map<String, Any?>)
}

/**
 * Everything Bridge decides, independent of Android. The Android layer feeds
 * it notification events and button taps and implements the interfaces above.
 */
class BridgeCore<H>(
    private val transport: Transport,
    private val ui: ShadowUi,
    private val sender: ReplySender<H>,
    private val audit: AuditLog,
    private val hasConsent: () -> Boolean,
    private val store: ReplyTokenStore<H> = ReplyTokenStore(),
    private val dedupeCapacity: Int = 1000,
) : BridgeClientListener {

    private val sentEvents = object : LinkedHashMap<String, Unit>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Unit>?) = size > dedupeCapacity
    }

    /** Stage 1. */
    fun onNotificationPosted(n: PostedNotification<H>) {
        if (!hasConsent()) return // nothing leaves the device before consent (mvp-1-spec 5)
        if (n.isGroup || n.replyHandle == null || n.text.isNullOrBlank()) return

        val eventId = "${n.sbnKey}|${n.messageTime}"
        synchronized(sentEvents) { if (eventId in sentEvents) return }

        val reg = store.register(n.sbnKey, n.senderName, n.replyHandle)
        reg.superseded.forEach { ui.cancel(it.token) }

        val sent = transport.send(buildJsonObject {
            put("type", "incoming")
            put("event_id", eventId)
            put("reply_token", reg.token)
            put("sender_name", n.senderName)
            put("text", n.text)
            put("posted_at", n.messageTime)
        })
        if (sent) {
            // Only a delivered event counts as seen, so a re-post after a reconnect is retried.
            synchronized(sentEvents) { sentEvents[eventId] = Unit }
            audit.record("forwarded", mapOf("token" to reg.token, "sender" to n.senderName))
        } else {
            store.take(reg.token)
            audit.record("dropped_offline", mapOf("sbn_key" to n.sbnKey))
        }
    }

    /** Messages from the agent (stage 3). */
    override fun onMessage(message: JsonObject) {
        when (message.string("type")) {
            "propose_reply" -> {
                val token = message.string("reply_token")
                val text = message.string("proposed_text")
                if (token == null || text.isNullOrBlank()) {
                    audit.record("bad_proposal", emptyMap())
                    return
                }
                // Unknown or already-answered token: ignore. Bridge is the source of truth.
                val entry = store.attachProposal(token, text)
                if (entry == null) {
                    audit.record("proposal_ignored_unknown_token", mapOf("token" to token))
                    return
                }
                // Title comes from Bridge's own record, never from the agent.
                ui.show(token, entry.senderName, text)
            }
            else -> audit.record("unknown_message", mapOf("type" to message.string("type")))
        }
    }

    /** Stage 4: the user tapped [Send now] (the system already required an unlock). */
    fun onUserSend(token: String) {
        ui.cancel(token) // the shadow notification is done whatever happens next
        val entry = store.take(token)
        val text = entry?.proposedText
        if (entry == null || text == null) {
            ui.showExpired()
            audit.record("send_expired", mapOf("token" to token))
            return
        }
        // Exactly the text the user was shown, from Bridge's record.
        if (sender.send(entry.handle, text)) {
            audit.record("sent", mapOf("token" to token, "sender" to entry.senderName, "text" to text))
            transport.send(buildJsonObject {
                put("type", "notification_sent"); put("reply_token", token)
                put("sent_text", text); put("sent_at", System.currentTimeMillis())
            })
        } else {
            audit.record("send_failed", mapOf("token" to token, "sender" to entry.senderName))
            ui.showSendFailed(entry.senderName)
        }
    }

    /** Stage 4: the user tapped [Ignore]. */
    fun onUserIgnore(token: String) {
        val entry = store.take(token) ?: return
        ui.cancel(token)
        audit.record("ignored", mapOf("token" to token, "sender" to entry.senderName))
        transport.send(buildJsonObject { put("type", "proposal_ignored"); put("reply_token", token) })
    }

    /** The original WhatsApp notification is gone (user read the chat). */
    fun onNotificationRemoved(sbnKey: String) {
        store.expire(sbnKey).forEach { entry ->
            ui.cancel(entry.token)
            audit.record("expired", mapOf("token" to entry.token, "sender" to entry.senderName))
            transport.send(buildJsonObject { put("type", "notification_expired"); put("reply_token", entry.token) })
        }
    }

    /** Periodic cleanup of tokens that never got a proposal. */
    fun sweep() {
        store.sweep().forEach { audit.record("token_timeout", mapOf("token" to it.token)) }
    }

    override fun onSessionOpened() = audit.record("session_opened", emptyMap())

    override fun onSessionClosed(reason: String) = audit.record("session_closed", mapOf("reason" to reason))

    override fun onUntrustedServer(consecutiveFailures: Int) {
        audit.record("handshake_failed", mapOf("consecutive" to consecutiveFailures))
        ui.showUntrustedServer()
    }

    fun pendingTokens(): Int = store.size()
}
