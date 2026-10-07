package agent.bridge.core

import java.security.SecureRandom

/**
 * Bridge-side source of truth for reply tokens (mvp-1-spec 3).
 *
 * A token maps to the WhatsApp notification it answers: the platform reply
 * handle [H] (a PendingIntent + RemoteInput on Android, never sent to Python),
 * the sender name shown to the user, and the one proposal the user may send.
 */
class ReplyTokenStore<H>(
    private val clock: () -> Long = System::currentTimeMillis,
    private val ttlMs: Long = 24 * 60 * 60 * 1000L,
) {
    data class Entry<H>(
        val token: String,
        val sbnKey: String,
        val senderName: String,
        val handle: H,
        val createdAt: Long,
        val proposedText: String? = null,
    )

    data class Registration<H>(val token: String, val superseded: List<Entry<H>>)

    private val random = SecureRandom()
    private val entries = LinkedHashMap<String, Entry<H>>()

    @Synchronized
    fun register(sbnKey: String, senderName: String, handle: H): Registration<H> {
        // The newest post of a notification carries the only valid reply handle.
        val superseded = entries.values.filter { it.sbnKey == sbnKey }
        superseded.forEach { entries.remove(it.token) }
        val token = "rt_" + ByteArray(16).also { random.nextBytes(it) }.toHex()
        entries[token] = Entry(token, sbnKey, senderName, handle, clock())
        return Registration(token, superseded)
    }

    /**
     * Records the proposal for [token]. Returns null when the token is unknown,
     * or when it already has a proposal: the text the user is shown must never
     * change underneath them.
     */
    @Synchronized
    fun attachProposal(token: String, text: String): Entry<H>? {
        val entry = entries[token] ?: return null
        if (entry.proposedText != null) return null
        return entry.copy(proposedText = text).also { entries[token] = it }
    }

    @Synchronized
    fun get(token: String): Entry<H>? = entries[token]

    /** Removes and returns the entry; a token can be acted on at most once. */
    @Synchronized
    fun take(token: String): Entry<H>? = entries.remove(token)

    @Synchronized
    fun expire(sbnKey: String): List<Entry<H>> {
        val gone = entries.values.filter { it.sbnKey == sbnKey }
        gone.forEach { entries.remove(it.token) }
        return gone
    }

    /** Drops tokens that never received a proposal within the TTL. */
    @Synchronized
    fun sweep(): List<Entry<H>> {
        val cutoff = clock() - ttlMs
        val old = entries.values.filter { it.proposedText == null && it.createdAt < cutoff }
        old.forEach { entries.remove(it.token) }
        return old
    }

    @Synchronized
    fun size(): Int = entries.size
}
